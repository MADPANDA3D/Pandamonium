import Storage from './storage.js';
import {
  detectProvider,
  extractProviderCredential,
  connectDetectedEndpoint,
  connectResultMessage,
} from './modelConnect.js';
import { startVoicePreview } from './voicePreview.js';
import {
  connectPortal,
  portalUrlIssue,
  portalMasterKeyIssue,
  portalConnectFailureMessage,
  portalConnectSuccessMessage,
} from './portalConnect.js';

let API_BASE = '';
let _handlers = {};
let _status = null;
let _statusFetchedAt = 0;
let _gallery = null;
let _view = { name: 'home', step: null };
let _notice = '';
let _busy = false;
let _skips = null;

const DISMISS_KEY = 'pandamonium-setup-wizard-dismissed';
const SKIPS_KEY = 'pandamonium-setup-wizard-skips';
const STATUS_TTL_MS = 30_000;

export function init(apiBase, handlers = {}) {
  API_BASE = apiBase || '';
  _handlers = handlers || {};
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function _modal() {
  return document.getElementById('guide-modal');
}

function _panel() {
  return document.getElementById('guide-panel');
}

function _isOpen() {
  const modal = _modal();
  return !!modal && !modal.classList.contains('hidden');
}

function _isDismissed() {
  return Storage.get(DISMISS_KEY) === '1';
}

function _dismissForever() {
  Storage.set(DISMISS_KEY, '1');
}

function _loadSkips() {
  if (_skips) return _skips;
  try {
    const parsed = JSON.parse(Storage.get(SKIPS_KEY) || '{}');
    _skips = parsed && typeof parsed === 'object' ? parsed : {};
  } catch (_) {
    _skips = {};
  }
  return _skips;
}

function _isSkipped(key) {
  return _loadSkips()[key] === true;
}

function _markSkipped(key) {
  _loadSkips()[key] = true;
  Storage.set(SKIPS_KEY, JSON.stringify(_loadSkips()));
}

function _isStatusPayload(payload) {
  return Boolean(
    payload
    && typeof payload === 'object'
    && typeof payload.is_admin === 'boolean'
    && payload.identity && typeof payload.identity === 'object'
    && payload.model && typeof payload.model === 'object'
  );
}

async function fetchStatus(force = false) {
  if (!force && _status && Date.now() - _statusFetchedAt < STATUS_TTL_MS) {
    return _status;
  }
  try {
    const res = await fetch(`${API_BASE}/api/setup/status`, { credentials: 'same-origin' });
    if (!res.ok) throw new Error('status unavailable');
    const payload = await res.json();
    _status = _isStatusPayload(payload) ? payload : { unavailable: true };
  } catch (_) {
    if (!_status) _status = { unavailable: true };
  }
  _statusFetchedAt = Date.now();
  return _status;
}

async function _fetchGallery() {
  try {
    const res = await fetch(`${API_BASE}/api/gallery/discovery`, { credentials: 'same-origin' });
    if (res.ok) _gallery = await res.json();
  } catch (_) {
    /* gallery discovery is optional */
  }
  return _gallery;
}

async function _fetchVoiceStatus() {
  try {
    const res = await fetch(`${API_BASE}/api/voice/status`, { credentials: 'same-origin' });
    if (!res.ok) throw new Error('voice status unavailable');
    const payload = await res.json();
    return payload && typeof payload === 'object' ? payload : { unavailable: true };
  } catch (_) {
    return { unavailable: true };
  }
}

async function _fetchPortalStatus() {
  try {
    const res = await fetch(`${API_BASE}/api/mcp/portal/status`, {
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
    });
    if (!res.ok) throw new Error('portal status unavailable');
    const payload = await res.json();
    return payload && typeof payload === 'object' ? payload : { unavailable: true };
  } catch (_) {
    return { unavailable: true };
  }
}

function _focusPrimary(modal) {
  return modal.querySelector(
    '.setup-lane-action.is-primary, .setup-wizard-primary, .setup-lane-action, .setup-wizard-field input'
  );
}

function _ensureFocusTrap(modal) {
  if (modal._setupFocusTrap) return;
  modal._setupFocusTrap = (event) => {
    if (!_isOpen()) return;
    const node = event.target;
    if (node && modal.contains(node)) return;
    _focusPrimary(modal)?.focus({ preventScroll: true });
  };
  document.addEventListener('focusin', modal._setupFocusTrap);
}

export async function open(options = {}) {
  const modal = _modal();
  if (!modal) return;
  modal.classList.remove('hidden');
  _view = options.step ? { name: 'step', step: options.step } : { name: 'home', step: null };
  if (options.notice) _notice = options.notice;
  _ensureFocusTrap(modal);
  await render();
  _focusPrimary(modal)?.focus();
}

export function close() {
  const modal = _modal();
  if (modal) modal.classList.add('hidden');
  window.dispatchEvent(new CustomEvent('pandamonium-setup-wizard-closed'));
}

export async function refreshStatus() {
  _statusFetchedAt = 0;
  if (!_isOpen()) return;
  const previous = _status;
  const next = await fetchStatus(true);
  if (previous && JSON.stringify(next) === JSON.stringify(previous)) return;
  render();
}

export async function maybeAutoOpen(authStatus) {
  if (!authStatus || !authStatus.is_admin) return;
  if (_isDismissed()) return;
  const status = await fetchStatus(true);
  if (!status || status.unavailable) return;
  if (status.identity?.configured && status.model?.usable) return;
  open();
}

function _requiredComplete(status) {
  return Boolean(status.identity?.configured && status.model?.usable);
}

function _openSettings(tab) {
  close();
  _handlers.openSettings?.(tab);
}

function _runTour() {
  close();
  _handlers.runChatCommand?.('/tour');
}

// MAD-931: the model-defaults chapter of the tour, surfaced from the guide so
// the first-run path can walk the five model lanes on demand.
function _runModelDefaults() {
  close();
  _handlers.runChatCommand?.('/tour-models');
}

function _runGuide() {
  close();
  _handlers.runChatCommand?.('/setup');
}

function _openStep(step) {
  _view = { name: 'step', step };
  render();
}

function _laneRows(status, isAdmin) {
  const identity = status.identity || {};
  const model = status.model || {};
  const voice = status.voice || {};
  const integrations = status.integrations || {};
  const extensions = status.extensions || {};
  const update = status.update || null;
  const gallery = _gallery || {};
  const connectedGalleries = Number(gallery.connected || 0);

  const managed = 'Managed by your administrator';
  const modelState = String(
    model.state || (model.usable
      ? 'validated'
      : (Number(model.endpoints || 0) > 0
        ? (Number(model.models || 0) > 0 ? 'discovered' : 'configured')
        : 'unconfigured'))
  );
  const rows = [
    {
      key: 'identity',
      label: 'Assistant name',
      done: Boolean(identity.configured),
      state: identity.configured
        ? `Ready — ${identity.display_name || 'named'}`
        : 'Required — using the default name',
      optional: false,
      action: isAdmin ? { label: 'Name it', run: () => { _view = { name: 'step', step: 'identity' }; render(); } } : null,
    },
    {
      key: 'model',
      label: 'Model engine',
      done: Boolean(model.usable),
      state: model.usable
        ? `Ready — ${model.models || 0} model${model.models === 1 ? '' : 's'} validated`
        : modelState === 'failed'
          ? `Needs attention — ${(model.last_failure && model.last_failure.category) || 'model test failed'}`
          : modelState === 'discovered'
            ? 'Models found — run the model test'
            : modelState === 'configured'
              ? 'Endpoint saved — add or discover a model'
              : 'Required — connect a model engine',
      optional: false,
      action: isAdmin ? { label: 'Connect', run: () => { _view = { name: 'step', step: 'model' }; render(); } } : null,
    },
    {
      key: 'voice',
      label: 'Voice',
      done: Boolean(voice.ready),
      state: voice.ready ? `Ready — ${voice.provider || 'configured'}` : 'Optional — talk to your assistant',
      optional: true,
      action: isAdmin ? { label: 'Set up', run: () => _openStep('voice') } : null,
    },
    {
      key: 'integrations',
      label: 'Services',
      done: Boolean(integrations.portal_connected || integrations.configured),
      state: integrations.portal_connected
        ? 'Ready — MAD MCP Portal connected'
        : integrations.configured
          ? `Ready — ${integrations.configured} service${integrations.configured === 1 ? '' : 's'} connected`
          : 'Optional — email, calendar, and more',
      optional: true,
      action: isAdmin ? { label: 'Connect', run: () => _openStep('integrations') } : null,
    },
    {
      key: 'extensions',
      label: 'Plugins',
      done: Number(extensions.enabled || 0) > 0,
      state: Number(extensions.enabled || 0) > 0
        ? `Ready — ${extensions.enabled} active`
        : 'Optional — add capabilities',
      optional: true,
      action: isAdmin ? { label: 'Browse', run: () => _openStep('plugins') } : null,
    },
    {
      key: 'gallery',
      label: 'Gallery',
      done: connectedGalleries > 0,
      state: connectedGalleries > 0
        ? `Ready — ${connectedGalleries} source${connectedGalleries === 1 ? '' : 's'} connected`
        : 'Optional — connect photos and media',
      optional: true,
      action: isAdmin ? { label: 'Connect', run: () => _openStep('gallery') } : null,
    },
  ];

  if (update) {
    const state = String(update.state || 'idle');
    rows.push({
      key: 'update',
      label: 'Updates',
      done: state === 'idle',
      state: state === 'idle'
        ? `Version ${update.version || 'unknown'}`
        : `Update ${state}`,
      optional: true,
      action: { label: 'View', run: () => _openStep('updates') },
    });
  }

  rows.forEach((row) => {
    if (row.optional && !row.done && _isSkipped(row.key)) {
      row.state = 'Skipped — set this up anytime';
    }
  });

  rows.forEach((row) => {
    if (!isAdmin && !row.done) {
      row.state = managed;
      row.action = null;
    }
  });
  return rows;
}

function _renderLane(row) {
  const lane = el('div', `setup-lane${row.done ? ' done' : ''}`);
  const mark = el('span', 'setup-lane-mark', row.done ? '✓' : row.optional ? '·' : '!');
  mark.setAttribute('aria-hidden', 'true');
  const copy = el('div', 'setup-lane-copy');
  copy.append(el('strong', 'setup-lane-label', row.label));
  copy.append(el('span', 'setup-lane-state', row.state));
  lane.append(mark, copy);
  if (row.action) {
    const button = el('button', `setup-lane-action${row.done ? '' : ' is-primary'}`, row.action.label);
    button.type = 'button';
    button.addEventListener('click', row.action.run);
    lane.append(button);
  }
  return lane;
}

async function renderHome(panel, status) {
  await _fetchGallery();
  const isAdmin = Boolean(status.is_admin);
  panel.replaceChildren();
  panel.className = 'setup-wizard';

  const head = el('div', 'setup-wizard-head');
  head.append(el('h3', null, isAdmin ? 'Set up Pandamonium' : 'Setup status'));
  head.append(el('p', null, isAdmin
    ? 'A guided path through the settings that matter. Everything here can be changed later.'
    : 'Your administrator manages this installation. Here is what is ready for you.'));
  panel.append(head);

  if (_notice) {
    panel.append(el('p', 'setup-wizard-notice', _notice));
    _notice = '';
  }

  if (_requiredComplete(status)) {
    panel.append(el('p', 'setup-wizard-ready', 'You are ready to chat.'));
  }

  const lanes = el('div', 'setup-lanes');
  _laneRows(status, isAdmin).forEach((row) => lanes.append(_renderLane(row)));
  panel.append(lanes);

  const next = el('div', 'setup-wizard-head');
  next.append(el('h3', null, "What's next"));
  next.append(el('p', null, 'Keep going whenever you are ready — these replays stay available from the guide.'));
  panel.append(next);

  const nextLinks = el('div', 'setup-wizard-footer');
  const tour = el('button', 'setup-wizard-secondary', 'Take the product tour');
  tour.type = 'button';
  tour.addEventListener('click', _runTour);
  nextLinks.append(tour);

  const modelDefaults = el('button', 'setup-wizard-secondary', 'Model defaults');
  modelDefaults.type = 'button';
  modelDefaults.addEventListener('click', _runModelDefaults);
  nextLinks.append(modelDefaults);

  const guide = el('button', 'setup-wizard-secondary', 'Replay the setup guide');
  guide.type = 'button';
  guide.addEventListener('click', _runGuide);
  nextLinks.append(guide);
  panel.append(nextLinks);

  const footer = el('div', 'setup-wizard-footer');
  const done = el('button', 'setup-wizard-primary', 'Done');
  done.type = 'button';
  done.addEventListener('click', close);
  footer.append(done);

  if (isAdmin) {
    const dismiss = el('button', 'setup-wizard-quiet', "Don't show this at startup");
    dismiss.type = 'button';
    dismiss.addEventListener('click', () => { _dismissForever(); close(); });
    footer.append(dismiss);
  }
  panel.append(footer);
}

function renderUnavailable(panel) {
  panel.replaceChildren();
  panel.className = 'setup-wizard';
  const head = el('div', 'setup-wizard-head');
  head.append(el('h3', null, 'Setup status'));
  head.append(el('p', null, "We couldn't check your setup right now. The app still works — try again in a moment."));
  panel.append(head);
  const footer = el('div', 'setup-wizard-footer');
  const retry = el('button', 'setup-wizard-primary', 'Try again');
  retry.type = 'button';
  retry.addEventListener('click', async () => { await fetchStatus(true); render(); });
  footer.append(retry);
  const done = el('button', 'setup-wizard-secondary', 'Close');
  done.type = 'button';
  done.addEventListener('click', close);
  footer.append(done);
  panel.append(footer);
}

function _backRow(panel) {
  const back = el('button', 'setup-wizard-quiet', '← Back');
  back.type = 'button';
  back.addEventListener('click', () => { _view = { name: 'home', step: null }; render(); });
  return back;
}

function renderIdentity(panel, status) {
  panel.replaceChildren();
  panel.className = 'setup-wizard';
  const head = el('div', 'setup-wizard-head');
  head.append(el('h3', null, 'What should we call your assistant?'));
  head.append(el('p', null, 'This is the name it answers to in chat and voice. You can change it anytime.'));
  panel.append(head);

  const field = el('label', 'setup-wizard-field');
  field.append(el('span', null, 'Assistant name'));
  const input = document.createElement('input');
  input.type = 'text';
  input.maxLength = 80;
  input.value = status.identity?.display_name || 'Assistant';
  input.autocomplete = 'off';
  field.append(input);
  panel.append(field);

  const message = el('p', 'setup-wizard-message');
  panel.append(message);

  const footer = el('div', 'setup-wizard-footer');
  const save = el('button', 'setup-wizard-primary', 'Save name');
  save.type = 'button';
  save.addEventListener('click', async () => {
    const name = input.value.trim();
    if (!name) {
      message.textContent = 'Please enter a name first.';
      message.className = 'setup-wizard-message is-error';
      return;
    }
    if (_busy) return;
    _busy = true;
    save.disabled = true;
    message.textContent = 'Saving…';
    message.className = 'setup-wizard-message';
    try {
      const res = await fetch(`${API_BASE}/api/auth/settings`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agent_display_name: name }),
      });
      if (!res.ok) throw new Error('save failed');
      _notice = `Saved — your assistant is now called ${name}.`;
      await fetchStatus(true);
      _view = { name: 'home', step: null };
      render();
    } catch (_) {
      message.textContent = "We couldn't save that name. Check the connection and try again.";
      message.className = 'setup-wizard-message is-error';
      save.disabled = false;
    } finally {
      _busy = false;
    }
  });
  footer.append(save);

  const advanced = el('button', 'setup-wizard-secondary', 'More identity options');
  advanced.type = 'button';
  advanced.addEventListener('click', () => _openSettings('identities'));
  footer.append(advanced);
  footer.append(_backRow(panel));
  panel.append(footer);
}

