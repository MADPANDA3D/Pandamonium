// First-party, same-document Voice Orb. No iframe, remote assets, or generic UI commands.

import sessionModule from './sessions.js';
import { collectClientState, handleUIControl } from './chatStream.js';
import { showError, showToast } from './ui.js';
import { trackWorkerTask } from './voiceOrbWorkers.js';

const $ = id => document.getElementById(id);
const UI_CONTROL_ALLOWLIST = new Set([
  'open_view:calendar',
  'close_view:document',
  'minimize_view:document',
]);
const STATE_COPY = {
  idle: 'Ready',
  listening: 'Listening',
  transcribing: 'Transcribing',
  thinking: 'Thinking',
  speaking: 'Speaking',
  interrupted: 'Interrupted',
  failed: 'Needs attention',
};

let active = false;
let callGeneration = 0;
let voiceSessionId = '';
let voiceConfig = null;
let currentState = 'idle';
let recorder = null;
let microphone = null;
let audioChunks = [];
let recognition = null;
let browserTranscript = '';
let captureAudioContext = null;
let captureAnalyser = null;
let silenceFrame = 0;
let responseController = null;
let playbackAudio = null;
let cueContext = null;
let orbEnergy = 0;

export function voiceUIControlAllowed(event) {
  if (!event || typeof event !== 'object') return false;
  return UI_CONTROL_ALLOWLIST.has(`${event.ui_event || ''}:${event.view || ''}`);
}

function panel() {
  return $('voice-orb-panel');
}

function setState(next, detail = '') {
  currentState = next;
  const root = panel();
  if (root) root.dataset.state = next;
  const status = $('voice-orb-status');
  const detailNode = $('voice-orb-detail');
  const talk = $('voice-orb-talk');
  if (status) status.textContent = STATE_COPY[next] || next;
  if (detailNode) detailNode.textContent = detail || {
    idle: `${voiceConfig?.assistant || 'Odysseus'} is ready.`,
    listening: 'Speak naturally. Tap the orb when you are done.',
    transcribing: 'Turning speech into text.',
    thinking: 'Preparing a response.',
    speaking: 'Tap the orb to interrupt.',
    interrupted: 'Response interrupted.',
    failed: 'Check voice settings and try again.',
  }[next] || '';
  if (talk) {
    const interruptible = next === 'speaking' || next === 'thinking';
    talk.title = interruptible ? 'Interrupt and speak' : (next === 'listening' ? 'Finish speaking' : 'Speak');
    talk.setAttribute('aria-label', talk.title);
  }
}

function setTranscript(text) {
  const node = $('voice-orb-transcript');
  if (node) node.textContent = text || '';
}

function setReply(text) {
  const node = $('voice-orb-reply');
  if (node) node.textContent = text || '';
}

function getCueContext() {
  const AudioContext = window.AudioContext || window.webkitAudioContext;
  if (!AudioContext) return null;
  if (!cueContext || cueContext.state === 'closed') cueContext = new AudioContext();
  cueContext.resume?.().catch(() => {});
  return cueContext;
}

function playCue(kind) {
  try {
    const context = getCueContext();
    if (!context) return;
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    const notes = { open: 440, listen: 620, done: 520, error: 180, interrupt: 300 };
    oscillator.frequency.setValueAtTime(notes[kind] || 440, context.currentTime);
    oscillator.type = kind === 'error' ? 'sawtooth' : 'sine';
    gain.gain.setValueAtTime(0.0001, context.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.06, context.currentTime + 0.015);
    gain.gain.exponentialRampToValueAtTime(0.0001, context.currentTime + 0.16);
    oscillator.connect(gain).connect(context.destination);
    oscillator.start();
    oscillator.stop(context.currentTime + 0.18);
  } catch {}
}

