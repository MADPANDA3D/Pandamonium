// static/js/jarvisVoice.js
// Jarvis call mode. Separate from voiceRecorder.js dictation.

import markdownModule from './markdown.js';

let sessionId = null;
let mediaRecorder = null;
let mediaStream = null;
let audioChunks = [];
let silenceTimer = null;
let maxTurnTimer = null;
let status = 'idle';
let isActive = false;
let isStopping = false;
let organicSphereFrame = null;
let playbackWaitResolve = null;
let sphereAudioContext = null;
let sphereAnalyser = null;
let sphereSource = null;
let sphereAudioTimer = null;
let sphereFreqData = null;
let chatSessionId = null;
let sphereSmoothedVolume = 0;
let sphereSmoothedLevels = Array(8).fill(0);
let playbackToken = 0;
let voiceTarget = 'jarvis';
let speechQueue = [];
let speechQueueRunning = false;
let currentSpeech = null;
let speechPaused = false;
let discardCurrentRecording = false;
let brainTurnInProgress = false;
let workerStreams = new Map();
let speechIdleResolvers = [];
let streamingAbortController = null;
let streamingAudioSources = new Set();
let activeWorkerTaskId = null;
let activeCodexThreadId = null;
let activeWorkspace = 'home-lab';
let activeWorkerQuestion = null;
let activeWorkerApproval = null;
let pendingWorkerText = null;
let liveAssistantMessage = null;
let streamingScheduledUntil = 0;
let streamingLastGain = null;
let activeTurnAudioPromise = null;
let activeAudioTurnId = null;
let captureAudioContext = null;

const ICON_PHONE = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.8 19.8 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.11 4.18 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.72c.13.96.35 1.9.66 2.81a2 2 0 0 1-.45 2.11L8.03 9.92a16 16 0 0 0 6.05 6.05l1.28-1.28a2 2 0 0 1 2.11-.45c.91.31 1.85.53 2.81.66A2 2 0 0 1 22 16.92z"/></svg>';
const ICON_MIC = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><path d="M12 19v3"/></svg>';
const ICON_STOP = '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>';
const ICON_CLOSE = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>';
const ORGANIC_SPHERE_URL = '/static/vendor/organic-sphere/index.html?v=20260710T195450Z';
const INSECURE_MIC_MESSAGE = 'Microphone needs localhost or HTTPS.';
const SPHERE_AUDIO_GAIN = 0.35;
const SPHERE_AUDIO_SMOOTHING = 0.75;
const SPOKEN_WORKER_EVENTS = new Set(['progress', 'question', 'approval_required', 'result', 'error']);
const DURABLE_SPEECH_TYPES = new Set(['question', 'approval_required', 'result', 'error']);
const PROGRESS_STALE_MS = 45000;
const STREAM_INITIAL_BUFFER_SECONDS = 2.2;
const STREAM_EDGE_CROSSFADE_SECONDS = 0.008;
const WORKER_LABELS = {
  jarvis: 'Jarvis',
  'pc-codex': 'PC Codex',
  hermes: 'Hermes',
  'vps-codex': 'VPS Codex',
};
let workerCatalog = {
  jarvis: { enabled: true, machine: 'Nimbus', connection: { state: 'connected' } },
  'pc-codex': { enabled: true, machine: 'Local workstation', connection: { state: 'checking' } },
  hermes: { enabled: false, machine: 'Hermes laptop', connection: { state: 'gated' } },
  'vps-codex': { enabled: false, machine: 'Remote server', connection: { state: 'gated' } },
};

function $(id) {
  return document.getElementById(id);
}

function showToast(message, duration = 2600) {
  const toast = $('toast');
  if (!toast) return;
  toast.textContent = message;
  toast.classList.add('show');
  setTimeout(() => toast.classList.remove('show'), duration);
}

function hasSecureMicContext() {
  return Boolean(window.isSecureContext && navigator.mediaDevices?.getUserMedia);
}

function logSphere(event, detail = {}) {
  console.info('[Jarvis sphere]', event, detail);
}

function clamp01(value) {
  const n = Number(value) || 0;
  return Math.max(0, Math.min(1, n));
}

function fallbackSphereLevels(next = status) {
  const t = Date.now() / 1000;
  const base = {
    listening: 0.12,
    speaking: 0.18,
    thinking: 0.08,
    transcribing: 0.06,
    background: 0.09,
    interrupted: 0.1,
  }[next] || 0.04;
  const pulse = (Math.sin(t * 3.2) + 1) * 0.5;
  return Array.from({ length: 8 }, (_, i) => clamp01(base * (0.45 + pulse * 0.45) / (i + 1)));
}

function shapeSphereLevels(volume, levels) {
  const shapedLevels = Array.from({ length: 8 }, (_, i) => {
    const target = clamp01((levels[i] || 0) * SPHERE_AUDIO_GAIN);
    sphereSmoothedLevels[i] = clamp01((sphereSmoothedLevels[i] * SPHERE_AUDIO_SMOOTHING) + (target * (1 - SPHERE_AUDIO_SMOOTHING)));
    return sphereSmoothedLevels[i];
  });
  const targetVolume = clamp01(volume * SPHERE_AUDIO_GAIN);
  sphereSmoothedVolume = clamp01((sphereSmoothedVolume * SPHERE_AUDIO_SMOOTHING) + (targetVolume * (1 - SPHERE_AUDIO_SMOOTHING)));
  return { volume: sphereSmoothedVolume, levels: shapedLevels };
}

function postSphereLevels(next = status, volume = 0, levels = fallbackSphereLevels(next)) {
  if (!organicSphereFrame?.contentWindow) return;
  const shaped = shapeSphereLevels(volume, levels);
  organicSphereFrame.contentWindow.postMessage({
    type: 'jarvis-audio-levels',
    state: next,
    volume: shaped.volume,
    levels: shaped.levels,
  }, window.location.origin);
}

function stopSphereAudio() {
  if (sphereAudioTimer) {
    clearInterval(sphereAudioTimer);
    sphereAudioTimer = null;
  }
  try { sphereSource?.disconnect(); } catch {}
  try { sphereAnalyser?.disconnect(); } catch {}
  sphereSource = null;
  sphereAnalyser = null;
  sphereFreqData = null;
  sphereSmoothedVolume = 0;
  sphereSmoothedLevels = Array(8).fill(0);
  streamingScheduledUntil = 0;
  streamingLastGain = null;
  if (sphereAudioContext) {
    sphereAudioContext.close().catch(() => {});
    sphereAudioContext = null;
  }
}

function startSpherePulse(next = status) {
  stopSphereAudio();
  sphereAudioTimer = setInterval(() => {
    const levels = fallbackSphereLevels(next);
    postSphereLevels(next, Math.max(...levels), levels);
  }, 120);
}