const MODEL_PROVIDER_DISPLAY = {
  llamacpp: 'llama.cpp',
  lmstudio: 'LM Studio',
  vllm: 'vLLM',
  ollama: 'Ollama',
};

function _refreshModelsInBackground() {
  try {
    const pending = window.modelsModule?.refreshModels?.(true);
    if (pending && typeof pending.catch === 'function') pending.catch(() => {});
  } catch (_) {
    /* the wizard status refresh already reflects the connection */
  }
}

async function _afterModelConnected(notice) {
  _notice = notice;
  await fetchStatus(true);
  _view = { name: 'home', step: null };
  await render();
  _refreshModelsInBackground();
  window.dispatchEvent(new CustomEvent('ge:model-endpoints-updated'));
}

function _discoveredBaseUrl(item) {
  const detected = detectProvider(String(item?.url || ''));
  return detected && detected.base_url ? detected.base_url : String(item?.url || '').replace(/\/+$/, '');
}

function _renderDiscoveredRow(item, alreadyAdded, onAdd) {
  const base = _discoveredBaseUrl(item);
  const hostPart = base.replace(/^https?:\/\//, '').split('/')[0];
  const providerDisplay = MODEL_PROVIDER_DISPLAY[item.provider]
    || (Number(item.port) === 1919 ? 'FreeToken' : 'OpenAI-compatible');
  const models = Array.isArray(item.models_display) && item.models_display.length
    ? item.models_display
    : (Array.isArray(item.models) ? item.models : []);

  const row = el('div', `setup-lane${alreadyAdded ? ' done' : ''}`);
  const mark = el('span', 'setup-lane-mark', alreadyAdded ? '✓' : '·');
  mark.setAttribute('aria-hidden', 'true');
  const copy = el('div', 'setup-lane-copy');
  copy.append(el('strong', 'setup-lane-label', `${providerDisplay} — ${hostPart}`));
  copy.append(el('span', 'setup-lane-state', models.length
    ? `${models.length} model${models.length === 1 ? '' : 's'}: ${models.join(', ')}`
    : 'No model IDs reported'));
  row.append(mark, copy);

  const add = el('button', 'setup-lane-action', alreadyAdded ? 'Added' : 'Add');
  add.type = 'button';
  add.disabled = alreadyAdded;
  add.addEventListener('click', () => onAdd({ add, base, providerDisplay, hostPart }));
  row.append(add);
  return row;
}

function renderModel(panel, status) {
  panel.replaceChildren();
  panel.className = 'setup-wizard';
  const head = el('div', 'setup-wizard-head');
  head.append(el('h3', null, 'Give it a brain'));
  head.append(el('p', null, 'Your assistant thinks through a model engine — a model running on this machine or a hosted API such as OpenRouter or DeepSeek. Paste a key or a server URL, or scan this machine. Settings stay closed.'));
  panel.append(head);

  const state = el('div', `setup-lane${status.model?.usable ? ' done' : ''}`);
  const mark = el('span', 'setup-lane-mark', status.model?.usable ? '✓' : '!');
  mark.setAttribute('aria-hidden', 'true');
  const copy = el('div', 'setup-lane-copy');
  copy.append(el('strong', 'setup-lane-label', 'Model engine'));
  copy.append(el('span', 'setup-lane-state', status.model?.usable
    ? `Validated — ${status.model.models} model${status.model.models === 1 ? '' : 's'} passed the model test`
    : (status.model?.last_failure?.guidance
      || status.model?.guidance
      || 'Not connected yet')));
  state.append(mark, copy);
  panel.append(state);

  const message = el('p', 'setup-wizard-message');
  panel.append(message);
  const setMessage = (text, isError) => {
    message.textContent = text;
    message.className = `setup-wizard-message${isError ? ' is-error' : ''}`;
  };

  const field = el('label', 'setup-wizard-field');
  field.append(el('span', null, 'Paste an API key or provider URL'));
  const input = document.createElement('input');
  input.type = 'text';
  input.autocomplete = 'off';
  input.spellcheck = false;
  input.placeholder = 'sk-or-… or http://localhost:11434';
  field.append(input);
  panel.append(field);

  const pasteFooter = el('div', 'setup-wizard-footer');
  const connect = el('button', 'setup-wizard-primary', 'Connect');
  connect.type = 'button';
  pasteFooter.append(connect);
  const scan = el('button', 'setup-wizard-secondary', 'Scan this machine');
  scan.type = 'button';
  pasteFooter.append(scan);
  panel.append(pasteFooter);

  const results = el('div', 'setup-lanes');
  panel.append(results);

  const skipFooter = el('div', 'setup-wizard-footer');
  const skip = el('button', 'setup-wizard-secondary', 'Skip for now');
  skip.type = 'button';
  skip.addEventListener('click', () => {
    _notice = 'No problem — connect a model whenever you are ready. Chat needs a model to work.';
    _view = { name: 'home', step: null };
    render();
  });
  skipFooter.append(skip);
  skipFooter.append(_backRow(panel));
  panel.append(skipFooter);

  const submitPasted = async () => {
    if (_busy) return;
    const raw = input.value.trim();
    if (!raw) {
      setMessage('Paste an API key or a server URL first, then press Connect.', true);
      return;
    }
    const paired = extractProviderCredential(raw);
    const detected = paired && paired.provider && paired.credential
      ? { base_url: paired.provider.url, api_key: paired.credential, name: paired.provider.name }
      : detectProvider(raw);
    if (!detected) {
      setMessage("We couldn't recognise that. Paste a provider API key (OpenRouter, OpenAI, Anthropic…) or a server URL such as http://localhost:11434/v1.", true);
      return;
    }
    if (detected.ambiguous) {
      setMessage("That key could belong to several providers, so we won't guess. Put the provider first — for example deepseek sk-… — then press Connect.", true);
      return;
    }

    const label = detected.name || detected.base_url;
    _busy = true;
    connect.disabled = true;
    setMessage(`Testing ${label}…`, false);
    try {
      const result = await connectDetectedEndpoint(detected, { apiBase: API_BASE });
      const outcome = connectResultMessage(result, label);
      if (outcome.level === 'success') {
        await _afterModelConnected(outcome.message);
        return;
      }
      setMessage(outcome.message, true);
    } finally {
      _busy = false;
      if (connect.isConnected) connect.disabled = false;
    }
  };
  connect.addEventListener('click', submitPasted);
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      submitPasted();
    }
  });

  const addDiscovered = async ({ add, base, providerDisplay, hostPart }) => {
    if (_busy) return;
    _busy = true;
    add.disabled = true;
    add.textContent = 'Adding…';
    setMessage(`Adding ${providerDisplay} at ${hostPart}…`, false);
    const label = `${providerDisplay} (${hostPart})`;
    try {
      const result = await connectDetectedEndpoint(
        { base_url: base, name: label },
        { apiBase: API_BASE, requireModels: false, skipProbe: false, endpointKind: 'local', refreshMode: 'auto' },
      );
      const outcome = connectResultMessage(result, label);
      if (outcome.level === 'success') {
        await _afterModelConnected(outcome.message);
        return;
      }
      if (result.failure === 'http_error') {
        setMessage(`We couldn't connect to ${label}. Make sure the server is still running, then scan again or paste its URL above.`, true);
      } else {
        setMessage(outcome.message, true);
      }
      add.disabled = false;
      add.textContent = 'Add';
    } finally {
      _busy = false;
    }
  };

  let scanning = false;
  scan.addEventListener('click', async () => {
    if (scanning || _busy) return;
    scanning = true;
    scan.disabled = true;
    results.replaceChildren();
    setMessage('Scanning this machine for model servers…', false);
    try {
      const [discoverRes, endpointsRes] = await Promise.all([
        fetch(`${API_BASE}/api/discover`, { credentials: 'same-origin' }),
        fetch(`${API_BASE}/api/model-endpoints`, { credentials: 'same-origin' }),
      ]);
      if (!discoverRes.ok) throw new Error('scan failed');
      const data = await discoverRes.json();
      const endpoints = endpointsRes.ok ? await endpointsRes.json() : [];
      const existingBases = new Set(
        (Array.isArray(endpoints) ? endpoints : []).map((endpoint) => _discoveredBaseUrl(endpoint))
      );
      const items = Array.isArray(data.items) ? data.items : [];
      if (!items.length) {
        setMessage('No local model server found. Start Ollama, LM Studio, vLLM, or llama.cpp on this machine, then scan again — or paste its URL above.', true);
        return;
      }
      setMessage(`Found ${items.length} model server${items.length === 1 ? '' : 's'}. Add the one you want to use.`, false);
      items.forEach((item) => {
        results.append(_renderDiscoveredRow(item, existingBases.has(_discoveredBaseUrl(item)), addDiscovered));
      });
    } catch (_) {
      setMessage("We couldn't scan this machine. Check the connection and try again, or paste the server URL above.", true);
    } finally {
      scanning = false;
      scan.disabled = false;
    }
  });
}