function drawOrb() {
  const canvas = $('voice-orb-canvas');
  if (!canvas) return;
  const rect = canvas.getBoundingClientRect();
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  const width = Math.max(1, Math.round(rect.width * ratio));
  const height = Math.max(1, Math.round(rect.height * ratio));
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  const context = canvas.getContext('2d');
  if (!context) return;
  const now = performance.now() / 1000;
  const cx = width / 2;
  const cy = height / 2;
  const base = Math.min(width, height) * 0.25;
  const stateBoost = currentState === 'listening' ? 0.16 : currentState === 'speaking' ? 0.12 : currentState === 'thinking' ? 0.08 : 0.03;
  const pulse = 1 + stateBoost * (0.5 + 0.5 * Math.sin(now * (currentState === 'thinking' ? 4.5 : 2.6))) + orbEnergy * 0.18;
  const colors = currentState === 'failed'
    ? ['#ff6b6b', '#801f3a']
    : currentState === 'listening'
      ? ['#9cf6ff', '#2674ff']
      : ['#d9fbff', '#665cff'];

  context.clearRect(0, 0, width, height);
  const glow = context.createRadialGradient(cx, cy, base * 0.1, cx, cy, base * 2.1);
  glow.addColorStop(0, `${colors[0]}ee`);
  glow.addColorStop(0.45, `${colors[1]}aa`);
  glow.addColorStop(1, 'transparent');
  context.fillStyle = glow;
  context.beginPath();
  context.arc(cx, cy, base * 2.1 * pulse, 0, Math.PI * 2);
  context.fill();

  for (let ring = 0; ring < 3; ring += 1) {
    context.save();
    context.translate(cx, cy);
    context.rotate(now * (ring % 2 ? -0.35 : 0.28) + ring);
    context.strokeStyle = ring === 1 ? `${colors[0]}bb` : `${colors[1]}99`;
    context.lineWidth = Math.max(1.5, 2.2 * ratio);
    context.beginPath();
    context.ellipse(0, 0, base * pulse * (1.08 + ring * 0.22), base * pulse * (0.72 + ring * 0.12), ring * 0.65, 0.2, Math.PI * 1.7);
    context.stroke();
    context.restore();
  }

  const core = context.createRadialGradient(cx - base * 0.3, cy - base * 0.35, 0, cx, cy, base * pulse);
  core.addColorStop(0, '#ffffff');
  core.addColorStop(0.28, colors[0]);
  core.addColorStop(1, colors[1]);
  context.fillStyle = core;
  context.beginPath();
  context.arc(cx, cy, base * pulse, 0, Math.PI * 2);
  context.fill();
  orbEnergy *= 0.92;
  requestAnimationFrame(drawOrb);
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, { credentials: 'same-origin', ...options });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail?.message || data.detail || data.message || 'Voice request failed');
  return data;
}

async function ensureVoiceSession() {
  if (voiceSessionId) return;
  voiceConfig = await fetchJson('/api/voice/status');
  const name = $('voice-orb-name');
  if (name) name.textContent = voiceConfig.assistant || 'Odysseus';
  const linked = sessionModule.getCurrentSessionId?.() || null;
  const created = await fetchJson('/api/voice/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ chat_session_id: linked }),
  });
  voiceSessionId = created.id;
}

function stopPlayback() {
  if (playbackAudio) {
    playbackAudio.pause();
    playbackAudio.currentTime = 0;
    playbackAudio = null;
  }
  window.speechSynthesis?.cancel();
  window.aiTTSManager?.stop?.();
}

async function interruptResponse() {
  responseController?.abort();
  responseController = null;
  stopPlayback();
  if (voiceSessionId) {
    fetch(`/api/voice/sessions/${encodeURIComponent(voiceSessionId)}/interrupt`, {
      method: 'POST',
      credentials: 'same-origin',
    }).catch(() => {});
  }
  setState('interrupted');
  playCue('interrupt');
}

function stopCapture(discard = false) {
  cancelAnimationFrame(silenceFrame);
  silenceFrame = 0;
  if (recognition) {
    try { recognition.stop(); } catch {}
    recognition = null;
  }
  if (recorder?.state === 'recording') {
    recorder._discard = discard;
    recorder.stop();
  }
  microphone?.getTracks().forEach(track => track.stop());
  microphone = null;
  captureAnalyser?.disconnect?.();
  captureAnalyser = null;
  captureAudioContext?.close?.().catch(() => {});
  captureAudioContext = null;
}

function startBrowserRecognition() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) throw new Error('Browser speech recognition is not supported here.');
  browserTranscript = '';
  recognition = new SpeechRecognition();
  recognition.continuous = true;
  recognition.interimResults = false;
  recognition.onresult = event => {
    for (let index = event.resultIndex; index < event.results.length; index += 1) {
      if (event.results[index].isFinal) browserTranscript += `${event.results[index][0].transcript} `;
    }
  };
  recognition.onerror = event => {
    if (event.error !== 'aborted' && event.error !== 'no-speech') console.warn('Voice recognition error:', event.error);
  };
  recognition.start();
}

async function transcribeAudio(blob) {
  if (voiceConfig?.stt?.provider === 'browser') {
    await new Promise(resolve => setTimeout(resolve, 180));
    return browserTranscript.trim();
  }
  const form = new FormData();
  form.append('file', blob, 'voice.webm');
  const response = await fetch('/api/stt/transcribe', { method: 'POST', credentials: 'same-origin', body: form });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail?.message || 'Transcription failed');
  return String(data.text || '').trim();
}