function startSphereAnalyser(sourceFactory, next = status) {
  stopSphereAudio();
  const AudioContext = window.AudioContext || window.webkitAudioContext;
  if (!AudioContext) {
    startSpherePulse(next);
    return;
  }
  try {
    sphereAudioContext = new AudioContext();
    sphereAnalyser = sphereAudioContext.createAnalyser();
    sphereAnalyser.fftSize = 256;
    sphereSource = sourceFactory(sphereAudioContext);
    sphereSource.connect(sphereAnalyser);
    sphereFreqData = new Uint8Array(sphereAnalyser.frequencyBinCount);
    sphereAudioContext.resume?.().catch(() => {});
    sphereAudioTimer = setInterval(() => {
      sphereAnalyser.getByteFrequencyData(sphereFreqData);
      const levelCount = 8;
      const binSize = Math.floor(sphereFreqData.length / levelCount) || 1;
      const levels = [];
      let max = 0;
      for (let i = 0; i < levelCount; i += 1) {
        let sum = 0;
        for (let j = 0; j < binSize; j += 1) sum += sphereFreqData[(i * binSize) + j] || 0;
        const value = clamp01(sum / binSize / 255);
        levels.push(value);
        if (value > max) max = value;
      }
      postSphereLevels(next, max, levels);
    }, 80);
    logSphere('audio-bridge-ready', { source: next });
  } catch (error) {
    console.warn('[Jarvis sphere] audio bridge fallback:', error);
    startSpherePulse(next);
  }
}

function startSphereStream(stream) {
  startSphereAnalyser(ctx => ctx.createMediaStreamSource(stream), 'listening');
}

function mountOrganicSphere() {
  const orb = $('jarvis-call-orb');
  if (!orb || organicSphereFrame) return;

  const frame = document.createElement('iframe');
  frame.className = 'jarvis-organic-frame';
  frame.title = 'Jarvis organic voice sphere';
  frame.src = ORGANIC_SPHERE_URL;
  frame.loading = 'eager';
  frame.referrerPolicy = 'no-referrer';
  frame.addEventListener('load', () => {
    logSphere('iframe-load');
    let attempts = 0;
    const markReady = () => {
      attempts += 1;
      if (markOrganicSphereReady('canvas-ready')) return;
      if (attempts < 40) window.setTimeout(markReady, 150);
      else logSphere('canvas-timeout');
    };
    markReady();
  }, { once: true });
  organicSphereFrame = frame;
  orb.appendChild(frame);
}

function markOrganicSphereReady(reason) {
  const orb = $('jarvis-call-orb');
  if (!orb || !organicSphereFrame) return false;
  try {
    const canvas = organicSphereFrame.contentDocument?.querySelector('canvas');
    if (!canvas) return false;
    const rect = canvas.getBoundingClientRect();
    orb.classList.add('has-frame');
    logSphere(reason, { width: Math.round(rect.width), height: Math.round(rect.height) });
    postSphereLevels(status);
    return true;
  } catch (error) {
    logSphere('canvas-check-failed', { message: error?.message || String(error) });
    return false;
  }
}

function handleSphereMessage(event) {
  if (!organicSphereFrame || event.source !== organicSphereFrame.contentWindow) return;
  if (event.data?.type === 'jarvis-sphere-ready') {
    logSphere('bridge-ready');
    if (!markOrganicSphereReady('bridge-ready')) {
      window.setTimeout(() => markOrganicSphereReady('bridge-ready-late'), 120);
    }
    postSphereLayout($('jarvis-call-panel')?.classList.contains('has-agent-task'));
  }
}

function unmountOrganicSphere() {
  const orb = $('jarvis-call-orb');
  stopSphereAudio();
  if (organicSphereFrame) {
    organicSphereFrame.removeAttribute('src');
    organicSphereFrame.remove();
    organicSphereFrame = null;
  }
  if (orb) orb.classList.remove('has-frame');
}

function setStatus(next, detail = '') {
  status = next;
  const root = $('jarvis-call-panel');
  const pill = $('jarvis-call-status');
  const detailEl = $('jarvis-call-detail');
  const talkBtn = $('jarvis-call-talk');
  const railBtn = $('rail-jarvis-call');
  const inputBtn = $('jarvis-input-sphere');
  const inputBar = document.querySelector('.chat-input-bar');
  document.body?.classList.toggle('jarvis-voice-active', isActive);
  document.documentElement?.classList.toggle('jarvis-voice-active', isActive);

  if (root) {
    root.dataset.state = next;
    root.hidden = !isActive;
  }
  if (pill) pill.textContent = statusLabel(next);
  if (detailEl) detailEl.textContent = detail || detailLabel(next);
  if (talkBtn) {
    talkBtn.dataset.state = next;
    talkBtn.disabled = next === 'thinking' || next === 'transcribing';
    talkBtn.innerHTML = next === 'listening' ? ICON_STOP : ICON_MIC;
    talkBtn.title = talkTitle(next);
  }
  if (railBtn) {
    railBtn.classList.toggle('active', isActive);
    railBtn.dataset.state = next;
  }
  if (inputBtn) {
    inputBtn.classList.toggle('active', isActive);
    inputBtn.dataset.state = next;
    inputBtn.title = sphereTitle(next);
    inputBtn.setAttribute('aria-label', sphereTitle(next));
  }
  if (inputBar) {
    inputBar.classList.toggle('jarvis-call-active', isActive);
    inputBar.dataset.jarvisState = next;
  }
  if (isActive && next !== 'failed') {
    mountOrganicSphere();
    if (next === 'speaking' && streamingAudioSources.size) {
      postSphereLevels(next);
    } else if (next === 'transcribing' || next === 'thinking' || next === 'worker' || next === 'background' || next === 'buffering' || next === 'interrupted' || next === 'speaking') {
      startSpherePulse(next);
    } else {
      postSphereLevels(next);
    }
  } else if (!isActive) {
    unmountOrganicSphere();
  }
  window._updateSendBtnIcon?.();
}

function statusLabel(value) {
  return {
    idle: 'Ready',
    listening: 'Listening',
    transcribing: 'Transcribing',
    thinking: 'Thinking',
    worker: 'Worker active',
    buffering: 'Preparing voice',
    speaking: 'Speaking',
    interrupted: 'Interrupted',
    background: 'Background task',
    ready: 'Ready',
    failed: 'Needs attention',
  }[value] || 'Ready';
}

function detailLabel(value) {
  return {
    idle: 'Jarvis is standing by.',
    listening: 'Listening for your turn.',
    transcribing: 'Reading your speech.',
    thinking: 'Jarvis is thinking.',
    worker: 'A connected worker is active.',
    buffering: 'Preparing Jarvis voice.',
    speaking: 'Jarvis is responding.',
    interrupted: 'Redirecting.',
    background: 'Running in the background, sir.',
    ready: 'Jarvis is standing by.',
    failed: 'The call loop hit an error.',
  }[value] || '';
}

function talkTitle(value) {
  if (value === 'listening') return 'Stop listening';
  if (value === 'speaking' || value === 'buffering') return 'Interrupt';
  return 'Speak to Jarvis';
}

