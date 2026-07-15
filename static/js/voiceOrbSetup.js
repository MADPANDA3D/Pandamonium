// Compact, text-only Voice Orb setup status and explicit Tailnet inspection.

const FIXED_WORKERS = [
  ['pc-codex', 'PC Codex'],
  ['hermes', 'Hermes'],
  ['vps-codex', 'VPS Codex'],
];
const PEER_ID = /^[0-9a-f]{32}$/;
const LOGICAL_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
const MODEL_ID = /^[A-Za-z0-9][A-Za-z0-9._+:/-]{0,127}$/;
const PEER_OS = new Set(['android', 'darwin', 'freebsd', 'ios', 'linux', 'windows']);
const PEER_STATUS = new Set(['online', 'offline']);
const PROVIDERS = new Set([
  'openai-compatible',
  'llamacpp-compatible',
  'lmstudio-compatible',
  'ollama',
]);
const MAX_PEERS = 32;
const MAX_SELECTION = 5;
const MAX_CANDIDATES = 16;
const MAX_MODELS = 16;
const MAX_CAPABILITIES = 12;

const peerSelections = new Map();
let initialized = false;

const $ = id => document.getElementById(id);

function safeLogicalNames(value, limit = MAX_CAPABILITIES) {
  if (!Array.isArray(value)) return [];
  return [...new Set(value
    .map(item => String(item || '').trim())
    .filter(item => LOGICAL_NAME.test(item)))]
    .slice(0, limit);
}

function safeModelId(value) {
  const model = String(value || '').trim();
  const rawAddress = /(?:^|[/:])\d{1,3}(?:\.\d{1,3}){3}(?:[/:]|$)/.test(model);
  const windowsPath = /^[A-Za-z]:\//.test(model);
  const manyColons = (model.match(/:/g) || []).length > 1;
  return MODEL_ID.test(model)
    && !model.includes('://')
    && !model.startsWith('/')
    && !rawAddress
    && !windowsPath
    && !manyColons
    ? model
    : '';
}

function providerLabel(value) {
  const provider = String(value || '').trim().toLowerCase();
  return ['disabled', 'endpoint', 'browser', 'local', 'configured'].includes(provider)
    ? provider.replaceAll('_', ' ')
    : 'unknown';
}

function modelSelectionLabel(value) {
  return {
    default: 'default model',
    endpoint_override: 'endpoint override',
    model_override: 'model override',
  }[String(value || '')] || 'configured model';
}

function setText(id, value) {
  const node = $(id);
  if (node) node.textContent = String(value || '');
}

function renderFixedWorkers(workers) {
  const list = $('voice-orb-setup-workers');
  if (!list) return;
  list.replaceChildren();
  const rawItems = Array.isArray(workers?.items) ? workers.items.slice(0, 16) : [];
  const items = new Map();
  for (const item of rawItems) {
    if (item && FIXED_WORKERS.some(([id]) => id === item.id) && !items.has(item.id)) {
      items.set(item.id, item);
    }
  }

  let configuredCount = 0;
  let readyCount = 0;
  for (const [id, label] of FIXED_WORKERS) {
    const worker = items.get(id) || {};
    const configured = worker.configured === true;
    const ready = configured && worker.ready === true;
    if (configured) configuredCount += 1;
    if (ready) readyCount += 1;

    const row = document.createElement('li');
    row.className = 'voice-orb-setup-worker';
    row.dataset.state = ready ? 'ready' : (configured ? 'unavailable' : 'not-configured');
    const heading = document.createElement('div');
    const name = document.createElement('strong');
    name.textContent = label;
    const state = document.createElement('span');
    state.textContent = ready ? 'Ready' : (configured ? 'Unavailable' : 'Not configured');
    heading.append(name, state);
    const capabilities = document.createElement('p');
    const names = safeLogicalNames(worker.capabilities);
    capabilities.textContent = names.length
      ? `Capabilities: ${names.join(', ')}`
      : 'Capabilities: none reported';
    row.append(heading, capabilities);
    list.appendChild(row);
  }

  const summary = readyCount >= 2
    ? `Fixed worker cluster: ${readyCount} ready of ${configuredCount} configured.`
    : readyCount === 1
      ? '1 fixed worker is ready.'
      : 'No fixed workers are ready.';
  setText('voice-orb-setup-workers-summary', summary);
}

