// static/js/sshConnections.js — Settings → SSH Connections (MAD-935)
//
// Admin surface for operator-configured SSH nodes. The user's click is the
// approval: Test, Enable keyless, Generate key, and Pin host key each perform
// their action directly with no stacked confirmation. The only destructive
// action (Remove) uses the same confirm() pattern as the existing admin lists.
//
// Private keys are write-only here: the server never returns one. The public
// key is shown so it can be installed on the node.

import uiModule from './ui.js';

const LIST_ID = 'ssh-connections-list';
const EDITOR_ID = 'ssh-editor';
const MSG_ID = 'ssh-msg';

const STATE_LABELS = {
  connected: 'Connected',
  auth_failed: 'Auth failed',
  host_key_unknown: 'Host key not pinned',
  host_key_changed: 'Host key changed',
  unreachable: 'Unreachable',
  unavailable: 'OpenSSH unavailable',
  failed: 'Failed',
  unknown: 'Not tested',
};

let _loaded = false;
let _connections = [];
let _editingId = null;
const _expanded = new Set();

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
  if (options.body) fetchOptions.body = options.body;
  const res = await fetch(path, fetchOptions);
  let data = {};
  try { data = await res.json(); } catch (_) { data = {}; }
  if (!res.ok) {
    const detail = typeof data.detail === 'string' ? data.detail : 'The request failed.';
    throw new Error(detail);
  }
  return data;
}

function connectionPath(id, suffix = '') {
  return `/api/ssh/connections/${encodeURIComponent(id)}${suffix}`;
}

function statusChip(status) {
  const state = status && status.state ? status.state : 'unknown';
  const cls = state === 'connected' ? 'ssh-chip-ok' : (state === 'unknown' ? 'ssh-chip-idle' : 'ssh-chip-bad');
  return `<span class="ssh-chip ${cls}">${esc(STATE_LABELS[state] || state)}</span>`;
}

function rowHtml(connection) {
  const expanded = _expanded.has(connection.id);
  const keylessLabel = connection.keyless ? 'Disable keyless' : 'Enable keyless';
  const keyLabel = connection.has_private_key ? 'Show public key' : 'Use existing key';
  return `
    <div class="admin-user-row ssh-row" data-ssh-id="${esc(connection.id)}">
      <div class="admin-user-info">
        <span class="admin-user-name">${esc(connection.label)}</span>
        ${statusChip(connection.status)}
        ${connection.keyless ? '<span class="ssh-chip ssh-chip-key">Keyless</span>' : ''}
      </div>
      <div class="ssh-meta">${esc(connection.user)}@${esc(connection.host)}:${esc(connection.port)}${connection.host_key_pinned ? ` · host key ${esc(connection.host_key_fingerprint || 'pinned')}` : ' · host key not pinned'}</div>
      ${connection.status && connection.status.message ? `<div class="ssh-note">${esc(connection.status.message)}</div>` : ''}
      <div class="ssh-detail ${expanded ? '' : 'hidden'}" data-ssh-detail>
        ${connection.has_private_key
          ? `<div class="ssh-field-label">Public key — install this on the node</div><pre class="ssh-key">${esc(connection.public_key)}</pre>`
          : '<div class="ssh-note">No key on this connection yet. Generate one, or paste an existing private key below.</div>'}
        <div class="ssh-field-label">Paste an existing private key</div>
        <textarea class="settings-select ssh-key-input" data-ssh-key-input rows="3" autocomplete="off" spellcheck="false" placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"></textarea>
        <button type="button" class="admin-btn-sm" data-ssh-action="import-key">Save private key</button>
      </div>
      <div class="ssh-actions">
        <button type="button" class="admin-btn-sm" data-ssh-action="test">Test</button>
        <button type="button" class="admin-btn-sm" data-ssh-action="keyless">${keylessLabel}</button>
        ${connection.has_private_key ? '' : '<button type="button" class="admin-btn-sm" data-ssh-action="keypair">Generate key</button>'}
        <button type="button" class="admin-btn-sm" data-ssh-action="host-key">Pin host key</button>
        <button type="button" class="admin-btn-sm" data-ssh-action="detail">${keyLabel}</button>
        <button type="button" class="admin-btn-sm" data-ssh-action="edit">Edit</button>
        <button type="button" class="admin-btn-delete" data-ssh-action="remove">Remove</button>
      </div>
    </div>`;
}