function sphereTitle(value) {
  if (!isActive) return 'Jarvis live call';
  if (value === 'speaking' || value === 'buffering') return 'Interrupt Jarvis';
  if (value === 'failed') return 'End Jarvis call';
  return 'End Jarvis call';
}

async function fetchJson(url, options = {}) {
  const res = await fetch(url, {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const message = body?.detail?.message || body?.message || body?.error || res.statusText;
    throw new Error(message);
  }
  return body;
}

function activeTaskCount() {
  const taskIds = new Set(workerStreams.keys());
  if (activeWorkerTaskId) taskIds.add(activeWorkerTaskId);
  return taskIds.size;
}

function setAgentMenuOpen(open) {
  const chip = $('jarvis-agent-chip');
  const menu = $('jarvis-agent-menu');
  if (!chip || !menu) return;
  chip.setAttribute('aria-expanded', open ? 'true' : 'false');
  menu.hidden = !open;
}

function refreshAgentControl() {
  const details = workerCatalog[voiceTarget] || workerCatalog.jarvis;
  const connection = details.connection?.state || (details.enabled ? 'connected' : 'gated');
  const tasks = activeTaskCount();
  const name = $('jarvis-agent-name');
  const meta = $('jarvis-agent-meta');
  const state = $('jarvis-agent-state');
  if (name) name.textContent = WORKER_LABELS[voiceTarget] || voiceTarget;
  if (meta) {
    const taskText = tasks ? `${tasks} active task${tasks === 1 ? '' : 's'}` : (connection === 'connected' ? 'ready' : connection.replace(/_/g, ' '));
    meta.textContent = `${details.machine || 'worker'} · ${activeWorkspace || 'workspace unbound'} · ${taskText}`;
  }
  state?.classList.toggle('is-connected', connection === 'connected');
  document.querySelectorAll('.jarvis-target').forEach(button => {
    const worker = button.dataset.worker;
    const item = workerCatalog[worker] || {};
    const active = worker === voiceTarget;
    button.classList.toggle('is-active', active);
    button.setAttribute('aria-checked', active ? 'true' : 'false');
    button.disabled = worker !== 'jarvis' && !item.enabled;
    const detail = button.querySelector('small');
    if (detail) {
      const itemState = item.connection?.state || (item.enabled ? 'connected' : 'gated');
      detail.textContent = `${item.machine || 'worker'} · ${itemState.replace(/_/g, ' ')}`;
    }
  });
}

async function loadWorkerCatalog() {
  try {
    const workers = await fetchJson('/api/agent-workers');
    workerCatalog = { ...workerCatalog, ...workers };
  } catch (error) {
    console.warn('Could not load Jarvis worker status:', error);
  }
  refreshAgentControl();
}

function setVoiceTarget(worker, persist = true) {
  const details = workerCatalog[worker];
  if (worker !== 'jarvis' && details && !details.enabled) {
    showToast(`${WORKER_LABELS[worker] || worker} is not connected yet.`);
    return false;
  }
  voiceTarget = worker;
  setAgentMenuOpen(false);
  setAgentWorkspaceActive(worker !== 'jarvis' || activeTaskCount() > 0);
  refreshAgentControl();
  if (persist) persistVoiceTarget().catch(error => console.warn('Could not save voice target:', error));
  return true;
}

function persistVoiceTarget(extra = {}) {
  if (!sessionId) return Promise.resolve();
  return fetchJson(`/api/voice/sessions/${encodeURIComponent(sessionId)}/target`, {
    method: 'POST',
    body: JSON.stringify({
      target: voiceTarget,
      workspace: activeWorkspace,
      task_id: activeWorkerTaskId || '',
      codex_thread_id: activeCodexThreadId,
      ...extra,
    }),
  });
}

function workspaceForText(text) {
  if (/\b(business|clients?|marketing|mad\s*panda|campaign|website|crm)\b/i.test(text)) return 'business';
  if (/\b(project\s+linux|linux\s+(desktop|workstation)|hyprland)\b/i.test(text)) return 'project-linux';
  return 'home-lab';
}

function setAgentWorkspaceActive(active) {
  $('jarvis-call-panel')?.classList.toggle('has-agent-task', active);
  postSphereLayout(active);
}

function postSphereLayout(transparent) {
  try {
    const experience = organicSphereFrame?.contentWindow?.__jarvisSphereBridge?.experience;
    if (experience?.renderer) {
      experience.scene.background = null;
      experience.renderer.instance.setClearAlpha(0);
      experience.renderer.usePostprocess = !transparent;
    }
  } catch (error) {
    logSphere('layout-bridge-fallback', { message: error?.message || String(error) });
  }
  organicSphereFrame?.contentWindow?.postMessage({
    type: 'jarvis-layout',
    transparent: Boolean(transparent),
  }, window.location.origin);
}

function addTimelineEvent(event) {
  const timeline = $('chat-history');
  if (!timeline) return;
  const row = document.createElement('article');
  row.className = `jarvis-task-event jarvis-worker-progress is-${event.type || 'progress'}`;
  const label = document.createElement('span');
  label.className = 'jarvis-task-event-label';
  label.textContent = event.worker === 'pc-codex' ? 'PC Codex' : (event.worker || 'Jarvis');
  const text = document.createElement('p');
  text.textContent = event.text || '';
  row.append(label, text);
  const deepLink = event.metadata?.codex_deep_link
    || (event.metadata?.codex_thread_id ? `codex://threads/${event.metadata.codex_thread_id}` : '');
  if (deepLink) {
    const open = document.createElement('a');
    open.className = 'jarvis-task-event-action';
    open.href = deepLink;
    open.textContent = 'Open in Codex';
    open.title = 'Open this task in Codex Desktop';
    row.appendChild(open);
  }
  timeline.appendChild(row);
  timeline.scrollTop = timeline.scrollHeight;
  return row;
}

async function openWorkerArtifact(event) {
  const documentId = event.metadata?.document_id;
  if (!documentId || !window.documentModule?.loadDocument) return;
  setAgentWorkspaceActive(true);
  await window.documentModule.loadDocument(documentId);
}

async function submitWorkerApproval(event, choice, row = null, spokenText = '') {
  if (!event?.task_id) return;
  await fetchJson(`/api/agent-tasks/${encodeURIComponent(event.task_id)}/approval`, {
    method: 'POST',
    body: JSON.stringify({ choice, spoken_text: spokenText || null }),
  });
  activeWorkerApproval = null;
  row?.querySelectorAll('button').forEach(button => { button.disabled = true; });
  showToast(choice === 'deny' ? 'Worker action denied.' : 'Worker action approved.');
}

function addApprovalEvent(event) {
  const row = addTimelineEvent(event);
  if (!row) return;
  const actions = document.createElement('div');
  actions.className = 'jarvis-task-approval-actions';
  const approve = document.createElement('button');
  approve.type = 'button';
  approve.textContent = 'Approve once';
  const deny = document.createElement('button');
  deny.type = 'button';
  deny.textContent = 'Deny';
  approve.addEventListener('click', () => submitWorkerApproval(event, 'once', row).catch(handleError));
  deny.addEventListener('click', () => submitWorkerApproval(event, 'deny', row).catch(handleError));
  actions.append(approve, deny);
  row.appendChild(actions);
}

function resolveSpeechIdle() {
  if (speechQueueRunning || speechQueue.length || currentSpeech) return;
  speechIdleResolvers.splice(0).forEach(resolve => resolve());
}

function waitForSpeechQueueIdle() {
  if (!speechQueueRunning && !speechQueue.length && !currentSpeech) return Promise.resolve();
  return new Promise(resolve => speechIdleResolvers.push(resolve));
}

function resumeListeningIfReady() {
  if (!isActive || brainTurnInProgress || activeTurnAudioPromise || speechQueueRunning || speechQueue.length || currentSpeech) return;
  if (status === 'failed' || mediaRecorder?.state === 'recording') return;
  setStatus('listening');
  startListening().catch(handleError);
}

function enqueueSpeech(text, type = 'progress', source = 'jarvis', timings = {}) {
  const clean = (window.aiTTSManager?.extractPlainText?.(text) || text || '').trim();
  if (!clean) return;
  const key = clean.toLowerCase().replace(/\s+/g, ' ');
  if (speechQueue.some(item => item.key === key) || currentSpeech?.key === key) return;
  if (type === 'progress') {
    const pending = speechQueue.filter(item => item.type === 'progress');
    while (pending.length >= 3) {
      const stale = pending.shift();
      const index = speechQueue.indexOf(stale);
      if (index >= 0) speechQueue.splice(index, 1);
    }
  }
  speechQueue.push({ text: clean, type, source, key, timings, createdAt: Date.now() });
  if (!speechPaused) processSpeechQueue().catch(handleError);
}

function pauseCaptureForSpeech() {
  if (!mediaRecorder || mediaRecorder.state !== 'recording') return;
  discardCurrentRecording = true;
  clearTurnTimers();
  mediaRecorder.stop();
  stopTracks();
}

async function processSpeechQueue() {
  if (speechQueueRunning || speechPaused || activeTurnAudioPromise) return;
  speechQueueRunning = true;
  try {
    while (isActive && !speechPaused && speechQueue.length) {
      const item = speechQueue.shift();
      if (item.type === 'progress' && Date.now() - item.createdAt > PROGRESS_STALE_MS) continue;
      currentSpeech = item;
      pauseCaptureForSpeech();
      if (status !== 'speaking') setStatus('speaking');
      await speak(item.text, item.timings);
      currentSpeech = null;
    }
  } finally {
    speechQueueRunning = false;
    currentSpeech = null;
    resolveSpeechIdle();
    if (isActive && !speechPaused && !brainTurnInProgress && !activeTurnAudioPromise && !speechQueue.length && status !== 'failed') {
      if (sphereAudioContext && streamingScheduledUntil > sphereAudioContext.currentTime) {
        await waitForScheduledPlayback(sphereAudioContext, streamingScheduledUntil, playbackToken);
      }
      stopStreamingAudio();
      resumeListeningIfReady();
    }
  }
}

async function handleWorkerEvent(event) {
  if (event.metadata?.codex_thread_id) {
    activeCodexThreadId = event.metadata.codex_thread_id;
    activeWorkspace = event.metadata.workspace || activeWorkspace;
    await persistVoiceTarget({ codex_thread_id: activeCodexThreadId }).catch(() => {});
  }
  if (event.type === 'question') activeWorkerQuestion = event;
  if (event.type === 'approval_required') {
    activeWorkerApproval = event;
    addApprovalEvent(event);
  } else if (['progress', 'question', 'artifact', 'result', 'error', 'cancelled'].includes(event.type)) {
    addTimelineEvent(event);
  }
  if (event.type === 'artifact') await openWorkerArtifact(event);
  if (SPOKEN_WORKER_EVENTS.has(event.type)) enqueueSpeech(event.text, event.type, event.worker || 'worker');
  if (['result', 'error', 'cancelled'].includes(event.type)) {
    const stream = workerStreams.get(event.task_id);
    stream?.close();
    workerStreams.delete(event.task_id);
    activeWorkerTaskId = null;
    activeWorkerQuestion = null;
    activeWorkerApproval = null;
    await persistVoiceTarget({ task_id: '' }).catch(() => {});
    const queuedText = pendingWorkerText;
    pendingWorkerText = null;
    if (queuedText && voiceTarget === 'pc-codex') {
      await startDirectWorkerTask(queuedText);
    }
    if (event.type === 'result' && chatSessionId && window.sessionModule?.selectSession) {
      window.sessionModule.selectSession(chatSessionId, { keepSidebar: true }).catch(error => {
        console.warn('Could not refresh worker result in chat:', error);
      });
    }
    setAgentWorkspaceActive(voiceTarget !== 'jarvis' || activeTaskCount() > 0);
  }
  refreshAgentControl();
}

function followWorkerTask(taskId) {
  if (!taskId || workerStreams.has(taskId)) return;
  setAgentWorkspaceActive(true);
  const stream = new EventSource(`/api/agent-tasks/${encodeURIComponent(taskId)}/events?after=-1`);
  workerStreams.set(taskId, stream);
  refreshAgentControl();
  stream.onmessage = message => {
    try {
      handleWorkerEvent(JSON.parse(message.data)).catch(error => console.warn('Worker event handling failed:', error));
    } catch (error) { console.warn('Worker event parse failed:', error); }
  };
  stream.onerror = () => {
    stream.close();
    workerStreams.delete(taskId);
    refreshAgentControl();
  };
}

async function startDirectWorkerTask(text) {
  if (!sessionId) await createSession();
  if (activeWorkerQuestion?.task_id) {
    const questions = activeWorkerQuestion.metadata?.questions || [];
    const answers = {};
    if (questions.length) {
      questions.forEach((question, index) => {
        answers[String(question.id || question.header || `answer_${index + 1}`)] = [text];
      });
    } else {
      answers.answer = [text];
    }
    await fetchJson(`/api/agent-tasks/${encodeURIComponent(activeWorkerQuestion.task_id)}/reply`, {
      method: 'POST',
      body: JSON.stringify({ answers }),
    });
    activeWorkerQuestion = null;
    return null;
  }
  if (activeWorkerTaskId) {
    if (!pendingWorkerText) pendingWorkerText = text;
    else showToast('PC Codex already has one follow-up queued.');
    return null;
  }
  const task = await fetchJson('/api/agent-tasks', {
    method: 'POST',
    body: JSON.stringify({
      worker: voiceTarget,
      session_id: chatSessionId,
      workspace: workspaceForText(text),
      prompt: text,
      permission_mode: 'read_only',
      approved: false,
      persist_prompt: true,
      codex_thread_id: activeCodexThreadId,
    }),
  });
  activeWorkerTaskId = task.task_id;
  activeWorkspace = task.workspace || workspaceForText(text);
  await persistVoiceTarget({ task_id: activeWorkerTaskId }).catch(() => {});
  followWorkerTask(task.task_id);
  refreshAgentControl();
  return task;
}

function requestsJarvisTarget(text) {
  return /\b(back|return|switch|talk|speak)\b.*\bjarvis\b/i.test(text);
}

function renderLiveUser(text, timings, turnStarted) {
  if (window.sessionModule?.getCurrentSessionId?.() !== chatSessionId) return;
  window.chatModule?.addMessage?.('user', text, '', { source: 'jarvis_voice_live' });
  timings.chat_user_render_ms = performance.now() - turnStarted;
  window.uiModule?.scrollHistory?.();
}

function appendLiveAssistant(delta, model = '') {
  if (!delta || window.sessionModule?.getCurrentSessionId?.() !== chatSessionId) return;
  if (!liveAssistantMessage) {
    liveAssistantMessage = window.chatModule?.addMessage?.('assistant', delta, model, { source: 'jarvis_voice_live' }) || null;
    if (liveAssistantMessage) liveAssistantMessage.dataset.raw = delta;
  } else {
    liveAssistantMessage.dataset.raw = (liveAssistantMessage.dataset.raw || '') + delta;
    const body = liveAssistantMessage.querySelector('.body');
    if (body) body.innerHTML = markdownModule.processWithThinking(markdownModule.squashOutsideCode(liveAssistantMessage.dataset.raw));
  }
  window.uiModule?.scrollHistory?.();
}

async function postPlaybackState(turnId, state, timings = {}) {
  if (!sessionId || !turnId) return;
  await fetchJson(`/api/voice/sessions/${encodeURIComponent(sessionId)}/turns/${encodeURIComponent(turnId)}/playback`, {
    method: 'POST',
    body: JSON.stringify({ state, timings }),
  }).catch(error => console.warn('Could not update Jarvis playback state:', error));
}

async function playVoiceTurnAudio(turnId, timings) {
  const controller = new AbortController();
  streamingAbortController = controller;
  const response = await fetch(
    `/api/voice/sessions/${encodeURIComponent(sessionId)}/turns/${encodeURIComponent(turnId)}/audio`,
    { credentials: 'same-origin', signal: controller.signal },
  );
  if (!response.ok || !response.body) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body?.detail?.message || body?.detail || 'Jarvis audio stream failed');
  }
  const token = ++playbackToken;
  try {
    return await consumePcmResponse(response, timings, token, turnId);
  } catch (error) {
    if (token === playbackToken) await postPlaybackState(turnId, 'failed', timings);
    throw error;
  } finally {
    if (streamingAbortController === controller) streamingAbortController = null;
  }
}

