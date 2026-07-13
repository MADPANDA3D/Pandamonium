// static/js/jarvisVoice.js
// Jarvis call mode. Separate from voiceRecorder.js dictation.

import markdownModule from './markdown.js';

let sessionId = null;
let mediaRecorder = null;
let mediaStream = null;
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
let cueAudioContext = null;
let chatSessionId = null;
let sphereSmoothedVolume = 0;
let sphereSmoothedLevels = Array(8).fill(0);
let playbackToken = 0;
let voiceTarget = 'jarvis';
let speechQueue = [];
let speechQueueRunning = false;
let currentSpeech = null;
let speechPaused = false;
let discardRecordingGeneration = null;
let brainTurnInProgress = false;
let workerStreams = new Map();
let workerEventChains = new Map();
let handledWorkerEventIds = new Set();
let taskSnapshots = new Map();
let activityTicker = null;
let activityRestoreRevision = 0;
let speechIdleResolvers = [];
let playbackAbortController = null;
let playbackAudioSources = new Set();
let activeWorkerTaskId = null;
let activeCodexThreadId = null;
let activeWorkspace = 'home-lab';
let liveAssistantMessage = null;
let activeTurnAudioPromise = null;
let activeAudioTurnId = null;
let captureAudioContext = null;
let captureVoicedMs = 0;
let voiceCallGeneration = 0;

const ICON_PHONE = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.8 19.8 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.11 4.18 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.72c.13.96.35 1.9.66 2.81a2 2 0 0 1-.45 2.11L8.03 9.92a16 16 0 0 0 6.05 6.05l1.28-1.28a2 2 0 0 1 2.11-.45c.91.31 1.85.53 2.81.66A2 2 0 0 1 22 16.92z"/></svg>';
const ICON_MIC = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><path d="M12 19v3"/></svg>';
const ICON_STOP = '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>';
const ICON_CLOSE = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>';
const END_VOICE_LABEL = 'End voice — task continues';
const ORGANIC_SPHERE_URL = '/static/vendor/organic-sphere/index.html?v=20260710T195450Z';
const INSECURE_MIC_MESSAGE = 'Microphone needs localhost or HTTPS.';
const SPHERE_AUDIO_GAIN = 0.35;
const SPHERE_AUDIO_SMOOTHING = 0.75;
const VOICE_RMS_THRESHOLD = 0.018;
const VOICE_SAMPLE_INTERVAL_MS = 140;
const MIN_VOICED_MS = 280;
const SPOKEN_WORKER_EVENTS = new Set(['progress', 'question', 'approval_required', 'result', 'error']);
const DURABLE_SPEECH_TYPES = new Set(['question', 'approval_required', 'error']);
const WORKER_SPEECH_MAX_CHARS = 700;
const TERMINAL_TASK_STATES = new Set(['completed', 'failed', 'cancelled', 'blocked']);
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

function isCurrentVoiceCall(callGeneration) {
  return isActive && callGeneration === voiceCallGeneration;
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
  if (sphereAudioContext) {
    sphereAudioContext.close().catch(() => {});
    sphereAudioContext = null;
  }
}

function playVoiceCue(name, delay = 0) {
  const AudioContext = window.AudioContext || window.webkitAudioContext;
  if (!AudioContext) return Promise.resolve();
  try {
    if (!cueAudioContext || cueAudioContext.state === 'closed') cueAudioContext = new AudioContext();
    cueAudioContext.resume?.().catch(() => {});
    const tones = {
      call: [[392, 0, 0.09], [523, 0.1, 0.14]],
      heard: [[784, 0, 0.07]],
      thinking: [[440, 0, 0.055], [554, 0.075, 0.075]],
    }[name] || [];
    const base = cueAudioContext.currentTime + Math.max(0, delay) + 0.005;
    tones.forEach(([frequency, offset, duration]) => {
      const oscillator = cueAudioContext.createOscillator();
      const gain = cueAudioContext.createGain();
      const start = base + offset;
      const end = start + duration;
      oscillator.type = 'sine';
      oscillator.frequency.setValueAtTime(frequency, start);
      gain.gain.setValueAtTime(0.0001, start);
      gain.gain.linearRampToValueAtTime(0.055, start + 0.012);
      gain.gain.exponentialRampToValueAtTime(0.0001, end);
      oscillator.connect(gain);
      gain.connect(cueAudioContext.destination);
      oscillator.start(start);
      oscillator.stop(end + 0.01);
    });
    const cueSeconds = tones.reduce((longest, [, offset, duration]) => Math.max(longest, offset + duration), 0);
    return new Promise(resolve => window.setTimeout(resolve, (Math.max(0, delay) + cueSeconds) * 1000));
  } catch (error) {
    console.warn('Jarvis voice cue unavailable:', error);
    return Promise.resolve();
  }
}