export function renderVoiceSetup(setup, { reveal = false } = {}) {
  if (!setup || typeof setup !== 'object' || Array.isArray(setup)) return;
  const coreReady = setup.core_ready === true;
  const summary = $('voice-orb-setup-summary');
  if (summary) {
    summary.textContent = coreReady ? 'Core ready' : 'Needs setup';
    summary.dataset.state = coreReady ? 'ready' : 'attention';
  }

  // The backend uses this exact text for speech; preserve it verbatim in the UI.
  if (typeof setup.text === 'string') setText('voice-orb-setup-text', setup.text);

  const model = setup.model && typeof setup.model === 'object' ? setup.model : {};
  setText(
    'voice-orb-setup-model',
    model.configured === true ? `Ready · ${modelSelectionLabel(model.selection)}` : 'Not configured',
  );
  const stt = setup.speech_to_text && typeof setup.speech_to_text === 'object'
    ? setup.speech_to_text
    : {};
  setText(
    'voice-orb-setup-stt',
    `${stt.available === true ? 'Ready' : 'Unavailable'} · ${providerLabel(stt.provider)}`,
  );
  const tts = setup.text_to_speech && typeof setup.text_to_speech === 'object'
    ? setup.text_to_speech
    : {};
  setText(
    'voice-orb-setup-tts',
    `${tts.available === true ? 'Ready' : 'Unavailable'} · ${providerLabel(tts.provider)}`,
  );
  renderFixedWorkers(setup.workers);

  const root = $('voice-orb-setup');
  if (reveal && root) root.open = true;
}

async function fetchStatus(path) {
  const response = await fetch(path, {
    method: 'GET',
    credentials: 'same-origin',
    cache: 'no-store',
    headers: { Accept: 'application/json' },
  });
  if (!response.ok) throw new Error('request failed');
  const data = await response.json().catch(() => null);
  if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error('invalid response');
  return data;
}

function updateProbeButton(changedInput = null) {
  const selected = [...peerSelections.values()].filter(input => input.checked);
  if (selected.length > MAX_SELECTION && changedInput) {
    changedInput.checked = false;
    setText('voice-orb-tailnet-status', `Select up to ${MAX_SELECTION} peers.`);
  }
  const count = [...peerSelections.values()].filter(input => input.checked).length;
  const button = $('voice-orb-tailnet-probe');
  if (button) button.disabled = count === 0 || count > MAX_SELECTION;
}

function renderTailnetPeers(data) {
  const list = $('voice-orb-tailnet-peers');
  if (!list) return 0;
  list.replaceChildren();
  peerSelections.clear();
  const seen = new Set();
  const rawPeers = Array.isArray(data.peers) ? data.peers.slice(0, MAX_PEERS) : [];
  const peers = rawPeers.filter(peer => {
    const peerId = String(peer?.id || '');
    if (!PEER_ID.test(peerId) || seen.has(peerId)) return false;
    seen.add(peerId);
    return true;
  });

  peers.forEach((peer, index) => {
    const peerId = String(peer.id);
    const row = document.createElement('li');
    row.className = 'voice-orb-tailnet-peer';
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.id = `voice-orb-tailnet-peer-${index}`;
    input.value = peerId;
    input.addEventListener('change', () => updateProbeButton(input));
    peerSelections.set(peerId, input);

    const label = document.createElement('label');
    label.htmlFor = input.id;
    const idNode = document.createElement('code');
    idNode.textContent = peerId;
    const meta = document.createElement('span');
    const os = String(peer.os || '').toLowerCase();
    const status = String(peer.status || '').toLowerCase();
    meta.textContent = `${PEER_OS.has(os) ? os : 'unknown'} · ${PEER_STATUS.has(status) ? status : 'unknown'}`;
    label.append(idNode, meta);
    row.append(input, label);
    list.appendChild(row);
  });
  updateProbeButton();
  return peers.length;
}

