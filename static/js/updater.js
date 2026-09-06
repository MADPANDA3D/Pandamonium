let pollTimer = null;
let lastRelease = null;

const el = id => document.getElementById(id);

function setState(text, kind = 'unknown') {
  const state = el('sidebar-update-state');
  if (!state) return;
  state.textContent = text;
  state.className = `sidebar-update-state is-${kind}`;
}

function shortCommit(value) {
  return value ? String(value).slice(0, 8) : 'unknown';
}

function renderOperation(operation = {}) {
  const detail = el('sidebar-update-detail');
  const backup = el('sidebar-update-backup');
  const rollback = el('sidebar-update-rollback');
  if (!detail || !backup || !rollback) return;
  const active = ['queued', 'running'].includes(operation.status);
  const action = el('sidebar-update-action');
  const checkButton = el('sidebar-update-check');
  if (action) action.disabled = active;
  if (checkButton) checkButton.disabled = active;
  if (active) {
    detail.hidden = false;
    detail.textContent = `${operation.message || operation.phase || 'Working'} · ${operation.progress || 0}%`;
    setState('Update in progress', 'checking');
  } else if (operation.message) {
    detail.hidden = false;
    detail.textContent = operation.message;
  } else {
    detail.hidden = true;
    detail.textContent = '';
  }
  if (operation.status === 'failed') {
    setState(operation.rollback_error ? 'Update and rollback failed' : 'Update failed', 'unknown');
  } else if (['succeeded', 'recovered', 'rolled_back'].includes(operation.status)) {
    setState(operation.status === 'succeeded' ? 'Update complete' : 'Rollback complete', 'current');
  }
  backup.hidden = !operation.backup_location;
  backup.textContent = operation.backup_location ? `Backup: ${operation.backup_location}` : '';
  rollback.hidden = !operation.rollback_available || active;
  if (active && !pollTimer) pollTimer = window.setInterval(pollStatus, 1000);
  if (!active && pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

function renderRelease(data) {
  lastRelease = data;
  if (data.version) window._appVersion = data.version;
  const version = el('sidebar-update-version');
  const commit = el('sidebar-update-commit');
  const action = el('sidebar-update-action');
  if (!version || !commit || !action) return;
  version.textContent = `Version ${data.version ? `v${data.version}` : 'unknown'}`;
  commit.textContent = `Deployed ${data.release || 'source'} · ${shortCommit(data.commit)}`;
  action.hidden = !(data.update_available && data.can_update);
  action.textContent = data.latest_version ? `Update to v${data.latest_version}` : 'Update now';
  if (data.update_available) {
    const compatibility = data.compatible
      ? (data.can_update ? 'Compatible' : data.installation?.reason)
      : data.compatibility_reason;
    setState(`v${data.latest_version} available · ${shortCommit(data.latest_commit)}`, data.compatible ? 'available' : 'unknown');
    const detail = el('sidebar-update-detail');
    if (detail && compatibility) {
      detail.hidden = false;
      detail.textContent = compatibility;
    }
  } else if (data.update_status === 'current') {
    setState('Up to date', 'current');
  } else {
    setState(data.compatibility_reason || 'Update check unavailable', 'unknown');
  }
  renderOperation(data.operation);
}

async function api(url, options = {}) {
  const response = await fetch(url, { credentials: 'same-origin', ...options });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || 'Update request failed');
  return data;
}

async function pollStatus() {
  try {
    const operation = await api('/api/update/status');
    renderOperation(operation);
    if (['succeeded', 'recovered', 'rolled_back'].includes(operation.status)) {
      renderRelease(await api('/api/version'));
      renderOperation(operation);
    }
  } catch (_) {
    // A short network gap is expected while the service restarts.
  }
}

async function check() {
  const button = el('sidebar-update-check');
  if (button) button.disabled = true;
  setState('Checking for updates…', 'checking');
  try {
    renderRelease(await api('/api/update/check', { method: 'POST' }));
  } catch (error) {
    setState(error.message, 'unknown');
  } finally {
    if (button) button.disabled = false;
  }
}

async function update() {
  if (!lastRelease?.latest_version || !window.confirm(
    `Update ${lastRelease.release || `v${lastRelease.version}`} (${shortCommit(lastRelease.commit)}) to v${lastRelease.latest_version} (${shortCommit(lastRelease.latest_commit)})?`,
  )) return;
  try {
    renderOperation(await api('/api/update/apply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        version: lastRelease.latest_version,
        commit: lastRelease.latest_commit,
      }),
    }));
  } catch (error) {
    setState(error.message, 'unknown');
  }
}

async function rollback() {
  if (!window.confirm('Roll back to the retained previous immutable release?')) return;
  try {
    renderOperation(await api('/api/update/rollback', { method: 'POST' }));
  } catch (error) {
    setState(error.message, 'unknown');
  }
}

async function init() {
  el('sidebar-update-check')?.addEventListener('click', check);
  el('sidebar-update-action')?.addEventListener('click', update);
  el('sidebar-update-rollback')?.addEventListener('click', rollback);
  try {
    renderRelease(await api('/api/version'));
    await pollStatus();
  } catch (_) {
    setState('Update check unavailable', 'unknown');
  }
}

export default { init };