function closeVoiceCueAudio() {
  if (!cueAudioContext) return;
  cueAudioContext.close().catch(() => {});
  cueAudioContext = null;
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
    postSphereLayout(true);
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
    if (next === 'speaking' && playbackAudioSources.size) {
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
  return END_VOICE_LABEL;
}

function browserTimezoneHeaders() {
  let name = '';
  try { name = Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch {}
  return {
    'X-Tz-Offset': String(-new Date().getTimezoneOffset()),
    'X-Tz-Name': name,
  };
}

async function fetchJson(url, options = {}) {
  const { headers = {}, ...requestOptions } = options;
  const res = await fetch(url, {
    credentials: 'same-origin',
    ...requestOptions,
    headers: { 'Content-Type': 'application/json', ...headers },
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
  const cancel = $('jarvis-agent-cancel');
  if (name) name.textContent = WORKER_LABELS[voiceTarget] || voiceTarget;
  if (meta) {
    const taskText = tasks ? `${tasks} active task${tasks === 1 ? '' : 's'}` : (connection === 'connected' ? 'ready' : connection.replace(/_/g, ' '));
    meta.textContent = `${details.machine || 'worker'} · ${activeWorkspace || 'workspace unbound'} · ${taskText}`;
  }
  state?.classList.toggle('is-connected', connection === 'connected');
  if (cancel) {
    cancel.hidden = !activeWorkerTaskId;
    cancel.disabled = !activeWorkerTaskId;
  }
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

function setAgentWorkspaceActive(active) {
  $('jarvis-call-panel')?.classList.toggle('has-agent-task', active);
  document.body?.classList.toggle('jarvis-agent-workspace-active', active);
  postSphereLayout(true);
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

function currentChatSessionId() {
  return window.sessionModule?.getCurrentSessionId?.() || null;
}

function taskActivityElement(taskId) {
  return Array.from(document.querySelectorAll('.jarvis-task-activity[data-task-id]'))
    .find(item => item.dataset.taskId === String(taskId)) || null;
}

function taskMessageElements(taskId) {
  return Array.from(document.querySelectorAll('#chat-history .msg[data-task-id]'))
    .filter(item => item.dataset.taskId === String(taskId));
}

function taskSummaryElements(taskId) {
  return taskMessageElements(taskId)
    .filter(item => item.dataset.source === 'jarvis_worker_summary');
}

function rememberTask(task) {
  const taskId = String(task?.task_id || '');
  if (!taskId) return null;
  const prior = taskSnapshots.get(taskId) || {};
  const merged = { ...prior, ...task, task_id: taskId };
  if (!Array.isArray(task.events) && Array.isArray(prior.events)) merged.events = prior.events;
  taskSnapshots.set(taskId, merged);
  return merged;
}

function taskVisible(task) {
  return Boolean(task?.session_id && task.session_id === currentChatSessionId());
}

function elapsedTaskTime(task, now = Date.now() / 1000) {
  const started = Number(task?.created_at || now);
  const ended = TERMINAL_TASK_STATES.has(String(task?.status || ''))
    ? Number(task?.updated_at || now)
    : now;
  const seconds = Math.max(0, Math.floor(ended - started));
  return `${Math.floor(seconds / 60)}m ${String(seconds % 60).padStart(2, '0')}s`;
}

function activityTitle(task) {
  const elapsed = elapsedTaskTime(task);
  if (task?.status === 'completed') return `Worked for ${elapsed}`;
  if (task?.status === 'failed' || task?.status === 'blocked') return `Failed after ${elapsed}`;
  if (task?.status === 'cancelled') return `Cancelled after ${elapsed}`;
  return `Working for ${elapsed}`;
}

function updateActivitySummary(group, task) {
  if (!group || !task) return;
  const duration = group.querySelector('.jarvis-task-duration');
  const worker = group.querySelector('.jarvis-task-worker');
  if (duration) duration.textContent = activityTitle(task);
  if (worker) worker.textContent = WORKER_LABELS[task.worker] || task.worker || 'Worker';
}

function ensureActivityTicker() {
  if (activityTicker) return;
  activityTicker = window.setInterval(() => {
    const activeGroups = Array.from(document.querySelectorAll('.jarvis-task-activity[data-task-id]'))
      .filter(group => !TERMINAL_TASK_STATES.has(group.dataset.status || ''));
    activeGroups.forEach(group => {
      const task = taskSnapshots.get(group.dataset.taskId);
      if (task) updateActivitySummary(group, task);
    });
    if (!activeGroups.length) {
      window.clearInterval(activityTicker);
      activityTicker = null;
    }
  }, 1000);
}

function positionActivityGroup(group) {
  if (!group) return;
  const box = $('chat-history');
  if (!box) return;
  const rail = $('jarvis-activity-rail');
  if (isActive && rail && !TERMINAL_TASK_STATES.has(group.dataset.status || '')) {
    if (group.parentElement !== rail) rail.appendChild(group);
    return;
  }
  const messages = taskMessageElements(group.dataset.taskId);
  const acknowledgement = messages.find(item => (
    item.classList.contains('msg-ai')
    && ['jarvis_voice', 'jarvis_voice_live'].includes(item.dataset.source || '')
  ));
  const firstSummary = messages.find(item => item.dataset.source === 'jarvis_worker_summary');
  const result = messages.find(item => item.dataset.source === 'agent_worker');
  if (acknowledgement) acknowledgement.after(group);
  else if (firstSummary) firstSummary.before(group);
  else if (result) result.before(group);
  else if (group.parentElement !== box) box.appendChild(group);
}

function positionVisibleActivityGroups() {
  document.querySelectorAll('.jarvis-task-activity[data-task-id]').forEach(positionActivityGroup);
}

function restoreActivityGroupsToChat() {
  $('jarvis-activity-rail')?.querySelectorAll('.jarvis-task-activity[data-task-id]').forEach(positionActivityGroup);
}

function positionWorkerResult(result, taskId) {
  const group = taskActivityElement(taskId);
  if (group && result && group.parentElement !== result.parentElement) group.after(result);
}

function setActivityStatus(group, task, status) {
  if (!group || !task) return;
  const previous = group.dataset.status || '';
  task.status = status || task.status || 'running';
  group.dataset.status = task.status;
  group.className = `jarvis-task-activity is-${task.status}`;
  const cancel = group.querySelector('.jarvis-task-cancel');
  if (cancel) cancel.hidden = TERMINAL_TASK_STATES.has(task.status);
  if (TERMINAL_TASK_STATES.has(task.status)) {
    group.querySelectorAll('.jarvis-task-approval-actions button').forEach(button => { button.disabled = true; });
  }
  if (task.status === 'completed') group.open = false;
  else if (task.status === 'failed' || task.status === 'cancelled' || task.status === 'blocked') group.open = true;
  else if (!previous) group.open = true;
  updateActivitySummary(group, task);
  if (TERMINAL_TASK_STATES.has(task.status)) positionActivityGroup(group);
  if (!TERMINAL_TASK_STATES.has(task.status)) ensureActivityTicker();
}

async function cancelWorkerTask(taskId) {
  if (!taskId || !window.confirm('Cancel the active task?')) return;
  await fetchJson(`/api/agent-tasks/${encodeURIComponent(taskId)}/cancel`, { method: 'POST' });
  showToast('Cancellation requested. Voice remains open.');
}

function ensureActivityGroup(task) {
  task = rememberTask(task);
  if (!task || !taskVisible(task)) return null;
  let group = taskActivityElement(task.task_id);
  if (!group) {
    group = document.createElement('details');
    group.className = 'jarvis-task-activity';
    group.dataset.taskId = task.task_id;
    group.dataset.sessionId = task.session_id;

    const summary = document.createElement('summary');
    const indicator = document.createElement('span');
    indicator.className = 'jarvis-task-indicator';
    indicator.setAttribute('aria-hidden', 'true');
    const duration = document.createElement('span');
    duration.className = 'jarvis-task-duration';
    const worker = document.createElement('span');
    worker.className = 'jarvis-task-worker';
    summary.append(indicator, duration, worker);

    const history = document.createElement('div');
    history.className = 'jarvis-task-activity-history';

    const controls = document.createElement('div');
    controls.className = 'jarvis-task-activity-controls';
    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.className = 'jarvis-task-cancel';
    cancel.textContent = 'Cancel Task';
    cancel.addEventListener('click', event => {
      event.preventDefault();
      cancelWorkerTask(task.task_id).catch(error => showToast(error.message || 'Could not cancel the task.'));
    });
    controls.appendChild(cancel);
    group.append(summary, history, controls);
    $('chat-history')?.appendChild(group);
  }
  setActivityStatus(group, task, task.status || 'running');
  positionActivityGroup(group);
  return group;
}

function activityEventKey(event) {
  return String(event.event_id || `${event.task_id || 'task'}:${event.seq ?? 'event'}`);
}

function toolActivityLabel(event) {
  const kind = String(event.metadata?.item_type || event.metadata?.source_event || '');
  const text = String(event.text || '').toLowerCase();
  if (kind === 'fileChange') return 'Edited files';
  if (kind === 'webSearch') return 'Searched the web';
  if (kind === 'mcpToolCall' || /\btool\b/.test(text)) return 'Used tools';
  if (/command completed:\s*(?:rg|grep|sed|cat|head|tail|less|ls|find|git (?:status|diff|show|log))\b/.test(text)) return 'Read files';
  if (kind === 'commandExecution' || /\bcommand\b/.test(text)) return 'Ran commands';
  if (event.metadata?.codex_thread_id) return 'Opened task';
  return 'Used tools';
}

function appendActivityAction(row, event) {
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
  if (event.type === 'artifact' && event.metadata?.document_id) {
    const open = document.createElement('button');
    open.type = 'button';
    open.className = 'jarvis-task-event-action';
    open.textContent = 'Open artifact';
    open.addEventListener('click', () => openWorkerArtifact(event).catch(handleError));
    row.appendChild(open);
  }
}

function ensureTaskDeepLink(group, event) {
  const deepLink = event.metadata?.codex_deep_link
    || (event.metadata?.codex_thread_id ? `codex://threads/${event.metadata.codex_thread_id}` : '');
  const controls = group?.querySelector('.jarvis-task-activity-controls');
  if (!deepLink || !controls || controls.querySelector('.jarvis-task-open-codex')) return;
  const open = document.createElement('a');
  open.className = 'jarvis-task-open-codex';
  open.href = deepLink;
  open.textContent = 'Open in Codex';
  controls.prepend(open);
}

function appendApprovalControls(row, event) {
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

function renderActivityEvent(event) {
  const task = rememberTask({
    ...(taskSnapshots.get(String(event.task_id)) || {}),
    task_id: String(event.task_id || ''),
    worker: event.worker,
  });
  if (!task || !taskVisible(task)) return null;
  const group = ensureActivityGroup(task);
  if (!group) return null;
  if (event.type === 'accepted') return group;
  if (event.type === 'result') {
    setActivityStatus(group, task, 'completed');
    return group;
  }

  const history = group.querySelector('.jarvis-task-activity-history');
  const eventKey = activityEventKey(event);
  if (!history || Array.from(history.children).some(row => (
    row.dataset.eventKey === eventKey
    || String(row.dataset.eventKeys || '').split(/\s+/).includes(eventKey)
  ))) {
    return group;
  }

  if (event.type === 'tool_activity') {
    ensureTaskDeepLink(group, event);
    const last = history.lastElementChild;
    const row = last?.dataset.eventType === 'tool_activity' ? last : document.createElement('div');
    if (row !== last) {
      row.className = 'jarvis-task-tool-row';
      row.dataset.eventType = 'tool_activity';
      row.dataset.eventKey = eventKey;
      row._labels = [];
      row._rawEvents = [];
      history.appendChild(row);
    }
    const label = toolActivityLabel(event);
    if (!row._labels.includes(label)) row._labels.push(label);
    row._rawEvents.push(String(event.text || label));
    row.textContent = row._labels.map((value, index) => index ? value.toLowerCase() : value).join(', ');
    row.title = row._rawEvents.join('\n');
    row.dataset.eventKeys = `${row.dataset.eventKeys || ''} ${eventKey}`.trim();
    return group;
  }

  const row = document.createElement('article');
  row.className = `jarvis-task-activity-event is-${event.type || 'progress'}`;
  row.dataset.eventType = event.type || 'progress';
  row.dataset.eventKey = eventKey;
  if (event.metadata?.milestone === true) row.classList.add('is-milestone');
  const text = document.createElement('p');
  text.textContent = event.text || '';
  row.appendChild(text);
  appendActivityAction(row, event);
  if (event.type === 'approval_required') appendApprovalControls(row, event);
  history.appendChild(row);

  if (event.type === 'error') setActivityStatus(group, task, 'failed');
  else if (event.type === 'cancelled') setActivityStatus(group, task, 'cancelled');
  else if (event.type === 'question') setActivityStatus(group, task, 'waiting');
  else if (event.type === 'approval_required') setActivityStatus(group, task, 'waiting_approval');
  else setActivityStatus(group, task, 'running');
  return group;
}

function findWorkerSummary(taskId, eventId, text) {
  return taskSummaryElements(taskId).find(item => {
    if (eventId) return item.dataset.workerEventId === eventId;
    return String(item.querySelector('.body')?.textContent || '').trim() === text;
  });
}

function renderWorkerSummary(event, task) {
  const metadata = event.metadata || {};
  const text = String(event.spoken_text || '').trim();
  const isBrokerSummary = event.type === 'progress'
    && (metadata.progress_summary === true || metadata.milestone === true);
  if (!isBrokerSummary || !text || !taskVisible(task)) return null;

  const eventId = String(event.event_id || '').trim();
  const existing = findWorkerSummary(event.task_id, eventId, text);
  if (existing) return existing;

  const summary = window.chatModule?.addMessage?.('assistant', text, '', {
    source: 'jarvis_worker_summary',
    worker: event.worker,
    task_id: event.task_id,
    worker_event_id: eventId,
    character_name: 'Jarvis',
  }) || null;
  if (summary && eventId) summary.dataset.workerEventId = eventId;
  window.uiModule?.scrollHistory?.();
  return summary;
}

async function openWorkerArtifact(event) {
  const documentId = event.metadata?.document_id;
  if (!documentId || !window.documentModule?.loadDocument) return;
  if (isActive) setAgentWorkspaceActive(true);
  await window.documentModule.loadDocument(documentId, { side: 'left' });
}

async function submitWorkerApproval(event, choice, row = null, spokenText = '') {
  if (!event?.task_id) return;
  await fetchJson(`/api/agent-tasks/${encodeURIComponent(event.task_id)}/approval`, {
    method: 'POST',
    body: JSON.stringify({ choice, spoken_text: spokenText || null }),
  });
  row?.querySelectorAll('button').forEach(button => { button.disabled = true; });
  showToast(choice === 'deny' ? 'Worker action denied.' : 'Worker action approved.');
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

function enqueueSpeech(text, type = 'speech', source = 'jarvis', timings = {}) {
  const clean = (window.aiTTSManager?.extractPlainText?.(text) || text || '').trim();
  if (!clean) return;
  const key = clean.toLowerCase().replace(/\s+/g, ' ');
  if (speechQueue.some(item => item.key === key) || currentSpeech?.key === key) return;
  speechQueue.push({ text: clean, type, source, key, timings });
  if (!speechPaused) processSpeechQueue().catch(handleError);
}

function workerSpeech(event) {
  const label = WORKER_LABELS[event.worker] || event.worker || 'Worker';
  const source = event.type === 'result'
    ? (event.spoken_text || `${label} finished. The full result is in chat.`)
    : event.type === 'progress'
      ? (event.spoken_text || '')
      : (event.text || '');
  const clean = (window.aiTTSManager?.extractPlainText?.(source) || source).trim();
  if (clean.length <= WORKER_SPEECH_MAX_CHARS) return clean;
  const clipped = clean.slice(0, WORKER_SPEECH_MAX_CHARS - 1);
  const boundary = Math.max(clipped.lastIndexOf('. '), clipped.lastIndexOf(' '));
  return `${clipped.slice(0, boundary > 400 ? boundary + 1 : clipped.length).trim()}…`;
}

function pauseCaptureForSpeech() {
  if (!mediaRecorder || mediaRecorder.state !== 'recording') return;
  discardRecordingGeneration = voiceCallGeneration;
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
      stopPlaybackAudio();
      resumeListeningIfReady();
    }
  }
}

async function handleWorkerEvent(event) {
  const eventId = String(event.event_id || '').trim();
  if (eventId && handledWorkerEventIds.has(eventId)) return;
  if (eventId) handledWorkerEventIds.add(eventId);
  const taskId = String(event.task_id || '');
  const prior = taskSnapshots.get(taskId) || {};
  const events = Array.isArray(prior.events) ? [...prior.events] : [];
  if (!events.some(item => activityEventKey(item) === activityEventKey(event))) events.push(event);
  const task = rememberTask({
    ...prior,
    task_id: taskId,
    worker: event.worker || prior.worker,
    events,
    updated_at: event.created_at || Date.now() / 1000,
  });
  const eventBelongsToActiveVoiceTask = isActive
    && taskId === activeWorkerTaskId
    && task?.session_id === chatSessionId;
  if (event.metadata?.codex_thread_id && eventBelongsToActiveVoiceTask) {
    activeCodexThreadId = event.metadata.codex_thread_id;
    activeWorkspace = event.metadata.workspace || activeWorkspace;
    await persistVoiceTarget({ codex_thread_id: activeCodexThreadId }).catch(() => {});
  }
  if (taskVisible(task)) {
    renderActivityEvent(event);
    renderWorkerSummary(event, task);
  }
  if (event.type === 'artifact' && isActive) await openWorkerArtifact(event);
  if (event.type === 'result'
      && event.text
      && taskVisible(task)) {
    let result = taskMessageElements(taskId).find(item => item.dataset.source === 'agent_worker');
    if (!result) {
      result = window.chatModule?.addMessage?.('assistant', event.text, '', {
        source: 'agent_worker',
        worker: event.worker,
        task_id: taskId,
        character_name: WORKER_LABELS[event.worker] || event.worker || 'Worker',
      });
    }
    positionWorkerResult(result, taskId);
    window.uiModule?.scrollHistory?.();
  }
  if (isActive
      && SPOKEN_WORKER_EVENTS.has(event.type)
      && (event.type !== 'progress' || Boolean(event.spoken_text))) {
    enqueueSpeech(workerSpeech(event), event.type, event.worker || 'worker');
  }
  if (['result', 'error', 'cancelled'].includes(event.type)) {
    if (task) {
      task.status = event.type === 'result' ? 'completed' : (event.type === 'error' ? 'failed' : 'cancelled');
      task.updated_at = event.created_at || Date.now() / 1000;
      const group = taskActivityElement(taskId);
      if (group) setActivityStatus(group, task, task.status);
    }
    const stream = workerStreams.get(taskId);
    stream?.close();
    workerStreams.delete(taskId);
    if (activeWorkerTaskId === taskId) {
      activeWorkerTaskId = null;
      await persistVoiceTarget({ task_id: '' }).catch(() => {});
    }
    if (isActive) setAgentWorkspaceActive(voiceTarget !== 'jarvis' || activeTaskCount() > 0);
  }
  refreshAgentControl();
}

function queueWorkerEvent(event) {
  const taskId = String(event.task_id || 'unbound');
  const previous = workerEventChains.get(taskId) || Promise.resolve();
  const queued = previous
    .catch(() => {})
    .then(() => handleWorkerEvent(event))
    .catch(error => console.warn('Worker event handling failed:', error));
  workerEventChains.set(taskId, queued);
  queued.then(() => {
    if (workerEventChains.get(taskId) === queued) workerEventChains.delete(taskId);
  });
}

function followWorkerTask(taskId, affectVoiceLayout = true) {
  if (!taskId || workerStreams.has(taskId)) return;
  if (isActive && affectVoiceLayout) setAgentWorkspaceActive(true);
  const stream = new EventSource(`/api/agent-tasks/${encodeURIComponent(taskId)}/events`);
  workerStreams.set(taskId, stream);
  refreshAgentControl();
  stream.onmessage = message => {
    try {
      const event = JSON.parse(message.data);
      if (!event.event_id && message.lastEventId) event.event_id = `${taskId}:${message.lastEventId}`;
      queueWorkerEvent(event);
    } catch (error) { console.warn('Worker event parse failed:', error); }
  };
  stream.onerror = refreshAgentControl;
}

async function restoreSessionTasks(targetSessionId) {
  const sessionIdToRestore = String(targetSessionId || '');
  if (!sessionIdToRestore || currentChatSessionId() !== sessionIdToRestore) return;
  const revision = ++activityRestoreRevision;
  const taskIds = new Set(
    Array.from(document.querySelectorAll('#chat-history .msg[data-task-id]'))
      .map(item => item.dataset.taskId)
      .filter(Boolean),
  );
  if (!taskIds.size) return;

  const snapshots = await Promise.all(Array.from(taskIds, async taskId => {
    try { return await fetchJson(`/api/agent-tasks/${encodeURIComponent(taskId)}`); }
    catch (error) {
      console.warn(`Could not restore worker task ${taskId}:`, error);
      return null;
    }
  }));
  if (revision !== activityRestoreRevision || currentChatSessionId() !== sessionIdToRestore) return;

  for (const snapshot of snapshots.filter(Boolean)) {
    if (snapshot.session_id !== sessionIdToRestore) continue;
    const task = rememberTask(snapshot);
    const group = ensureActivityGroup(task);
    const events = [...(snapshot.events || [])].sort((left, right) => Number(left.seq || 0) - Number(right.seq || 0));
    events.forEach(event => {
      if (event.event_id) handledWorkerEventIds.add(String(event.event_id));
      renderActivityEvent(event);
    });
    if (group) {
      setActivityStatus(group, task, task.status || 'running');
      positionActivityGroup(group);
      const result = taskMessageElements(task.task_id).find(item => item.dataset.source === 'agent_worker');
      if (result) positionWorkerResult(result, task.task_id);
    }
    if (!TERMINAL_TASK_STATES.has(task.status || '')) followWorkerTask(task.task_id);
  }
}

async function cancelActiveWorkerTask() {
  const taskId = activeWorkerTaskId;
  if (!taskId) return;
  setAgentMenuOpen(false);
  await cancelWorkerTask(taskId);
}

function renderLiveUser(text, timings, turnStarted) {
  if (window.sessionModule?.getCurrentSessionId?.() !== chatSessionId) return;
  window.chatModule?.addMessage?.('user', text, '', { source: 'jarvis_voice_live' });
  timings.chat_user_render_ms = performance.now() - turnStarted;
  window.uiModule?.scrollHistory?.();
}

function applyLiveTaskMetadata(message, task) {
  if (!message || !task?.task_id) return;
  message.dataset.source = 'jarvis_voice_live';
  message.dataset.taskId = String(task.task_id);
  if (task.worker) message.dataset.worker = String(task.worker);
  const group = taskActivityElement(task.task_id);
  if (group) positionActivityGroup(group);
}

function appendLiveAssistant(delta, model = '', task = null) {
  if (!delta || window.sessionModule?.getCurrentSessionId?.() !== chatSessionId) return;
  if (!liveAssistantMessage) {
    liveAssistantMessage = window.chatModule?.addMessage?.('assistant', delta, model, {
      source: 'jarvis_voice_live',
      task_id: task?.task_id,
      worker: task?.worker,
    }) || null;
    if (liveAssistantMessage) liveAssistantMessage.dataset.raw = delta;
  } else {
    liveAssistantMessage.dataset.raw = (liveAssistantMessage.dataset.raw || '') + delta;
    const body = liveAssistantMessage.querySelector('.body');
    if (body) body.innerHTML = markdownModule.processWithThinking(markdownModule.squashOutsideCode(liveAssistantMessage.dataset.raw));
  }
  applyLiveTaskMetadata(liveAssistantMessage, task);
  window.uiModule?.scrollHistory?.();
}

async function postPlaybackState(turnId, state, timings = {}, voiceSessionId = sessionId) {
  if (!voiceSessionId || !turnId) return;
  await fetchJson(`/api/voice/sessions/${encodeURIComponent(voiceSessionId)}/turns/${encodeURIComponent(turnId)}/playback`, {
    method: 'POST',
    body: JSON.stringify({ state, timings }),
  }).catch(error => console.warn('Could not update Jarvis playback state:', error));
}

async function playVoiceTurnAudio(turnId, timings, voiceSessionId) {
  const token = ++playbackToken;
  return playBufferedAudio(
    `/api/voice/sessions/${encodeURIComponent(voiceSessionId)}/turns/${encodeURIComponent(turnId)}/audio`,
    {}, timings, token, turnId, voiceSessionId,
  );
}

async function streamTurn(text, timings, turnStarted, callGeneration) {
  if (!sessionId) await createSession(callGeneration);
  if (!isCurrentVoiceCall(callGeneration) || !sessionId) throw new Error('Voice call ended.');
  const turnSessionId = sessionId;
  const turnChatSessionId = chatSessionId;
  liveAssistantMessage = null;
  const turnTasks = [];
  const response = await fetch(`/api/voice/sessions/${encodeURIComponent(turnSessionId)}/respond/stream`, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...browserTimezoneHeaders() },
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
        if (isCurrentVoiceCall(callGeneration)) {
          const delta = event.text || '';
          appendLiveAssistant(delta, event.model || 'Jarvis', turnTasks[0] || null);
          if (timings.chat_assistant_first_render_ms == null) timings.chat_assistant_first_render_ms = performance.now() - turnStarted;
        }
      }
      else if (event.type === 'audio_ready') {
        if (!isCurrentVoiceCall(callGeneration)) {
          turnAudioPromise = Promise.resolve();
        } else {
          activeAudioTurnId = event.turn_id;
          setStatus('buffering');
          const promise = playVoiceTurnAudio(event.turn_id, timings, turnSessionId);
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
      }
      else if (event.type === 'state' && event.state !== 'listening' && isCurrentVoiceCall(callGeneration)) setStatus(event.state);
      else if (event.type === 'target_changed') {
        if (isCurrentVoiceCall(callGeneration)) {
          activeWorkspace = event.workspace || activeWorkspace;
          setVoiceTarget(event.target || 'jarvis', false);
        }
      }
      else if (event.type === 'agent_task') {
        const currentCall = isCurrentVoiceCall(callGeneration);
        const taskWorkspace = event.workspace || (currentCall ? activeWorkspace : 'home-lab');
        const existingTask = taskSnapshots.get(String(event.task_id || ''));
        const task = rememberTask({
          ...(existingTask || {}),
          task_id: event.task_id,
          session_id: turnChatSessionId,
          worker: event.worker || 'pc-codex',
          workspace: taskWorkspace,
          status: 'running',
          created_at: existingTask?.created_at || Date.now() / 1000,
          updated_at: Date.now() / 1000,
          events: existingTask?.events || [],
        });
        if (task && !turnTasks.some(item => item.task_id === task.task_id)) turnTasks.push(task);
        ensureActivityGroup(task);
        if (currentCall) {
          activeWorkerTaskId = event.task_id;
          activeWorkspace = taskWorkspace;
          if (event.foreground !== false) setVoiceTarget(event.worker || 'pc-codex', false);
          else setAgentWorkspaceActive(true);
          persistVoiceTarget({ task_id: activeWorkerTaskId }).catch(() => {});
          refreshAgentControl();
        }
        followWorkerTask(event.task_id, currentCall);
      }
      else if (event.type === 'final') {
        final = event;
        const finalTaskId = (event.task_ids || [])[0];
        const task = turnTasks.find(item => item.task_id === finalTaskId) || taskSnapshots.get(finalTaskId) || turnTasks[0];
        if (isCurrentVoiceCall(callGeneration)) applyLiveTaskMetadata(liveAssistantMessage, task);
      }
      else if (event.type === 'error') throw new Error(event.text || 'Jarvis brain request failed');
    }
    if (done) break;
  }
  if (!final) throw new Error('Jarvis returned no final response.');
  if (!turnAudioPromise && !isCurrentVoiceCall(callGeneration)) turnAudioPromise = Promise.resolve();
  if (!turnAudioPromise) throw new Error('Jarvis returned no audio stream.');
  return { ...final, audioPromise: turnAudioPromise, voiceSessionId: turnSessionId };
}

async function createSession(callGeneration = voiceCallGeneration) {
  const activeChatSessionId = window.sessionModule?.getCurrentSessionId?.() || null;
  const session = await fetchJson('/api/voice/sessions', {
    method: 'POST',
    headers: browserTimezoneHeaders(),
    body: JSON.stringify({ mode: 'jarvis_call', chat_session_id: activeChatSessionId }),
  });
  if (!isCurrentVoiceCall(callGeneration)) return null;
  sessionId = session.id;
  chatSessionId = session.chat_session_id || null;
  const savedTarget = session.target || 'jarvis';
  voiceTarget = 'jarvis';
  activeWorkspace = session.workspace || 'home-lab';
  activeWorkerTaskId = session.active_task_id || null;
  activeCodexThreadId = session.codex_thread_id || null;
  if (chatSessionId) await openLinkedChatSession(chatSessionId, callGeneration);
  if (!isCurrentVoiceCall(callGeneration)) return null;
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
    headers: browserTimezoneHeaders(),
    body: JSON.stringify({ text }),
  });
}

async function postTurnDiagnostics(timings, voiceSessionId = sessionId) {
  if (!voiceSessionId) return;
  await fetchJson(`/api/voice/sessions/${encodeURIComponent(voiceSessionId)}/diagnostics`, {
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
  stopPlaybackAudio();
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

function stopPlaybackAudio() {
  playbackAbortController?.abort();
  playbackAbortController = null;
  resolvePlaybackWait();
  playbackAudioSources.forEach(source => {
    try { source.stop(); } catch {}
  });
  playbackAudioSources.clear();
  stopSphereAudio();
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

function startSilenceWatch(stream, callGeneration) {
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
  captureVoicedMs = 0;

  silenceTimer = setInterval(() => {
    analyser.getByteTimeDomainData(data);
    let sum = 0;
    for (let i = 0; i < data.length; i += 1) {
      const normalized = (data[i] - 128) / 128;
      sum += normalized * normalized;
    }
    const rms = Math.sqrt(sum / data.length);
    if (rms > VOICE_RMS_THRESHOLD) {
      heardVoice = true;
      lastVoiceAt = Date.now();
      captureVoicedMs += VOICE_SAMPLE_INTERVAL_MS;
    }
    if (heardVoice && Date.now() - lastVoiceAt > 1200 && isCurrentVoiceCall(callGeneration)) {
      stopListening();
    }
  }, VOICE_SAMPLE_INTERVAL_MS);

  maxTurnTimer = setTimeout(() => {
    if (isCurrentVoiceCall(callGeneration)) stopListening();
  }, 30000);
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
  const callGeneration = voiceCallGeneration;

  const recordingChunks = [];
  isStopping = false;
  let requestedStream;
  try {
    requestedStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
        channelCount: 1,
      },
    });
  } catch (error) {
    if (!isCurrentVoiceCall(callGeneration)) return;
    throw error;
  }
  if (!isCurrentVoiceCall(callGeneration)) {
    requestedStream.getTracks().forEach(track => track.stop());
    return;
  }
  mediaStream = requestedStream;
  startSphereStream(mediaStream);
  mediaRecorder = new MediaRecorder(mediaStream, { mimeType: 'audio/webm' });

  mediaRecorder.ondataavailable = event => {
    if (event.data?.size) recordingChunks.push(event.data);
  };

  mediaRecorder.onstop = async () => {
    if (!isCurrentVoiceCall(callGeneration)) {
      requestedStream.getTracks().forEach(track => track.stop());
      return;
    }
    clearTurnTimers();
    stopTracks();
    isStopping = false;

    if (discardRecordingGeneration === callGeneration) {
      discardRecordingGeneration = null;
      captureVoicedMs = 0;
      return;
    }

    const blob = new Blob(recordingChunks, { type: 'audio/webm' });
    if (!blob.size) {
      setStatus('idle');
      return;
    }
    if (captureVoicedMs < MIN_VOICED_MS) {
      captureVoicedMs = 0;
      setStatus('listening', 'No speech detected.');
      window.setTimeout(() => {
        if (isCurrentVoiceCall(callGeneration)) startListening().catch(handleError);
      }, 400);
      return;
    }
    captureVoicedMs = 0;

    try {
      const turnStarted = performance.now();
      setStatus('transcribing');
      const timings = { turn_started_at: turnStarted };
      const sttStarted = performance.now();
      const text = await transcribe(blob);
      timings.stt_ms = performance.now() - sttStarted;
      timings.transcript_chars = text.length;
      if (!isCurrentVoiceCall(callGeneration)) return;
      const transcriptEl = $('jarvis-call-transcript');
      if (transcriptEl) transcriptEl.textContent = text || '';
      if (!text) {
        setStatus('listening', 'No speech detected.');
        window.setTimeout(() => {
          if (isCurrentVoiceCall(callGeneration)) startListening().catch(handleError);
        }, 800);
        return;
      }
      renderLiveUser(text, timings, turnStarted);
      playVoiceCue('heard');

      speechPaused = false;
      playVoiceCue('thinking', 0.1);
      setStatus('thinking');
      brainTurnInProgress = true;
      const brainStarted = performance.now();
      const response = await streamTurn(text, timings, turnStarted, callGeneration);
      if (!isCurrentVoiceCall(callGeneration)) return;
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
      if (!isCurrentVoiceCall(callGeneration)) return;
      if (activeTurnAudioPromise === response.audioPromise) activeTurnAudioPromise = null;
      activeAudioTurnId = null;
      if (!speechQueueRunning && speechQueue.length) processSpeechQueue().catch(handleError);
      await waitForSpeechQueueIdle();
      if (!isCurrentVoiceCall(callGeneration)) return;
      stopPlaybackAudio();
      delete timings.turn_started_at;
      await postTurnDiagnostics(timings, response.voiceSessionId);
      if (!isCurrentVoiceCall(callGeneration)) return;
      resumeListeningIfReady();
    } catch (error) {
      if (!isCurrentVoiceCall(callGeneration)) return;
      brainTurnInProgress = false;
      handleError(error);
    }
  };

  mediaRecorder.start();
  setStatus('listening');
  startSilenceWatch(mediaStream, callGeneration);
}

async function ensurePlaybackContext() {
  const AudioContext = window.AudioContext || window.webkitAudioContext;
  if (!AudioContext) throw new Error('Audio playback is not supported by this browser.');
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
  }
  const context = sphereAudioContext;
  await context.resume();
  return context;
}

async function playBufferedAudio(url, options, timings, token, turnId = null, voiceSessionId = sessionId) {
  const started = performance.now();
  const controller = new AbortController();
  playbackAbortController = controller;
  try {
    const response = await fetch(url, {
      credentials: 'same-origin',
      ...options,
      signal: controller.signal,
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body?.detail?.message || body?.detail || body?.message || 'Audio synthesis failed');
    }
    const encodedAudio = await response.arrayBuffer();
    if (!encodedAudio.byteLength) throw new Error('Audio synthesis returned no audio.');
    if (token !== playbackToken) return null;

    const context = await ensurePlaybackContext();
    const audioBuffer = await context.decodeAudioData(encodedAudio.slice(0));
    if (token !== playbackToken) return null;

    timings.tts_chunks = 1;
    timings.tts_blocks = 1;
    timings.scheduler_underruns = 0;
    timings.tts_generation_ms = performance.now() - started;
    timings.playback_duration_ms = audioBuffer.duration * 1000;
    timings.tts_first_audio_ms = performance.now() - started;
    if (timings.turn_started_at != null) timings.end_to_first_audio_ms = performance.now() - timings.turn_started_at;

    const source = context.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(sphereAnalyser);
    playbackAudioSources.add(source);
    setStatus('speaking');
    if (turnId) postPlaybackState(turnId, 'started', timings, voiceSessionId);

    await new Promise((resolve, reject) => {
      let settled = false;
      const finish = () => {
        if (settled) return;
        settled = true;
        playbackAudioSources.delete(source);
        if (playbackWaitResolve === finish) playbackWaitResolve = null;
        try { source.disconnect(); } catch {}
        resolve();
      };
      playbackWaitResolve = finish;
      source.onended = finish;
      try { source.start(); } catch (error) { reject(error); }
    });

    timings.tts_total_ms = performance.now() - started;
    if (turnId && token === playbackToken) await postPlaybackState(turnId, 'completed', timings, voiceSessionId);
    return { audio_ms: timings.playback_duration_ms, blocks: 1 };
  } catch (error) {
    if (token !== playbackToken || error?.name === 'AbortError') return null;
    if (turnId) await postPlaybackState(turnId, 'failed', timings, voiceSessionId);
    speechQueue = [];
    currentSpeech = null;
    stopPlaybackAudio();
    setStatus('failed', error.message || 'Audio playback failed.');
    throw error;
  } finally {
    if (playbackAbortController === controller) playbackAbortController = null;
  }
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
  await playBufferedAudio('/api/tts/synthesize', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, format: 'audio', use_cache: false }),
  }, timings, token);
}

