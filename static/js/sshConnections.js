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
const TAILNET_ID = 'ssh-tailnet-results';

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
let _pendingTailnetPeer = null;
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
  const isTailscale = connection.auth_mode === 'tailscale_ssh';
  const keylessLabel = connection.keyless ? 'Disable keyless' : 'Enable keyless';
  const keyLabel = connection.has_private_key ? 'Show public key' : 'Use existing key';
  const meta = isTailscale
    ? `${esc(connection.user)}@${esc(connection.host)}:${esc(connection.port)} · Tailscale SSH (no key needed)`
    : `${esc(connection.user)}@${esc(connection.host)}:${esc(connection.port)}${connection.host_key_pinned ? ` · host key ${esc(connection.host_key_fingerprint || 'pinned')}` : ' · host key not pinned'}`;
  const actions = isTailscale
    ? `<button type="button" class="admin-btn-sm" data-ssh-action="test">Test</button>
        <button type="button" class="admin-btn-sm" data-ssh-action="edit">Edit</button>
        <button type="button" class="admin-btn-delete" data-ssh-action="remove">Remove</button>`
    : `<button type="button" class="admin-btn-sm" data-ssh-action="test">Test</button>
        <button type="button" class="admin-btn-sm" data-ssh-action="keyless">${keylessLabel}</button>
        ${connection.has_private_key ? '' : '<button type="button" class="admin-btn-sm" data-ssh-action="keypair">Generate key</button>'}
        <button type="button" class="admin-btn-sm" data-ssh-action="host-key">Pin host key</button>
        <button type="button" class="admin-btn-sm" data-ssh-action="detail">${keyLabel}</button>
        <button type="button" class="admin-btn-sm" data-ssh-action="edit">Edit</button>
        <button type="button" class="admin-btn-delete" data-ssh-action="remove">Remove</button>`;
  return `
    <div class="admin-user-row ssh-row" data-ssh-id="${esc(connection.id)}">
      <div class="admin-user-info">
        <span class="admin-user-name">${esc(connection.label)}</span>
        ${statusChip(connection.status)}
        ${isTailscale ? '<span class="ssh-chip ssh-chip-key">Tailscale SSH</span>' : ''}
        ${connection.keyless ? '<span class="ssh-chip ssh-chip-key">Keyless</span>' : ''}
      </div>
      <div class="ssh-meta">${meta}</div>
      ${connection.status && connection.status.message ? `<div class="ssh-note">${esc(connection.status.message)}</div>` : ''}
      ${isTailscale ? '' : `<div class="ssh-detail ${expanded ? '' : 'hidden'}" data-ssh-detail>
        ${connection.has_private_key
          ? `<div class="ssh-field-label">Public key — install this on the node</div><pre class="ssh-key">${esc(connection.public_key)}</pre>`
          : '<div class="ssh-note">No key on this connection yet. Generate one, or paste an existing private key below.</div>'}
        ${connection.has_private_key && (!connection.status || connection.status.state !== 'connected')
          ? `<div class="ssh-field-label">Finish setup — install this key with the node password (used once, never stored)</div>
             <div class="ssh-install-row">
               <input type="password" class="settings-select ssh-password-input" data-ssh-password autocomplete="off" placeholder="Password for ${esc(connection.user)}@${esc(connection.host)}">
               <button type="button" class="admin-btn-sm" data-ssh-action="install-key">Install key</button>
             </div>`
          : ''}
        <div class="ssh-field-label">Paste an existing private key</div>
        <textarea class="settings-select ssh-key-input" data-ssh-key-input rows="3" autocomplete="off" spellcheck="false" placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"></textarea>
        <button type="button" class="admin-btn-sm" data-ssh-action="import-key">Save private key</button>
      </div>`}
      <div class="ssh-actions">${actions}</div>
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

function openEditor(connection, prefill = null) {
  const host = el(EDITOR_ID);
  if (!host) return;
  _editingId = connection ? connection.id : null;
  _pendingTailnetPeer = !connection && prefill && prefill.tailnetPeer ? prefill.tailnetPeer : null;
  const peer = _pendingTailnetPeer;
  host.classList.remove('hidden');
  host.innerHTML = `
    <div class="admin-model-form ssh-editor-form">
      <div class="settings-row">
        <label class="settings-label" for="ssh-edit-label">Label</label>
        <input id="ssh-edit-label" class="settings-select" type="text" maxlength="80" autocomplete="off" value="${esc(connection ? connection.label : (peer ? peer.name : ''))}">
      </div>
      <div class="settings-row">
        <label class="settings-label" for="ssh-edit-host">Host</label>
        <input id="ssh-edit-host" class="settings-select" type="text" autocapitalize="off" spellcheck="false" placeholder="192.168.1.20 or vps.example.com" value="${esc(connection ? connection.host : '')}"${peer ? ' disabled' : ''}>
      </div>
      ${peer ? `<div class="ssh-note">Tailnet node "${esc(peer.name)}" (${esc(peer.os || 'unknown OS')}) selected. Its tailnet address is resolved when you save.</div>` : ''}
      <div class="settings-row">
        <label class="settings-label" for="ssh-edit-user">User</label>
        <input id="ssh-edit-user" class="settings-select" type="text" autocapitalize="off" spellcheck="false" placeholder="root" value="${esc(connection ? connection.user : '')}">
      </div>
      <div class="settings-row">
        <label class="settings-label" for="ssh-edit-port">Port</label>
        <input id="ssh-edit-port" class="settings-select" type="text" inputmode="numeric" style="width:110px" value="${esc(connection ? connection.port : 22)}">
      </div>
      ${peer && peer.keyless
        ? '<div class="ssh-note">This node has Tailscale SSH enabled — no key or password is needed.</div>'
        : `<label class="ssh-check"><input type="checkbox" id="ssh-edit-keyless" ${connection && connection.keyless ? 'checked' : ''}> Keyless — generate a keypair and preset it for this node</label>`}
      <div class="ssh-editor-actions">
        <button type="button" class="admin-btn-sm" data-ssh-editor="cancel">Cancel</button>
        <button type="button" class="admin-btn-add" data-ssh-editor="save">${connection ? 'Save changes' : 'Add connection'}</button>
      </div>
    </div>`;
}

function closeEditor() {
  const host = el(EDITOR_ID);
  _editingId = null;
  _pendingTailnetPeer = null;
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
  if (_pendingTailnetPeer && !_editingId) {
    body.append('host', '');
    body.append('tailnet_peer_id', _pendingTailnetPeer.id);
  } else {
    body.append('host', host.value);
  }
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
    if (action === 'install-key') {
      const row = document.querySelector(`.ssh-row[data-ssh-id="${CSS.escape(connection.id)}"]`);
      const input = row ? row.querySelector('[data-ssh-password]') : null;
      const password = input ? input.value : '';
      if (!password) {
        setMessage('Enter the node password to finish setup.', false);
        return;
      }
      const body = new FormData();
      body.append('password', password);
      const data = await api(connectionPath(connection.id, '/install-key'), { method: 'POST', body });
      if (input) input.value = '';
      await load(true);
      setMessage(data.message || 'Key installed.', data.ok === true);
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

async function scanTailnet() {
  const results = el(TAILNET_ID);
  if (!results) return;
  const button = el('ssh-scan-btn');
  if (button) button.disabled = true;
  results.classList.remove('hidden');
  results.innerHTML = '<div class="admin-empty">Scanning your tailnet…</div>';
  try {
    const data = await api('/api/ssh/discover');
    renderTailnetPeers(data);
  } catch (error) {
    results.innerHTML = `<div class="admin-empty">${esc(error.message)}</div>`;
  } finally {
    if (button) button.disabled = false;
  }
}

function renderTailnetPeers(data) {
  const results = el(TAILNET_ID);
  if (!results) return;
  const peers = data && Array.isArray(data.peers) ? data.peers : [];
  if (!data || data.available === false || !peers.length) {
    results.innerHTML = `<div class="admin-empty">${esc((data && data.message) || 'No online tailnet nodes were found.')}</div>`;
    return;
  }
  results.innerHTML = `
    <div class="ssh-tailnet-panel">
      <div class="ssh-field-label">Online tailnet nodes — pick one to add</div>
      ${peers.map((peer) => `
        <div class="ssh-tailnet-row">
          <div class="ssh-tailnet-info">
            <span class="admin-user-name">${esc(peer.name)}</span>
            <span class="ssh-meta">${esc(peer.os || 'unknown OS')}${peer.keyless ? ' · Keyless (Tailscale SSH)' : ''}</span>
          </div>
          <button type="button" class="admin-btn-sm" data-ssh-tailnet="${esc(peer.id)}">Add</button>
        </div>`).join('')}
    </div>`;
  results.querySelectorAll('[data-ssh-tailnet]').forEach((button) => {
    button.addEventListener('click', () => {
      const peer = peers.find((item) => item.id === button.dataset.sshTailnet);
      if (!peer) return;
      openEditor(null, { tailnetPeer: peer });
      const editor = el(EDITOR_ID);
      if (editor && editor.scrollIntoView) editor.scrollIntoView({ block: 'nearest' });
    });
  });
}

let _bound = false;
function bind() {  if (_bound) return;
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
  const scanButton = el('ssh-scan-btn');
  if (scanButton) scanButton.addEventListener('click', scanTailnet);
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