function _startStep(panel, title, copy) {
  panel.replaceChildren();
  panel.className = 'setup-wizard';
  const head = el('div', 'setup-wizard-head');
  head.append(el('h3', null, title));
  if (copy) head.append(el('p', null, copy));
  panel.append(head);
}

function _stateLane(label, state, done) {
  const lane = el('div', `setup-lane${done ? ' done' : ''}`);
  const mark = el('span', 'setup-lane-mark', done ? '✓' : '·');
  mark.setAttribute('aria-hidden', 'true');
  const copy = el('div', 'setup-lane-copy');
  copy.append(el('strong', 'setup-lane-label', label));
  copy.append(el('span', 'setup-lane-state', state));
  lane.append(mark, copy);
  return lane;
}

function _messageNode(panel) {
  const message = el('p', 'setup-wizard-message');
  panel.append(message);
  return {
    set(text, isError) {
      message.textContent = text;
      message.className = `setup-wizard-message${isError ? ' is-error' : ''}`;
    },
  };
}

function _skipButton(key, notice) {
  const skip = el('button', 'setup-wizard-secondary', 'Skip for now');
  skip.type = 'button';
  skip.addEventListener('click', () => {
    _markSkipped(key);
    _notice = notice;
    _view = { name: 'home', step: null };
    render();
  });
  return skip;
}

