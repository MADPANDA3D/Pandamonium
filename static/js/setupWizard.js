import Storage from './storage.js';
import {
  detectProvider,
  extractProviderCredential,
  connectDetectedEndpoint,
  connectResultMessage,
} from './modelConnect.js';

let API_BASE = '';
let _handlers = {};
let _status = null;
let _statusFetchedAt = 0;
let _gallery = null;
let _view = { name: 'home', step: null };
let _notice = '';
let _busy = false;

const DISMISS_KEY = 'pandamonium-setup-wizard-dismissed';
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
      action: isAdmin ? { label: 'Set up', run: () => _openSettings('ai') } : null,
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
      action: isAdmin ? { label: 'Connect', run: () => _openSettings('integrations') } : null,
    },
    {
      key: 'extensions',
      label: 'Plugins',
      done: Number(extensions.enabled || 0) > 0,
      state: Number(extensions.enabled || 0) > 0
        ? `Ready — ${extensions.enabled} active`
        : 'Optional — add capabilities',
      optional: true,
      action: isAdmin ? { label: 'Browse', run: () => { close(); _handlers.openMarketplace?.(); } } : null,
    },
    {
      key: 'gallery',
      label: 'Gallery',
      done: connectedGalleries > 0,
      state: connectedGalleries > 0
        ? `Ready — ${connectedGalleries} source${connectedGalleries === 1 ? '' : 's'} connected`
        : 'Optional — connect photos and media',
      optional: true,
      action: isAdmin ? { label: 'Connect', run: () => { close(); _handlers.openGallery?.(); } } : null,
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
      action: null,
    });
  }

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

  const footer = el('div', 'setup-wizard-footer');
  const done = el('button', 'setup-wizard-primary', 'Done');
  done.type = 'button';
  done.addEventListener('click', close);
  footer.append(done);

  const tour = el('button', 'setup-wizard-secondary', 'Take the tour');
  tour.type = 'button';
  tour.addEventListener('click', _runTour);
  footer.append(tour);

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
  advanced.addEventListener('click', () => _openSettings('ai'));
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

async function render() {
  const panel = _panel();
  if (!panel) return;
  const status = await fetchStatus();
  if (!status || status.unavailable) {
    renderUnavailable(panel);
    return;
  }
  if (_view.name === 'step' && _view.step === 'identity') {
    renderIdentity(panel, status);
    return;
  }
  if (_view.name === 'step' && _view.step === 'model') {
    renderModel(panel, status);
    return;
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