function renderList() {
  const host = el(LIST_ID);
  if (!host) return;
  if (!_connections.length) {
    host.innerHTML = '<div class="admin-empty">No SSH connections yet. Add a node to get started.</div>';
    return;
  }
  host.innerHTML = _connections.map(rowHtml).join('');
}

function openEditor(connection) {
  const host = el(EDITOR_ID);
  if (!host) return;
  _editingId = connection ? connection.id : null;
  host.classList.remove('hidden');
  host.innerHTML = `
    <div class="admin-model-form ssh-editor-form">
      <div class="settings-row">
        <label class="settings-label" for="ssh-edit-label">Label</label>
        <input id="ssh-edit-label" class="settings-select" type="text" maxlength="80" autocomplete="off" value="${esc(connection ? connection.label : '')}">
      </div>
      <div class="settings-row">
        <label class="settings-label" for="ssh-edit-host">Host</label>
        <input id="ssh-edit-host" class="settings-select" type="text" autocapitalize="off" spellcheck="false" placeholder="192.168.1.20 or vps.example.com" value="${esc(connection ? connection.host : '')}">
      </div>
      <div class="settings-row">
        <label class="settings-label" for="ssh-edit-user">User</label>
        <input id="ssh-edit-user" class="settings-select" type="text" autocapitalize="off" spellcheck="false" placeholder="root" value="${esc(connection ? connection.user : '')}">
      </div>
      <div class="settings-row">
        <label class="settings-label" for="ssh-edit-port">Port</label>
        <input id="ssh-edit-port" class="settings-select" type="text" inputmode="numeric" style="width:110px" value="${esc(connection ? connection.port : 22)}">
      </div>
      <label class="ssh-check"><input type="checkbox" id="ssh-edit-keyless" ${connection && connection.keyless ? 'checked' : ''}> Keyless — generate a keypair and preset it for this node</label>
      <div class="ssh-editor-actions">
        <button type="button" class="admin-btn-sm" data-ssh-editor="cancel">Cancel</button>
        <button type="button" class="admin-btn-add" data-ssh-editor="save">${connection ? 'Save changes' : 'Add connection'}</button>
      </div>
    </div>`;
}

function closeEditor() {
  const host = el(EDITOR_ID);
  _editingId = null;
  if (host) {
    host.classList.add('hidden');
    host.innerHTML = '';
  }
}

async function saveEditor() {
  const label = el('ssh-edit-label');
  const host = el('ssh-edit-host');
  const user = el('ssh-edit-user');
  const port = el('ssh-edit-port');
  const keyless = el('ssh-edit-keyless');
  if (!label || !host || !user || !port) return;
  const body = new FormData();
  body.append('label', label.value);
  body.append('host', host.value);
  body.append('user', user.value);
  body.append('port', port.value);
  try {
    let data;
    if (_editingId) {
      data = await api(connectionPath(_editingId), { method: 'PATCH', body });
      if (keyless) {
        const keylessBody = new FormData();
        keylessBody.append('keyless', keyless.checked ? 'true' : 'false');
        data = await api(connectionPath(_editingId, '/keyless'), { method: 'POST', body: keylessBody });
      }
    } else {
      body.append('keyless', keyless && keyless.checked ? 'true' : 'false');
      data = await api('/api/ssh/connections', { method: 'POST', body });
    }
    closeEditor();
    await load(true);
    setMessage(data.message || 'Connection saved.', true);
  } catch (error) {
    setMessage(error.message, false);
  }
}