function _updateStateCopy(state) {
  if (state === 'idle') return 'No update in progress';
  if (state === 'failed' || state === 'error') {
    return 'The last update did not finish — an administrator can check the Updater';
  }
  return `Update in progress — ${state}`;
}

async function renderVoice(panel, status) {
  _startStep(panel, 'Give it a voice', 'Voice lets you talk to your assistant out loud and hear it answer. It is optional — chat works without it.');
  const voice = await _fetchVoiceStatus();
  const setup = (voice && voice.setup) || {};
  const ready = Boolean(setup.core_ready);
  const tts = (voice && voice.tts) || {};
  panel.append(_stateLane('Voice', ready ? 'Ready — voice setup is complete' : 'Not ready yet', ready));

  const message = _messageNode(panel);

  if (voice.unavailable) {
    message.set("We couldn't check voice right now. Try again in a moment.", true);
  } else if (!ready) {
    const guidance = Array.isArray(setup.guidance) ? setup.guidance : [];
    if (guidance.length) {
      guidance.forEach((line) => panel.append(el('p', 'setup-wizard-note', line)));
    } else {
      panel.append(el('p', 'setup-wizard-note', 'Enable a speech-to-text and text-to-speech provider in Voice settings, then come back.'));
    }
  }

  const canTest = !voice.unavailable && tts.available !== false && String(tts.provider || 'disabled') !== 'disabled';
  const footer = el('div', 'setup-wizard-footer');
  const test = el('button', `setup-wizard-${canTest ? 'primary' : 'secondary'}`, 'Test voice');
  test.type = 'button';
  test.disabled = !canTest;
  test.addEventListener('click', async () => {
    if (_busy) return;
    _busy = true;
    test.disabled = true;
    message.set('Playing a voice sample…', false);
    try {
      const preview = startVoicePreview({
        apiBase: API_BASE,
        provider: tts.provider || '',
        model: voice.model_override || '',
        voice: tts.voice || '',
        speed: tts.speed || 1,
        onPhase: (phase) => {
          if (phase === 'playing') message.set('Playing a voice sample…', false);
        },
      });
      await preview.done;
      message.set("That's your assistant's voice. If you heard it, voice is ready to go.", false);
    } catch (error) {
      message.set(error instanceof Error ? error.message : "We couldn't play the sample. Check the voice settings and try again.", true);
    } finally {
      _busy = false;
      test.disabled = false;
    }
  });
  footer.append(test);

  const settings = el('button', 'setup-wizard-secondary', 'Voice settings');
  settings.type = 'button';
  settings.addEventListener('click', () => _openSettings('ai'));
  footer.append(settings);
  footer.append(_skipButton('voice', 'No problem — set up voice whenever you like from the guide or Settings.'));
  footer.append(_backRow(panel));
  panel.append(footer);
}