async function streamTurn(text, timings, turnStarted) {
  if (!sessionId) await createSession();
  liveAssistantMessage = null;
  const response = await fetch(`/api/voice/sessions/${encodeURIComponent(sessionId)}/respond/stream`, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  });
  if (!response.ok || !response.body) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body?.detail?.message || body?.detail || response.statusText);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let final = null;
  let turnAudioPromise = null;
  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const frames = buffer.split('\n\n');
    buffer = frames.pop() || '';
    for (const frame of frames) {
      const line = frame.split('\n').find(row => row.startsWith('data: '));
      if (!line || line === 'data: [DONE]') continue;
      const event = JSON.parse(line.slice(6));
      if (event.type === 'assistant_delta') {
        const delta = event.text || '';
        appendLiveAssistant(delta, event.model || 'Jarvis');
        if (timings.chat_assistant_first_render_ms == null) timings.chat_assistant_first_render_ms = performance.now() - turnStarted;
      }
      else if (event.type === 'audio_ready') {
        activeAudioTurnId = event.turn_id;
        setStatus('buffering');
        const promise = playVoiceTurnAudio(event.turn_id, timings);
        activeTurnAudioPromise = promise;
        turnAudioPromise = promise;
        promise.then(() => {
          if (activeTurnAudioPromise === promise) activeTurnAudioPromise = null;
          if (activeAudioTurnId === event.turn_id) activeAudioTurnId = null;
          if (speechQueue.length) processSpeechQueue().catch(handleError);
        }, () => {
          if (activeTurnAudioPromise === promise) activeTurnAudioPromise = null;
          if (activeAudioTurnId === event.turn_id) activeAudioTurnId = null;
        });
      }
      else if (event.type === 'state' && event.state !== 'listening') setStatus(event.state);
      else if (event.type === 'target_changed') {
        activeWorkspace = event.workspace || activeWorkspace;
        setVoiceTarget(event.target || 'jarvis', false);
      }
      else if (event.type === 'agent_task') {
        activeWorkerTaskId = event.task_id;
        activeWorkspace = event.workspace || activeWorkspace;
        if (event.foreground !== false) setVoiceTarget(event.worker || 'pc-codex', false);
        else setAgentWorkspaceActive(true);
        persistVoiceTarget({ task_id: activeWorkerTaskId }).catch(() => {});
        followWorkerTask(event.task_id);
        refreshAgentControl();
      }
      else if (event.type === 'final') final = event;
      else if (event.type === 'error') throw new Error(event.text || 'Jarvis brain request failed');
    }
    if (done) break;
  }
  if (!final) throw new Error('Jarvis returned no final response.');
  if (!turnAudioPromise) throw new Error('Jarvis returned no audio stream.');
  return { ...final, audioPromise: turnAudioPromise };
}