function handleError(error) {
  console.error('Jarvis voice error:', error);
  activeTurnAudioPromise = null;
  activeAudioTurnId = null;
  brainTurnInProgress = false;
  speechQueue = [];
  currentSpeech = null;
  clearTurnTimers();
  stopPlaybackAudio();
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

  const callGeneration = ++voiceCallGeneration;
  isActive = true;
  speechPaused = false;
  speechQueue = [];
  brainTurnInProgress = false;
  activeWorkerTaskId = null;
  activeCodexThreadId = null;
  activeWorkspace = 'home-lab';
  liveAssistantMessage = null;
  activeTurnAudioPromise = null;
  activeAudioTurnId = null;
  setAgentWorkspaceActive(activeTaskCount() > 0);
  positionVisibleActivityGroups();
  setStatus('idle', 'Connecting…');
  const callCue = playVoiceCue('call');
  try {
    await createSession(callGeneration);
    if (!isCurrentVoiceCall(callGeneration)) return;
    await callCue;
    if (!isCurrentVoiceCall(callGeneration)) return;
    setStatus('idle');
    prewarmVoiceStack();
    await startListening();
  } catch (error) {
    if (isCurrentVoiceCall(callGeneration)) handleError(error);
  }
}

function endCall() {
  const continuedTasks = activeTaskCount();
  voiceCallGeneration += 1;
  isActive = false;
  restoreActivityGroupsToChat();
  brainTurnInProgress = false;
  activeTurnAudioPromise = null;
  activeAudioTurnId = null;
  speechPaused = true;
  speechQueue = [];
  currentSpeech = null;
  setAgentWorkspaceActive(false);
  playbackToken += 1;
  clearTurnTimers();
  resolvePlaybackWait();
  stopPlaybackAudio();
  if (window.aiTTSManager) window.aiTTSManager.stop();
  closeVoiceCueAudio();
  stopSphereAudio();
  stopListening();
  stopTracks();
  setStatus('idle');
  unmountOrganicSphere();
  const panel = $('jarvis-call-panel');
  if (panel) panel.hidden = true;
  sessionId = null;
  if (!continuedTasks) chatSessionId = null;
  voiceTarget = 'jarvis';
  if (!continuedTasks) activeWorkerTaskId = null;
  liveAssistantMessage = null;
  if (continuedTasks) showToast(`Voice ended. ${continuedTasks === 1 ? 'The active task continues' : `${continuedTasks} active tasks continue`}.`);
}