function renderTailnetCandidates(data, selectedPeerIds) {
  const list = $('voice-orb-tailnet-results');
  if (!list) return 0;
  list.replaceChildren();
  const selected = new Set(selectedPeerIds);
  const rawCandidates = Array.isArray(data.candidates)
    ? data.candidates.slice(0, MAX_CANDIDATES)
    : [];
  let rendered = 0;

  for (const candidate of rawCandidates) {
    const peerId = String(candidate?.peer_id || '');
    const provider = String(candidate?.provider || '').toLowerCase();
    if (!selected.has(peerId) || !PEER_ID.test(peerId) || !PROVIDERS.has(provider)) continue;
    const models = [...new Set((Array.isArray(candidate.models) ? candidate.models : [])
      .map(safeModelId)
      .filter(Boolean))]
      .slice(0, MAX_MODELS);
    if (!models.length) continue;

    const row = document.createElement('li');
    row.className = 'voice-orb-tailnet-result';
    const heading = document.createElement('p');
    const idNode = document.createElement('code');
    idNode.textContent = peerId;
    const detail = document.createElement('span');
    detail.textContent = `${provider} · ${models.length} model${models.length === 1 ? '' : 's'}`;
    heading.append(idNode, detail);
    const modelList = document.createElement('ul');
    models.forEach(model => {
      const item = document.createElement('li');
      item.textContent = model;
      modelList.appendChild(item);
    });
    const capabilities = safeLogicalNames(candidate.capabilities);
    if (capabilities.length) {
      const note = document.createElement('p');
      note.textContent = `Capabilities: ${capabilities.join(', ')}`;
      row.append(heading, modelList, note);
    } else {
      row.append(heading, modelList);
    }
    list.appendChild(row);
    rendered += 1;
  }
  return rendered;
}

export async function inspectTailnetPeers() {
  const button = $('voice-orb-tailnet-list');
  const probe = $('voice-orb-tailnet-probe');
  if (button) button.disabled = true;
  if (probe) probe.disabled = true;
  $('voice-orb-tailnet-results')?.replaceChildren();
  setText('voice-orb-tailnet-status', 'Inspecting Tailnet peers…');
  try {
    const data = await fetchStatus('/api/discover?mode=tailnet_peers');
    const count = renderTailnetPeers(data);
    setText(
      'voice-orb-tailnet-status',
      count
        ? `${count} peer${count === 1 ? '' : 's'} available. Select up to ${MAX_SELECTION}, then probe.`
        : 'No selectable Tailnet peers were returned.',
    );
  } catch {
    peerSelections.clear();
    $('voice-orb-tailnet-peers')?.replaceChildren();
    setText('voice-orb-tailnet-status', 'Tailnet inspection could not be completed.');
  } finally {
    if (button) button.disabled = false;
    updateProbeButton();
  }
}

export async function probeSelectedTailnetPeers() {
  const peerIds = [...peerSelections.entries()]
    .filter(([, input]) => input.checked)
    .map(([peerId]) => peerId)
    .slice(0, MAX_SELECTION);
  if (!peerIds.length) return;
  const button = $('voice-orb-tailnet-probe');
  if (button) button.disabled = true;
  setText('voice-orb-tailnet-status', 'Probing selected peers…');
  $('voice-orb-tailnet-results')?.replaceChildren();
  try {
    const params = new URLSearchParams({ mode: 'tailnet_probe' });
    peerIds.forEach(peerId => params.append('peer_id', peerId));
    const data = await fetchStatus(`/api/discover?${params.toString()}`);
    const count = renderTailnetCandidates(data, peerIds);
    setText(
      'voice-orb-tailnet-status',
      count
        ? `${count} model-server candidate${count === 1 ? '' : 's'} found.`
        : 'No model-server candidates were found on the selected peers.',
    );
  } catch {
    setText('voice-orb-tailnet-status', 'Selected peers could not be probed.');
  } finally {
    updateProbeButton();
  }
}

export function initVoiceOrbSetup() {
  if (initialized) return;
  initialized = true;
  $('voice-orb-tailnet-list')?.addEventListener('click', inspectTailnetPeers);
  $('voice-orb-tailnet-probe')?.addEventListener('click', probeSelectedTailnetPeers);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initVoiceOrbSetup, { once: true });
} else {
  initVoiceOrbSetup();
}

export default { renderVoiceSetup, initVoiceOrbSetup };