async function createSession() {
  const activeChatSessionId = window.sessionModule?.getCurrentSessionId?.() || null;
  const session = await fetchJson('/api/voice/sessions', {
    method: 'POST',
    body: JSON.stringify({ mode: 'jarvis_call', chat_session_id: activeChatSessionId }),
  });
  sessionId = session.id;
  chatSessionId = session.chat_session_id || null;
  const savedTarget = session.target || 'jarvis';
  voiceTarget = 'jarvis';
  activeWorkspace = session.workspace || 'home-lab';
  activeWorkerTaskId = session.active_task_id || null;
  activeCodexThreadId = session.codex_thread_id || null;
  setVoiceTarget(savedTarget, false);
  return session;
}

async function transcribe(blob) {
  const form = new FormData();
  form.append('file', blob, `jarvis-turn-${Date.now()}.webm`);
  const res = await fetch('/api/stt/transcribe', {
    method: 'POST',
    credentials: 'same-origin',
    body: form,
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const message = body?.detail?.message || body?.error || 'Transcription failed';
    throw new Error(message);
  }
  return (body.text || '').trim();
}

async function sendTurn(text) {
  if (!sessionId) await createSession();
  return fetchJson(`/api/voice/sessions/${encodeURIComponent(sessionId)}/respond`, {
    method: 'POST',
    body: JSON.stringify({ text }),
  });
}

async function postTurnDiagnostics(timings) {
  if (!sessionId) return;
  await fetchJson(`/api/voice/sessions/${encodeURIComponent(sessionId)}/diagnostics`, {
    method: 'POST',
    body: JSON.stringify({ label: 'client_turn', timings }),
  }).catch(error => console.warn('Jarvis voice timing diagnostic failed:', error));
}

function prewarmVoiceStack() {
  const jobs = [
    fetch('/api/voice/prewarm', { method: 'POST', credentials: 'same-origin' }),
  ];
  if (window.aiTTSManager?.checkAvailability) {
    jobs.push(window.aiTTSManager.checkAvailability());
  }
  Promise.allSettled(jobs).catch(() => {});
}

async function interrupt() {
  if (currentSpeech && DURABLE_SPEECH_TYPES.has(currentSpeech.type)) speechQueue.unshift(currentSpeech);
  speechQueue = speechQueue.filter(item => DURABLE_SPEECH_TYPES.has(item.type));
  speechPaused = true;
  const interruptedTurnId = activeAudioTurnId;
  activeAudioTurnId = null;
  activeTurnAudioPromise = null;
  playbackToken += 1;
  resolvePlaybackWait();
  stopStreamingAudio();
  if (window.aiTTSManager) window.aiTTSManager.stop();
  if (sessionId) {
    if (interruptedTurnId) await postPlaybackState(interruptedTurnId, 'interrupted');
    await fetchJson(`/api/voice/sessions/${encodeURIComponent(sessionId)}/interrupt`, { method: 'POST', body: '{}' })
      .catch(() => {});
  }
  setStatus('interrupted');
}

function resolvePlaybackWait() {
  if (playbackWaitResolve) {
    playbackWaitResolve();
    playbackWaitResolve = null;
  }
}

function waitForScheduledPlayback(context, endTime, token) {
  if (token !== playbackToken || endTime <= context.currentTime) return Promise.resolve();
  return new Promise(resolve => {
    let timer = null;
    const done = () => {
      if (timer) clearTimeout(timer);
      if (playbackWaitResolve === done) playbackWaitResolve = null;
      resolve();
    };
    playbackWaitResolve = done;
    timer = setTimeout(done, Math.max(0, endTime - context.currentTime) * 1000 + 30);
  });
}

function stopStreamingAudio() {
  streamingAbortController?.abort();
  streamingAbortController = null;
  streamingAudioSources.forEach(source => {
    try { source.stop(); } catch {}
  });
  streamingAudioSources.clear();
  stopSphereAudio();
}

function pcm16FromBase64(value) {
  const bytes = Uint8Array.from(atob(value), char => char.charCodeAt(0));
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const samples = new Float32Array(bytes.byteLength / 2);
  for (let i = 0; i < samples.length; i += 1) {
    samples[i] = view.getInt16(i * 2, true) / 32768;
  }
  return samples;
}

function stopTracks() {
  if (mediaStream) {
    mediaStream.getTracks().forEach(track => track.stop());
    mediaStream = null;
  }
  stopSphereAudio();
}

function clearTurnTimers() {
  if (silenceTimer) {
    clearInterval(silenceTimer);
    silenceTimer = null;
  }
  if (maxTurnTimer) {
    clearTimeout(maxTurnTimer);
    maxTurnTimer = null;
  }
  if (captureAudioContext) {
    captureAudioContext.close().catch(() => {});
    captureAudioContext = null;
  }
}

function stopListening() {
  if (isStopping) return;
  isStopping = true;
  clearTurnTimers();
  if (mediaRecorder && mediaRecorder.state === 'recording') {
    mediaRecorder.stop();
  } else {
    stopTracks();
    isStopping = false;
    setStatus('idle');
  }
}

function startSilenceWatch(stream) {
  const AudioContext = window.AudioContext || window.webkitAudioContext;
  if (!AudioContext) return;
  const ctx = new AudioContext();
  captureAudioContext = ctx;
  const source = ctx.createMediaStreamSource(stream);
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 1024;
  source.connect(analyser);
  const data = new Uint8Array(analyser.fftSize);
  let heardVoice = false;
  let lastVoiceAt = 0;

  silenceTimer = setInterval(() => {
    analyser.getByteTimeDomainData(data);
    let sum = 0;
    for (let i = 0; i < data.length; i += 1) {
      const normalized = (data[i] - 128) / 128;
      sum += normalized * normalized;
    }
    const rms = Math.sqrt(sum / data.length);
    if (rms > 0.018) {
      heardVoice = true;
      lastVoiceAt = Date.now();
    }
    if (heardVoice && Date.now() - lastVoiceAt > 1200) {
      stopListening();
    }
  }, 140);

  maxTurnTimer = setTimeout(stopListening, 30000);
}

async function startListening() {
  if (!window.isSecureContext) {
    setStatus('failed', INSECURE_MIC_MESSAGE);
    showToast(INSECURE_MIC_MESSAGE);
    return;
  }
  if (!hasSecureMicContext()) {
    setStatus('failed', 'Microphone is not available.');
    return;
  }
  if (!isActive || brainTurnInProgress || activeTurnAudioPromise || speechQueueRunning || currentSpeech) return;
  if (mediaRecorder?.state === 'recording') return;

  audioChunks = [];
  isStopping = false;
  mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  startSphereStream(mediaStream);
  mediaRecorder = new MediaRecorder(mediaStream, { mimeType: 'audio/webm' });

  mediaRecorder.ondataavailable = event => {
    if (event.data?.size) audioChunks.push(event.data);
  };

  mediaRecorder.onstop = async () => {
    clearTurnTimers();
    stopTracks();
    isStopping = false;

    if (discardCurrentRecording) {
      discardCurrentRecording = false;
      return;
    }

    const blob = new Blob(audioChunks, { type: 'audio/webm' });
    if (!blob.size || !isActive) {
      setStatus(isActive ? 'idle' : 'idle');
      return;
    }

    try {
      const turnStarted = performance.now();
      setStatus('transcribing');
      const timings = { turn_started_at: turnStarted };
      const sttStarted = performance.now();
      const text = await transcribe(blob);
      timings.stt_ms = performance.now() - sttStarted;
      timings.transcript_chars = text.length;
      const transcriptEl = $('jarvis-call-transcript');
      if (transcriptEl) transcriptEl.textContent = text || '';
      if (!text) {
        setStatus('listening', 'No speech detected.');
        window.setTimeout(() => { if (isActive) startListening().catch(handleError); }, 800);
        return;
      }
      renderLiveUser(text, timings, turnStarted);

      speechPaused = false;
      if (activeWorkerApproval?.task_id) {
        const approves = /\b(approve|approved|yes|allow|go ahead|proceed)\b/i.test(text);
        const denies = /\b(deny|denied|no|reject|do not|don't)\b/i.test(text);
        if (approves || denies) {
          setStatus('background');
          await submitWorkerApproval(activeWorkerApproval, approves ? 'once' : 'deny', null, text);
          resumeListeningIfReady();
          return;
        }
      }
      if (voiceTarget !== 'jarvis' && !requestsJarvisTarget(text)) {
        setStatus('background');
        await startDirectWorkerTask(text);
        brainTurnInProgress = false;
        resumeListeningIfReady();
        return;
      }
      if (requestsJarvisTarget(text)) setVoiceTarget('jarvis');

      setStatus('thinking');
      brainTurnInProgress = true;
      const brainStarted = performance.now();
      const response = await streamTurn(text, timings, turnStarted);
      timings.respond_ms = performance.now() - brainStarted;
      const reply = response.assistant_text || '';
      const diagnostic = response.diagnostics || {};
      if (diagnostic.brain_ms != null) timings.brain_ms = diagnostic.brain_ms;
      if (diagnostic.brain_first_token_ms != null) timings.brain_first_token_ms = diagnostic.brain_first_token_ms;
      timings.assistant_chars = reply.length;
      timings.num_predict = diagnostic.num_predict || '';
      const panel = $('jarvis-call-panel');
      if (panel) {
        panel.dataset.voiceModel = diagnostic.model || '';
        panel.dataset.turnDiagnostic = `${text.length}:${reply.length}:${diagnostic.guard_reason || 'ok'}`;
      }
      const replyEl = $('jarvis-call-reply');
      if (replyEl) replyEl.textContent = reply;
      brainTurnInProgress = false;
      await response.audioPromise;
      if (activeTurnAudioPromise === response.audioPromise) activeTurnAudioPromise = null;
      activeAudioTurnId = null;
      if (!speechQueueRunning && speechQueue.length) processSpeechQueue().catch(handleError);
      await waitForSpeechQueueIdle();
      stopStreamingAudio();
      delete timings.turn_started_at;
      await postTurnDiagnostics(timings);
      resumeListeningIfReady();
    } catch (error) {
      brainTurnInProgress = false;
      handleError(error);
    }
  };

  mediaRecorder.start();
  setStatus('listening');
  startSilenceWatch(mediaStream);
}

async function ensurePlaybackContext() {
  const AudioContext = window.AudioContext || window.webkitAudioContext;
  if (!AudioContext) throw new Error('Streaming audio is not supported by this browser.');
  const freshContext = !sphereAudioContext || sphereAudioContext.state === 'closed' || !sphereAnalyser;
  if (freshContext) {
    stopSphereAudio();
    sphereAudioContext = new AudioContext();
    sphereAnalyser = sphereAudioContext.createAnalyser();
    sphereAnalyser.fftSize = 256;
    sphereAnalyser.connect(sphereAudioContext.destination);
    sphereFreqData = new Uint8Array(sphereAnalyser.frequencyBinCount);
    sphereAudioTimer = setInterval(() => {
      sphereAnalyser.getByteFrequencyData(sphereFreqData);
      const binSize = Math.floor(sphereFreqData.length / 8) || 1;
      const levels = Array.from({ length: 8 }, (_, index) => {
        let sum = 0;
        for (let offset = 0; offset < binSize; offset += 1) sum += sphereFreqData[(index * binSize) + offset] || 0;
        return clamp01(sum / binSize / 255);
      });
      postSphereLevels('speaking', Math.max(...levels), levels);
    }, 80);
    streamingScheduledUntil = 0;
    streamingLastGain = null;
  }
  const context = sphereAudioContext;
  await context.resume();
  return context;
}

async function consumePcmResponse(response, timings, token, turnId = null) {
  const started = performance.now();
  const context = await ensurePlaybackContext();
  timings.tts_chunks = 0;
  timings.scheduler_underruns = 0;

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let sampleRate = 48000;
  let streamDone = null;
  let playbackStarted = false;
  let nextAudioStartsBlock = false;
  const handleLine = line => {
    if (!line.trim()) return;
    const event = JSON.parse(line);
    if (event.type === 'start') {
      sampleRate = Number(event.sample_rate) || sampleRate;
      return;
    }
    if (event.type === 'error') throw new Error(event.error || 'VoxCPM streaming failed');
    if (event.type === 'done') {
      streamDone = event;
      return;
    }
    if (event.type === 'block') {
      timings.tts_blocks = Number(event.index) + 1;
      nextAudioStartsBlock = true;
      return;
    }
    if (event.type !== 'audio' || !event.pcm_base64 || token !== playbackToken) return;
    const samples = pcm16FromBase64(event.pcm_base64);
    const audioBuffer = context.createBuffer(1, samples.length, sampleRate);
    audioBuffer.copyToChannel(samples, 0);
    const source = context.createBufferSource();
    const gain = context.createGain();
    source.buffer = audioBuffer;
    source.connect(gain);
    gain.connect(sphereAnalyser);
    source.onended = () => {
      streamingAudioSources.delete(source);
      try { gain.disconnect(); } catch {}
    };
    streamingAudioSources.add(source);
    const previousEnd = streamingScheduledUntil;
    const hasQueuedAudio = Boolean(streamingLastGain && previousEnd > context.currentTime + 0.005);
    if (streamingLastGain && !hasQueuedAudio) timings.scheduler_underruns += 1;
    const crossfadeBoundary = hasQueuedAudio && nextAudioStartsBlock;
    const beginsAt = hasQueuedAudio
      ? Math.max(context.currentTime + 0.005, previousEnd - (crossfadeBoundary ? STREAM_EDGE_CROSSFADE_SECONDS : 0))
      : context.currentTime + STREAM_INITIAL_BUFFER_SECONDS;
    if (crossfadeBoundary) {
      const fadeEndsAt = beginsAt + STREAM_EDGE_CROSSFADE_SECONDS;
      streamingLastGain.gain.cancelScheduledValues(beginsAt);
      streamingLastGain.gain.setValueAtTime(1, beginsAt);
      streamingLastGain.gain.linearRampToValueAtTime(0, fadeEndsAt);
      gain.gain.setValueAtTime(0, beginsAt);
      gain.gain.linearRampToValueAtTime(1, fadeEndsAt);
    } else {
      gain.gain.setValueAtTime(1, beginsAt);
    }
    nextAudioStartsBlock = false;
    source.start(beginsAt);
    streamingScheduledUntil = beginsAt + audioBuffer.duration;
    streamingLastGain = gain;
    timings.tts_chunks += 1;
    if (timings.tts_first_audio_ms == null) {
      timings.tts_first_audio_ms = performance.now() - started;
      if (timings.turn_started_at != null) timings.end_to_first_audio_ms = performance.now() - timings.turn_started_at;
    }
    if (!playbackStarted) {
      playbackStarted = true;
      setStatus('speaking');
      if (turnId) postPlaybackState(turnId, 'started', timings);
    }
  };

  try {
    while (isActive && token === playbackToken) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';
      lines.forEach(handleLine);
      if (done) break;
    }
    if (buffer.trim()) handleLine(buffer);
    if (token === playbackToken && streamingScheduledUntil > context.currentTime) {
      await waitForScheduledPlayback(context, streamingScheduledUntil, token);
    }
  } catch (error) {
    if (token === playbackToken && error?.name !== 'AbortError') throw error;
  } finally {
    reader.cancel().catch(() => {});
    timings.tts_generation_ms = Number(streamDone?.generation_ms) || performance.now() - started;
    timings.playback_duration_ms = Number(streamDone?.audio_ms) || 0;
    timings.tts_total_ms = performance.now() - started;
  }
  if (turnId && token === playbackToken) await postPlaybackState(turnId, 'completed', timings);
  return streamDone;
}

async function speak(text, timings = {}) {
  const manager = window.aiTTSManager;
  if (!manager) return;
  await manager.checkAvailability?.();
  if (!manager.available) return;
  if (manager.useBrowserTTS) {
    const started = performance.now();
    await manager.play(text);
    timings.tts_total_ms = performance.now() - started;
    return;
  }

  const token = ++playbackToken;
  const controller = new AbortController();
  streamingAbortController = controller;
  const response = await fetch('/api/tts/stream', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, format: 'audio', use_cache: false }),
    signal: controller.signal,
  });
  if (!response.ok || !response.body) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body?.detail?.message || body?.detail || 'Streaming speech failed');
  }
  try {
    await consumePcmResponse(response, timings, token);
  } finally {
    if (streamingAbortController === controller) streamingAbortController = null;
  }
}