function monitorSilence(stream, generation) {
  const AudioContext = window.AudioContext || window.webkitAudioContext;
  if (!AudioContext) return;
  captureAudioContext = new AudioContext();
  const source = captureAudioContext.createMediaStreamSource(stream);
  captureAnalyser = captureAudioContext.createAnalyser();
  captureAnalyser.fftSize = 512;
  source.connect(captureAnalyser);
  const samples = new Uint8Array(captureAnalyser.fftSize);
  const started = performance.now();
  let heardAt = 0;
  let quietSince = 0;

  const check = () => {
    if (!active || generation !== callGeneration || recorder?.state !== 'recording' || !captureAnalyser) return;
    captureAnalyser.getByteTimeDomainData(samples);
    let sum = 0;
    for (const sample of samples) {
      const value = (sample - 128) / 128;
      sum += value * value;
    }
    const level = Math.sqrt(sum / samples.length);
    orbEnergy = Math.max(orbEnergy, Math.min(1, level * 7));
    const now = performance.now();
    if (level > 0.035) {
      heardAt = now;
      quietSince = 0;
    } else if (heardAt && !quietSince) {
      quietSince = now;
    }
    if ((quietSince && now - quietSince > 1100) || now - started > 30_000) {
      stopCapture(false);
      return;
    }
    silenceFrame = requestAnimationFrame(check);
  };
  silenceFrame = requestAnimationFrame(check);
}

async function startListening() {
  if (!active || recorder?.state === 'recording') return;
  if (!voiceConfig?.stt?.available) {
    try {
      voiceConfig = await fetchJson('/api/voice/status');
    } catch (error) {
      fail(error);
      return;
    }
  }
  if (!voiceConfig?.stt?.available) {
    setState('failed', 'Enable a speech-to-text provider in Settings.');
    return;
  }
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    setState('failed', 'Microphone access needs HTTPS or localhost.');
    return;
  }
  stopPlayback();
  const generation = callGeneration;
  try {
    microphone = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    if (!active || generation !== callGeneration) {
      microphone.getTracks().forEach(track => track.stop());
      microphone = null;
      return;
    }
    audioChunks = [];
    browserTranscript = '';
    if (voiceConfig.stt.provider === 'browser') startBrowserRecognition();
    const mime = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus']
      .find(type => window.MediaRecorder?.isTypeSupported?.(type));
    recorder = mime ? new MediaRecorder(microphone, { mimeType: mime }) : new MediaRecorder(microphone);
    recorder.ondataavailable = event => {
      if (event.data.size) audioChunks.push(event.data);
    };
    recorder.onstop = async () => {
      const stopped = recorder;
      microphone?.getTracks().forEach(track => track.stop());
      microphone = null;
      if (stopped?._discard || !active || generation !== callGeneration) return;
      setState('transcribing');
      try {
        const blob = new Blob(audioChunks, { type: stopped.mimeType || 'audio/webm' });
        const text = await transcribeAudio(blob);
        if (!active || generation !== callGeneration) return;
        if (!text) {
          setState('idle', 'No speech detected. Tap the orb to try again.');
          return;
        }
        await sendTranscript(text, generation);
      } catch (error) {
        fail(error);
      }
    };
    recorder.start();
    setState('listening');
    playCue('listen');
    monitorSilence(microphone, generation);
  } catch (error) {
    fail(error);
  }
}

async function playServerSpeech(text, generation) {
  const response = await fetch('/api/tts/synthesize', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, format: 'audio' }),
  });
  if (!response.ok) throw new Error('Speech playback failed');
  const url = URL.createObjectURL(await response.blob());
  try {
    await new Promise((resolve, reject) => {
      const audio = new Audio(url);
      playbackAudio = audio;
      audio.playbackRate = Number(voiceConfig?.tts?.speed || 1);
      audio.onended = resolve;
      audio.onerror = () => reject(new Error('Speech playback failed'));
      audio.play().catch(reject);
    });
  } finally {
    URL.revokeObjectURL(url);
    if (generation === callGeneration) playbackAudio = null;
  }
}

async function playBrowserSpeech(text) {
  await new Promise((resolve, reject) => {
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.rate = Number(voiceConfig?.tts?.speed || 1);
    const wanted = String(voiceConfig?.tts?.voice || '').toLowerCase();
    if (wanted) {
      utterance.voice = window.speechSynthesis.getVoices().find(voice => voice.name.toLowerCase().includes(wanted)) || null;
    }
    utterance.onend = resolve;
    utterance.onerror = event => reject(new Error(event.error || 'Browser speech failed'));
    window.speechSynthesis.speak(utterance);
  });
}