async function renderIntegrations(panel, status) {
  _startStep(panel, 'Connect your services', 'MAD MCP Portal links your assistant to the services and tools in your Portal account. It is optional.');
  if (!status.is_admin) {
    panel.append(el('p', 'setup-wizard-note', 'Only an administrator can connect services on this installation, so this is managed for you.'));
    const footer = el('div', 'setup-wizard-footer');
    footer.append(_skipButton('integrations', 'No problem — services stay managed by your administrator.'));
    footer.append(_backRow(panel));
    panel.append(footer);
    return;
  }

  const portal = await _fetchPortalStatus();
  const connected = portal.status === 'connected' && portal.configured === true;
  const tools = Number(portal.tool_count || 0);
  panel.append(_stateLane('MAD MCP Portal', connected
    ? `Ready — ${tools} tool${tools === 1 ? '' : 's'} available`
    : (portal.unavailable ? "We couldn't check Portal right now" : 'Not connected yet'), connected));

  const message = _messageNode(panel);
  const footer = el('div', 'setup-wizard-footer');

  if (!connected) {
    const urlField = el('label', 'setup-wizard-field');
    urlField.append(el('span', null, 'Portal MCP URL'));
    const urlInput = document.createElement('input');
    urlInput.type = 'text';
    urlInput.autocomplete = 'off';
    urlInput.spellcheck = false;
    urlInput.placeholder = 'https://portal.example.com/api/mcp';
    if (portal.portal_url) urlInput.value = portal.portal_url;
    urlField.append(urlInput);
    panel.append(urlField);

    const keyField = el('label', 'setup-wizard-field');
    keyField.append(el('span', null, 'Master key'));
    const keyInput = document.createElement('input');
    keyInput.type = 'password';
    keyInput.autocomplete = 'off';
    keyInput.spellcheck = false;
    keyField.append(keyInput);
    panel.append(keyField);

    const connect = el('button', 'setup-wizard-primary', 'Connect');
    connect.type = 'button';
    connect.addEventListener('click', async () => {
      if (_busy) return;
      const issue = portalUrlIssue(urlInput.value) || portalMasterKeyIssue(keyInput.value);
      if (issue) {
        message.set(issue, true);
        return;
      }
      _busy = true;
      connect.disabled = true;
      message.set('Connecting to your Portal…', false);
      try {
        const result = await connectPortal({
          apiBase: API_BASE,
          portalUrl: urlInput.value,
          masterKey: keyInput.value,
        });
        keyInput.value = '';
        if (!result.ok) {
          message.set(portalConnectFailureMessage(result), true);
          return;
        }
        _notice = portalConnectSuccessMessage(result.payload);
        await fetchStatus(true);
        _view = { name: 'home', step: null };
        render();
      } finally {
        _busy = false;
        connect.disabled = false;
      }
    });
    footer.append(connect);
  }

  const more = el('button', 'setup-wizard-secondary', 'Other services');
  more.type = 'button';
  more.addEventListener('click', () => _openSettings('integrations'));
  footer.append(more);
  footer.append(_skipButton('integrations', 'No problem — connect your Portal whenever you like.'));
  footer.append(_backRow(panel));
  panel.append(footer);
}

