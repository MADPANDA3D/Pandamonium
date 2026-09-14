// static/js/unslothRuntime.js — Settings → Training Runtime (MAD-796)
//
// Admin surface for an operator-provided Unsloth Studio runtime. The tab only
// configures, tests, disables, and removes the connection: Test is read-only
// discovery and no control starts, stops, or resets a training job. The access
// token is write-only here — the server never returns it.

import uiModule from './ui.js';

const ROOT_ID = 'unsloth-runtime-status';
const EDITOR_ID = 'unsloth-editor';
const MSG_ID = 'unsloth-msg';

const STATE_LABELS = {
  unconfigured: 'Not configured',
  disabled: 'Disabled',
  untested: 'Not tested',
  online: 'Online',
  offline: 'Offline',
  unauthorized: 'Token rejected',
  incompatible: 'Incompatible',
  busy: 'Busy',
  insufficient_resources: 'Insufficient resources',
  invalid_response: 'Invalid response',
  error: 'Runtime error',
};

const CAPABILITY_LABELS = {
  training: 'Training',
  conversion: 'Conversion',
  inference: 'Inference',
};

const CAPABILITY_STATE_LABELS = {
  supported: 'Supported',
  unsupported: 'Unsupported',
  unavailable: 'Unavailable',
  unknown: 'Unknown',
};

let _loaded = false;
let _state = null;

function el(id) { return document.getElementById(id); }
function esc(value) { return uiModule.esc(value == null ? '' : String(value)); }

function setMessage(text, ok) {
  const node = el(MSG_ID);
  if (!node) return;
  node.textContent = text || '';
  node.style.color = ok ? 'var(--green, #50fa7b)' : (text ? 'var(--red, #ff3347)' : '');
}

async function api(path, options = {}) {
  const fetchOptions = { method: options.method || 'GET', credentials: 'same-origin' };
  if (options.body) {
    fetchOptions.headers = { 'Content-Type': 'application/json' };
    fetchOptions.body = JSON.stringify(options.body);
  }
  const res = await fetch(path, fetchOptions);
  let data = {};
  try { data = await res.json(); } catch (_) { data = {}; }
  if (!res.ok) {
    const detail = typeof data.detail === 'string' ? data.detail : (data.detail && data.detail.message) || 'The request failed.';
    throw new Error(detail);
  }
  return data;
}

function stateChip(status) {
  const state = status || 'untested';
  const cls = state === 'online' ? 'ok' : (state === 'untested' || state === 'unconfigured' ? 'idle' : 'bad');
  return `<span class="unsloth-chip unsloth-chip-${cls}">${esc(STATE_LABELS[state] || state)}</span>`;
}

function capabilityChips(capabilities) {
  if (!capabilities) return '';
  return Object.keys(CAPABILITY_LABELS).map((key) => {
    const capability = capabilities[key] || { state: 'unknown' };
    const state = capability.state || 'unknown';
    const cls = state === 'supported' ? 'ok' : (state === 'unknown' ? 'idle' : 'bad');
    return `<span class="unsloth-chip unsloth-chip-${cls}" title="${esc(capability.detail || '')}">${CAPABILITY_LABELS[key]}: ${esc(CAPABILITY_STATE_LABELS[state] || state)}</span>`;
  }).join(' ');
}

function renderStatus() {
  const host = el(ROOT_ID);
  if (!host) return;
  if (!_state || !_state.configured) {
    host.innerHTML = '<div class="admin-empty">No Unsloth Studio runtime is configured. Pandamonium does not bundle a trainer: run Unsloth Studio on a machine you own, then point this tab at its HTTP API.</div>';
    return;
  }
  const status = _state.status || 'untested';
  const detail = [
    _state.base_url,
    _state.runtime_version ? `runtime v${_state.runtime_version}` : '',
    `adapter ${_state.contract || ''}`,
    _state.job_state ? `job ${_state.job_state}` : '',
  ].filter(Boolean).join(' · ');
  host.innerHTML = `
    <div class="admin-user-row unsloth-row">
      <div class="admin-user-info">
        <span class="admin-user-name">Unsloth Studio</span>
        ${stateChip(status)}
        ${_state.enabled ? '' : '<span class="unsloth-chip unsloth-chip-idle">Disabled</span>'}
        ${_state.token_configured ? '<span class="unsloth-chip unsloth-chip-idle">Token saved</span>' : ''}
      </div>
      <div class="unsloth-meta">${esc(detail)}</div>
      ${_state.last_message ? `<div class="unsloth-note">${esc(_state.last_message)}</div>` : ''}
      <div class="unsloth-caps">${capabilityChips(_state.capabilities)}</div>
      <div class="unsloth-actions">
        <button type="button" class="admin-btn-sm" data-unsloth-action="test">Test</button>
        <button type="button" class="admin-btn-sm" data-unsloth-action="toggle">${_state.enabled ? 'Disable' : 'Enable'}</button>
        <button type="button" class="admin-btn-sm" data-unsloth-action="edit">Edit</button>
        <button type="button" class="admin-btn-delete" data-unsloth-action="remove">Remove</button>
      </div>
    </div>`;
}