async function openLinkedChatSession(linkedChatSessionId, callGeneration = voiceCallGeneration) {
  try {
    if (!window.sessionModule?.selectSession) return;
    if (!isCurrentVoiceCall(callGeneration)) return;
    if (window.sessionModule.loadSessions) await window.sessionModule.loadSessions();
    if (!isCurrentVoiceCall(callGeneration)) return;
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
  const cancelTaskBtn = $('jarvis-agent-cancel');
  const agentSelector = document.querySelector('.jarvis-agent-selector');
  const targetButtons = document.querySelectorAll('.jarvis-target');

  if (railBtn) {
    railBtn.innerHTML = ICON_PHONE;
    railBtn.addEventListener('click', toggleCall);
  }
  if (closeBtn) {
    closeBtn.innerHTML = ICON_CLOSE;
    closeBtn.title = END_VOICE_LABEL;
    closeBtn.setAttribute('aria-label', END_VOICE_LABEL);
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
  cancelTaskBtn?.addEventListener('click', () => {
    cancelActiveWorkerTask().catch(error => showToast(error.message || 'Could not cancel the task.'));
  });
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
  window.addEventListener('odysseus:session-rendered', event => {
    restoreSessionTasks(event.detail?.sessionId).catch(error => {
      console.warn('Could not restore Jarvis task activity:', error);
    });
  });

  window.addEventListener('message', handleSphereMessage);
  setVoiceTarget('jarvis');
  loadWorkerCatalog().catch(() => {});
  setStatus('idle');
  const currentSession = currentChatSessionId();
  if (currentSession) restoreSessionTasks(currentSession).catch(() => {});
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', bind);
} else {
  bind();
}

window.jarvisVoice = { startCall, endCall, interrupt, isActive: isCallActive, restoreSessionTasks };