function renderPlugins(panel, status) {
  const isAdmin = Boolean(status.is_admin);
  const extensions = status.extensions || {};
  const installed = Number(extensions.installed || 0);
  const enabled = Number(extensions.enabled || 0);
  _startStep(panel, 'Add plugins', 'Plugins add new capabilities to your assistant. Install and manage them anytime from Add Plugins.');
  panel.append(_stateLane('Plugins', installed > 0
    ? `Installed — ${installed} plugin${installed === 1 ? '' : 's'}, ${enabled} active`
    : 'Nothing installed yet', installed > 0));

  if (!isAdmin) {
    panel.append(el('p', 'setup-wizard-note', 'Only an administrator can add plugins on this installation, so this is managed for you.'));
  }

  const footer = el('div', 'setup-wizard-footer');
  if (isAdmin) {
    const add = el('button', 'setup-wizard-primary', 'Add Plugins');
    add.type = 'button';
    add.addEventListener('click', () => { close(); _handlers.openMarketplace?.(); });
    footer.append(add);
  }
  footer.append(_skipButton('extensions', 'No problem — browse plugins whenever you like.'));
  footer.append(_backRow(panel));
  panel.append(footer);
}

async function renderGallery(panel, status) {
  _startStep(panel, 'Connect your gallery', 'Gallery brings your photos and media into chat. It is optional.');
  if (!status.is_admin) {
    panel.append(el('p', 'setup-wizard-note', 'Only an administrator can connect gallery sources on this installation, so this is managed for you.'));
    const footer = el('div', 'setup-wizard-footer');
    footer.append(_skipButton('gallery', 'No problem — gallery stays managed by your administrator.'));
    footer.append(_backRow(panel));
    panel.append(footer);
    return;
  }

  const gallery = await _fetchGallery();
  const message = _messageNode(panel);
  if (!gallery) {
    panel.append(_stateLane('Gallery', "We couldn't check gallery sources right now", false));
    message.set('Try again in a moment, or open Gallery settings to connect a source.', true);
  } else {
    const connected = Number(gallery.connected || 0);
    panel.append(_stateLane('Gallery', connected > 0
      ? `Ready — ${connected} source${connected === 1 ? '' : 's'} connected`
      : 'No sources connected yet', connected > 0));
    const sources = Array.isArray(gallery.sources) ? gallery.sources : [];
    sources.slice(0, 5).forEach((source) => {
      const label = source.label || source.provider || 'Gallery source';
      const location = source.location || source.server_url || '';
      const stateCopy = source.state === 'connected'
        ? 'Connected'
        : source.state === 'disabled'
          ? 'Turned off'
          : 'Available';
      panel.append(_stateLane(label, `${stateCopy}${location ? ` — ${location}` : ''}`, source.state === 'connected'));
    });
    if (sources.length > 5) {
      panel.append(el('p', 'setup-wizard-note', `And ${sources.length - 5} more source${sources.length - 5 === 1 ? '' : 's'} in Gallery settings.`));
    }
  }

  const footer = el('div', 'setup-wizard-footer');
  const connect = el('button', 'setup-wizard-primary', 'Connect a gallery');
  connect.type = 'button';
  connect.addEventListener('click', () => { close(); _handlers.openGallery?.(); });
  footer.append(connect);
  footer.append(_skipButton('gallery', 'No problem — connect a gallery whenever you like.'));
  footer.append(_backRow(panel));
  panel.append(footer);
}