function closeEditor() {
  const host = el(EDITOR_ID);
  if (host) {
    host.classList.add('hidden');
    host.innerHTML = '';
  }
}

function openEditor() {
  const host = el(EDITOR_ID);
  if (!host) return;
  const state = _state || {};
  host.classList.remove('hidden');
  host.innerHTML = `
    <div class="unsloth-editor-form">
      <div class="settings-row">
        <label class="settings-label" for="unsloth-edit-url">Studio URL</label>
        <input id="unsloth-edit-url" class="settings-select" type="text" autocomplete="off" spellcheck="false" placeholder="http://127.0.0.1:8888" value="${esc(state.base_url || '')}">
      </div>
      <div class="settings-row">
        <label class="settings-label" for="unsloth-edit-token">Access token</label>
        <input id="unsloth-edit-token" class="settings-select" type="password" autocomplete="off" spellcheck="false" placeholder="${state.token_configured ? 'Saved — leave blank to keep' : 'Paste a Studio access token'}">
      </div>
      <div class="settings-row">
        <label class="settings-label" for="unsloth-edit-endpoint">Model endpoint id (optional)</label>
        <input id="unsloth-edit-endpoint" class="settings-select" type="text" autocomplete="off" spellcheck="false" placeholder="Bind an existing added model" value="${esc(state.model_endpoint ? state.model_endpoint.id : '')}">
      </div>
      <div class="unsloth-editor-actions">
        <button type="button" class="admin-btn-sm" data-unsloth-editor="cancel">Cancel</button>
        <button type="button" class="admin-btn-add" data-unsloth-editor="save">Save runtime</button>
      </div>
    </div>`;
}

async function saveEditor() {
  const url = el('unsloth-edit-url');
  const token = el('unsloth-edit-token');
  const endpoint = el('unsloth-edit-endpoint');
  if (!url) return;
  const body = { base_url: url.value.trim() };
  if (token && token.value.trim()) body.token = token.value.trim();
  if (endpoint) body.model_endpoint_id = endpoint.value.trim();
  try {
    const data = await api('/api/unsloth/connection', { method: 'PUT', body });
    closeEditor();
    await load(true);
    setMessage(data.status === 'disabled' ? 'Runtime saved (disabled).' : 'Runtime saved. Run Test to verify it.', true);
  } catch (error) {
    setMessage(error.message, false);
  }
}

async function handleAction(action) {
  if (!_state) return;
  try {
    if (action === 'test') {
      const data = await api('/api/unsloth/connection/test', { method: 'POST' });
      await load(true);
      setMessage(data.message || 'Runtime tested.', data.ok === true);
      return;
    }
    if (action === 'toggle') {
      const data = await api('/api/unsloth/connection', { method: 'PUT', body: { enabled: !_state.enabled } });
      await load(true);
      setMessage(data.enabled ? 'Runtime enabled. Run Test to verify it.' : 'Runtime disabled.', true);
      return;
    }
    if (action === 'edit') {
      openEditor();
      return;
    }
    if (action === 'remove') {
      if (!confirm('Remove the Unsloth Studio runtime connection?')) return;
      await api('/api/unsloth/connection', { method: 'DELETE' });
      closeEditor();
      await load(true);
      setMessage('Runtime connection removed. Existing models and data are untouched.', true);
    }
  } catch (error) {
    setMessage(error.message, false);
  }
}

let _bound = false;
function bind() {
  if (_bound) return;
  const host = el(ROOT_ID);
  if (!host) return;
  _bound = true;
  host.addEventListener('click', (event) => {
    const button = event.target.closest('[data-unsloth-action]');
    if (!button) return;
    handleAction(button.dataset.unslothAction);
  });
  const addButton = el('unsloth-add-btn');
  if (addButton) {
    addButton.addEventListener('click', () => openEditor());
    addButton.textContent = 'Configure runtime';
  }
  const refreshButton = el('unsloth-refresh-btn');
  if (refreshButton) refreshButton.addEventListener('click', () => load(true));
  const editor = el(EDITOR_ID);
  if (editor) {
    editor.addEventListener('click', (event) => {
      const button = event.target.closest('[data-unsloth-editor]');
      if (!button) return;
      if (button.dataset.unslothEditor === 'cancel') closeEditor();
      else saveEditor();
    });
  }
}

export async function load(force = false) {
  bind();
  const host = el(ROOT_ID);
  if (!host) return;
  if (_loaded && !force) return;
  if (!_loaded) host.innerHTML = '<div class="admin-empty">Loading...</div>';
  try {
    _state = await api('/api/unsloth/connection');
    _loaded = true;
    renderStatus();
  } catch (error) {
    host.innerHTML = `<div class="admin-empty">${esc(error.message)}</div>`;
  }
}

export function open() {
  load(true);
}

const unslothModule = { load, open, _state: () => _state };
export default unslothModule;