function handleError(error) {
  console.error('Jarvis voice error:', error);
  activeTurnAudioPromise = null;
  activeAudioTurnId = null;
  brainTurnInProgress = false;
  clearTurnTimers();
  stopTracks();
  setStatus('failed', error.message || 'Voice loop failed.');
  showToast(error.message || 'Voice loop failed.');
}

async function startCall() {
  if (!window.isSecureContext) {
    isActive = false;
    sessionId = null;
    setStatus('failed', INSECURE_MIC_MESSAGE);
    showToast(INSECURE_MIC_MESSAGE);
    return;
  }
  if (!hasSecureMicContext()) {
    isActive = false;
    sessionId = null;
    setStatus('failed', 'Microphone is not available.');
    showToast('Microphone is not available.');
    return;
  }

  isActive = true;
  speechPaused = false;
  speechQueue = [];
  brainTurnInProgress = false;
  activeWorkerTaskId = null;
  activeCodexThreadId = null;
  activeWorkspace = 'home-lab';
  pendingWorkerText = null;
  liveAssistantMessage = null;
  activeTurnAudioPromise = null;
  activeAudioTurnId = null;
  const timeline = $('jarvis-task-timeline');
  if (timeline) timeline.replaceChildren();
  setAgentWorkspaceActive(false);
  mountOrganicSphere();
  await createSession();
  setStatus('idle');
  prewarmVoiceStack();
  await startListening();
}