async function handleAction(action, connection) {
  try {
    if (action === 'test') {
      const data = await api(connectionPath(connection.id, '/test'), { method: 'POST' });
      await load(true);
      setMessage(data.message || 'Connection tested.', data.ok === true);
      return;
    }
    if (action === 'keyless') {
      const body = new FormData();
      body.append('keyless', connection.keyless ? 'false' : 'true');
      const data = await api(connectionPath(connection.id, '/keyless'), { method: 'POST', body });
      await load(true);
      setMessage(data.message || 'Keyless setting updated.', true);
      return;
    }
    if (action === 'keypair') {
      const data = await api(connectionPath(connection.id, '/keypair'), { method: 'POST' });
      _expanded.add(connection.id);
      await load(true);
      setMessage(data.message || 'Public key generated.', true);
      return;
    }
    if (action === 'host-key') {
      const data = await api(connectionPath(connection.id, '/host-key'), { method: 'POST' });
      await load(true);
      setMessage(data.message || 'Host key pinned.', true);
      return;
    }
    if (action === 'import-key') {
      const row = document.querySelector(`.ssh-row[data-ssh-id="${CSS.escape(connection.id)}"]`);
      const input = row ? row.querySelector('[data-ssh-key-input]') : null;
      const value = input ? input.value.trim() : '';
      if (!value) {
        setMessage('Paste a private key first.', false);
        return;
      }
      const body = new FormData();
      body.append('private_key', value);
      const data = await api(connectionPath(connection.id, '/private-key'), { method: 'POST', body });
      _expanded.add(connection.id);
      await load(true);
      setMessage(data.message || 'Private key saved.', true);
      return;
    }
    if (action === 'detail') {
      if (_expanded.has(connection.id)) _expanded.delete(connection.id);
      else _expanded.add(connection.id);
      renderList();
      return;
    }
    if (action === 'edit') {
      openEditor(connection);
      return;
    }
    if (action === 'remove') {
      if (!confirm(`Remove the SSH connection "${connection.label}"?`)) return;
      await api(connectionPath(connection.id), { method: 'DELETE' });
      _expanded.delete(connection.id);
      await load(true);
      setMessage('Connection removed.', true);
    }
  } catch (error) {
    setMessage(error.message, false);
  }
}

let _bound = false;
function bind() {
  if (_bound) return;
  const list = el(LIST_ID);
  if (!list) return;
  _bound = true;
  list.addEventListener('click', (event) => {
    const button = event.target.closest('[data-ssh-action]');
    if (!button) return;
    const row = button.closest('[data-ssh-id]');
    if (!row) return;
    const connection = _connections.find((item) => item.id === row.dataset.sshId);
    if (!connection) return;
    handleAction(button.dataset.sshAction, connection);
  });
  const addButton = el('ssh-add-btn');
  if (addButton) addButton.addEventListener('click', () => openEditor(null));
  const refreshButton = el('ssh-refresh-btn');
  if (refreshButton) refreshButton.addEventListener('click', () => load(true));
  const editor = el(EDITOR_ID);
  if (editor) {
    editor.addEventListener('click', (event) => {
      const button = event.target.closest('[data-ssh-editor]');
      if (!button) return;
      if (button.dataset.sshEditor === 'cancel') closeEditor();
      else saveEditor();
    });
  }
}

export async function load(force = false) {
  bind();
  const list = el(LIST_ID);
  if (_loaded && !force) return;
  if (!list) return;
  if (!_loaded) list.innerHTML = '<div class="admin-empty">Loading...</div>';
  try {
    const data = await api('/api/ssh/connections');
    _connections = Array.isArray(data.connections) ? data.connections : [];
    _loaded = true;
    renderList();
  } catch (error) {
    list.innerHTML = `<div class="admin-empty">${esc(error.message)}</div>`;
  }
}

export function open() {
  load(true);
}

const sshModule = { load, open, _connections: () => _connections };
export default sshModule;