function renderUpdates(panel, status) {
  _startStep(panel, 'Updates', 'This guide only reports update state — it never changes your version. Installation and rollback stay in the Updater.');
  const update = status.update;
  if (!update) {
    panel.append(el('p', 'setup-wizard-note', 'Update status is available to administrators.'));
  } else {
    const state = String(update.state || 'idle');
    panel.append(_stateLane('Version', `Installed version ${update.version || 'unknown'}`, true));
    panel.append(_stateLane('Update state', _updateStateCopy(state), state === 'idle'));
    if (update.target_version) {
      panel.append(_stateLane('Target version', String(update.target_version), false));
    }
    panel.append(_stateLane('Rollback', update.rollback_available
      ? 'A rollback snapshot is available'
      : 'No rollback snapshot available', false));
  }

  const footer = el('div', 'setup-wizard-footer');
  footer.append(_skipButton('update', "Updates stay in your administrator's hands — nothing changed here."));
  footer.append(_backRow(panel));
  panel.append(footer);
}

async function render() {
  const panel = _panel();
  if (!panel) return;
  const status = await fetchStatus();
  if (!status || status.unavailable) {
    renderUnavailable(panel);
    return;
  }
  if (_view.name === 'step') {
    if (_view.step === 'identity') return renderIdentity(panel, status);
    if (_view.step === 'model') return renderModel(panel, status);
    if (_view.step === 'voice') return renderVoice(panel, status);
    if (_view.step === 'integrations') return renderIntegrations(panel, status);
    if (_view.step === 'plugins') return renderPlugins(panel, status);
    if (_view.step === 'gallery') return renderGallery(panel, status);
    if (_view.step === 'updates') return renderUpdates(panel, status);
  }
  await renderHome(panel, status);
}

export default {
  init,
  open,
  close,
  refreshStatus,
  maybeAutoOpen,
};