function endCall() {
  const linkedChatSessionId = chatSessionId;
  isActive = false;
  brainTurnInProgress = false;
  activeTurnAudioPromise = null;
  activeAudioTurnId = null;
  speechPaused = true;
  speechQueue = [];
  currentSpeech = null;
  workerStreams.forEach(stream => stream.close());
  workerStreams.clear();
  setAgentWorkspaceActive(false);
  playbackToken += 1;
  clearTurnTimers();
  resolvePlaybackWait();
  stopStreamingAudio();
  if (window.aiTTSManager) window.aiTTSManager.stop();
  stopSphereAudio();
  stopListening();
  stopTracks();
  setStatus('idle');
  unmountOrganicSphere();
  const panel = $('jarvis-call-panel');
  if (panel) panel.hidden = true;
  sessionId = null;
  chatSessionId = null;
  voiceTarget = 'jarvis';
  activeWorkerTaskId = null;
  pendingWorkerText = null;
  liveAssistantMessage = null;
  if (linkedChatSessionId) openLinkedChatSession(linkedChatSessionId);
}

async function openLinkedChatSession(linkedChatSessionId) {
  try {
    if (!window.sessionModule?.selectSession) return;
    if (window.sessionModule.loadSessions) await window.sessionModule.loadSessions();
    await window.sessionModule.selectSession(linkedChatSessionId, { keepSidebar: true });
  } catch (error) {
    console.warn('Could not open Jarvis voice transcript session:', error);
  }
}