async function speak(text, generation) {
  if (!active || generation !== callGeneration) return;
  if (!voiceConfig?.tts?.available) {
    setState('idle', 'Speech is disabled; the response is shown as text.');
    return;
  }
  setState('speaking');
  try {
    if (voiceConfig.tts.provider === 'browser') await playBrowserSpeech(text);
    else await playServerSpeech(text, generation);
    if (active && generation === callGeneration) {
      playCue('done');
      await startListening();
    }
  } catch (error) {
    fail(error);
  }
}

async function readVoiceEvents(response, generation) {
  if (!response.ok || !response.body) throw new Error('Voice response failed');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let accumulated = '';
  let finalText = '';
  while (active && generation === callGeneration) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const frames = buffer.split('\n\n');
    buffer = frames.pop() || '';
    for (const frame of frames) {
      const line = frame.split('\n').find(row => row.startsWith('data:'));
      if (!line) continue;
      const event = JSON.parse(line.slice(5).trim());
      if (event.type === 'delta') {
        accumulated += event.text || '';
        setReply(accumulated);
      } else if (event.type === 'ui_control') {
        if (voiceUIControlAllowed(event)) handleUIControl(event);
        else console.warn('Ignored unsupported Voice Orb UI control.');
      } else if (event.type === 'worker_task' && event.task) {
        trackWorkerTask(event.task);
      } else if (event.type === 'final') {
        finalText = String(event.text || accumulated).trim();
        setReply(finalText);
      } else if (event.type === 'error') {
        throw new Error(event.text || 'Voice response failed');
      }
    }
    if (done) break;
  }
  if (!finalText) throw new Error('Voice response ended without a final answer');
  return finalText;
}

async function sendTranscript(text, generation) {
  setTranscript(text);
  setReply('');
  setState('thinking');
  responseController = new AbortController();
  try {
    const response = await fetch(`/api/voice/sessions/${encodeURIComponent(voiceSessionId)}/respond`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, client_state: collectClientState() }),
      signal: responseController.signal,
    });
    const reply = await readVoiceEvents(response, generation);
    responseController = null;
    await speak(reply, generation);
  } catch (error) {
    responseController = null;
    if (error.name !== 'AbortError') fail(error);
  }
}

function fail(error) {
  console.warn('Voice Orb error:', error?.message || String(error));
  stopCapture(true);
  stopPlayback();
  setState('failed', error?.message || 'Voice mode is unavailable.');
  playCue('error');
  showError(error?.message || 'Voice mode is unavailable.');
}

export async function openVoiceOrb() {
  if (active && voiceSessionId) {
    panel().hidden = false;
    return;
  }
  active = true;
  callGeneration += 1;
  const root = panel();
  if (root) root.hidden = false;
  setState('thinking', 'Connecting voice mode.');
  playCue('open');
  try {
    await ensureVoiceSession();
    if (!active) return;
    showToast('Voice Orb ready');
    await startListening();
  } catch (error) {
    fail(error);
  }
}

export async function closeVoiceOrb() {
  if (!active && panel()?.hidden) return;
  active = false;
  callGeneration += 1;
  await interruptResponse();
  stopCapture(true);
  stopPlayback();
  voiceSessionId = '';
  setTranscript('');
  setReply('');
  setState('idle');
  if (panel()) panel().hidden = true;
}

async function talkButtonPressed() {
  if (!active) {
    await openVoiceOrb();
    return;
  }
  if (!voiceSessionId) {
    active = false;
    await openVoiceOrb();
    return;
  }
  if (currentState === 'listening') {
    stopCapture(false);
    return;
  }
  if (currentState === 'speaking' || currentState === 'thinking') await interruptResponse();
  await startListening();
}

function init() {
  $('rail-voice-orb')?.addEventListener('click', openVoiceOrb);
  $('voice-orb-input')?.addEventListener('click', openVoiceOrb);
  $('voice-orb-talk')?.addEventListener('click', talkButtonPressed);
  $('voice-orb-close')?.addEventListener('click', closeVoiceOrb);
  window.addEventListener('pagehide', () => closeVoiceOrb());
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) closeVoiceOrb();
  });
  requestAnimationFrame(drawOrb);
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
else init();

export default { openVoiceOrb, closeVoiceOrb, voiceUIControlAllowed };