function isCallActive() {
  return isActive;
}

function toggleCall() {
  if (isActive) {
    endCall();
    return;
  }
  startCall().catch(handleError);
}

async function handleInputSphereClick() {
  if (!isActive) {
    await startCall();
    return;
  }
  if (status === 'speaking' || status === 'buffering') {
    await interrupt();
    await startListening();
    return;
  }
  endCall();
}

function bind() {
  if (document.documentElement.dataset.jarvisVoiceBound === '1') return;
  document.documentElement.dataset.jarvisVoiceBound = '1';
  const railBtn = $('rail-jarvis-call');
  const closeBtn = $('jarvis-call-close');
  const talkBtn = $('jarvis-call-talk');
  const inputBtn = $('jarvis-input-sphere');
  const agentChip = $('jarvis-agent-chip');
  const agentSelector = document.querySelector('.jarvis-agent-selector');
  const targetButtons = document.querySelectorAll('.jarvis-target');

  if (railBtn) {
    railBtn.innerHTML = ICON_PHONE;
    railBtn.addEventListener('click', toggleCall);
  }
  if (closeBtn) {
    closeBtn.innerHTML = ICON_CLOSE;
    closeBtn.addEventListener('click', endCall);
  }
  if (talkBtn) {
    talkBtn.innerHTML = ICON_MIC;
    talkBtn.addEventListener('click', async () => {
      if (!isActive) {
        await startCall();
        return;
      }
      if (status === 'listening') {
        stopListening();
      } else if (status === 'speaking' || status === 'buffering') {
        await interrupt();
        await startListening();
      } else if (status === 'idle' || status === 'interrupted' || status === 'failed') {
        await startListening();
      }
    });
  }
  if (inputBtn) {
    inputBtn.addEventListener('click', () => {
      handleInputSphereClick().catch(handleError);
    });
  }
  if (agentChip) {
    agentChip.addEventListener('click', async event => {
      event.stopPropagation();
      await loadWorkerCatalog();
      setAgentMenuOpen(agentChip.getAttribute('aria-expanded') !== 'true');
    });
  }
  targetButtons.forEach(button => {
    button.addEventListener('click', () => {
      if (!button.disabled) {
        activeWorkspace = button.dataset.workspace || activeWorkspace;
        setVoiceTarget(button.dataset.worker || 'jarvis');
      }
    });
  });
  document.addEventListener('click', event => {
    if (!agentSelector?.contains(event.target)) setAgentMenuOpen(false);
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') setAgentMenuOpen(false);
  });

  window.addEventListener('message', handleSphereMessage);
  setVoiceTarget('jarvis');
  loadWorkerCatalog().catch(() => {});
  setStatus('idle');
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', bind);
} else {
  bind();
}

window.jarvisVoice = { startCall, endCall, interrupt, isActive: isCallActive };
