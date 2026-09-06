let pollTimer = null;
let pollInFlight = false;
let lastRelease = null;
let lastOperation = {};
let modalOpener = null;
let initialized = false;

const POLL_INTERVAL_MS = 900;
const MODAL_ID = 'updater-modal';
const ACTIVE_STATUSES = new Set(['queued', 'running']);
const SUCCESS_STATUSES = new Set(['succeeded', 'recovered', 'rolled_back']);
const PHASES = ['scan', 'verify', 'preserve', 'activate', 'complete'];

const el = id => document.getElementById(id);

function setState(text, kind = 'unknown') {
  const state = el('sidebar-update-state');
  if (!state) return;
  state.textContent = text;
  state.className = `sidebar-update-state is-${kind}`;
}

function setPill(state, label) {
  const pill = el('updater-status-pill');
  if (!pill) return;
  pill.dataset.state = state;
  pill.textContent = label;
}

function shortCommit(value) {
  return value ? String(value).slice(0, 8) : 'unknown';
}

function installationLabel(kind) {
  return ({
    'managed-native': 'Managed native',
    container: 'Docker container',
    'source-checkout': 'Source checkout',
  })[kind] || 'Custom installation';
}

function deploymentLabel(data) {
  if (data.release) return data.release;
  if (data.installation?.kind === 'container') return 'container image';
  if (data.installation?.kind === 'source-checkout') return 'source checkout';
  return 'source';
}

function setProgress({ state, title, detail, progress = 0, phase = 'scan' }) {
  const card = el('updater-progress-card');
  const titleEl = el('updater-progress-title');
  const detailEl = el('updater-progress-detail');
  const percent = el('updater-progress-percent');
  const fill = el('updater-progress-fill');
  const meter = document.querySelector('.updater-progress-meter');
  const bounded = Math.max(0, Math.min(100, Number(progress) || 0));
  if (card) card.dataset.state = state;
  if (titleEl) titleEl.textContent = title;
  if (detailEl) detailEl.textContent = detail;
  if (percent) percent.textContent = `${bounded}%`;
  if (fill) fill.style.width = `${bounded}%`;
  if (meter) meter.setAttribute('aria-valuenow', String(bounded));
  renderPhases(phase, state);
}

function renderPhases(activePhase, state) {
  const activeIndex = Math.max(0, PHASES.indexOf(activePhase));
  document.querySelectorAll('#updater-phase-list li').forEach((item) => {
    const index = PHASES.indexOf(item.dataset.phase);
    let itemState = 'pending';
    if (state === 'complete') itemState = 'complete';
    else if (index < activeIndex) itemState = 'complete';
    else if (index === activeIndex) {
      itemState = state === 'error' ? 'failed' : 'working';
    }
    item.dataset.state = itemState;
  });
}

function operationPhase(phase) {
  if (['download', 'stage'].includes(phase)) return 'verify';
  if (['backup', 'rehearsal'].includes(phase)) return 'preserve';
  if (['migration', 'activate', 'health', 'rollback'].includes(phase)) return 'activate';
  if (phase === 'complete') return 'complete';
  return 'verify';
}

function manualGuidance(installation = {}) {
  const guidance = el('updater-manual-guidance');
  const title = el('updater-manual-title');
  const copy = el('updater-manual-copy');
  const command = el('updater-manual-command');
  if (!guidance || !title || !copy || !command) return;
  guidance.hidden = installation.supported === true;
  if (guidance.hidden) return;
  if (installation.kind === 'container') {
    title.textContent = 'Update from the Docker host';
    copy.textContent = 'Pandamonium will not mutate its own running container. Pull the release on the host, then rebuild and recreate it.';
    command.textContent = 'git pull --ff-only && PANDAMONIUM_SOURCE_REVISION="$(git rev-parse HEAD)" docker compose up -d --build';
  } else if (installation.kind === 'source-checkout') {
    title.textContent = 'Update this source checkout';
    copy.textContent = 'Review the release notes, pull the repository on the host, refresh dependencies, and restart Pandamonium.';
    command.textContent = 'git pull --ff-only';
  } else {
    title.textContent = 'Host-managed update';
    copy.textContent = installation.reason || 'Use this platform\'s normal update procedure from the host.';
    command.textContent = '';
  }
  command.hidden = !command.textContent;
}

function renderFacts(data = {}) {
  const version = data.version ? `v${data.version}` : 'Unknown';
  const commit = data.commit ? shortCommit(data.commit) : 'Not embedded';
  const installation = data.installation || {};
  if (el('updater-installed-version')) el('updater-installed-version').textContent = version;
  if (el('updater-installed-commit')) el('updater-installed-commit').textContent = commit;
  if (el('updater-installation-kind')) {
    el('updater-installation-kind').textContent = installationLabel(installation.kind);
  }
  if (el('updater-update-mode')) {
    el('updater-update-mode').textContent = installation.supported ? 'One-click signed' : 'Host-managed';
  }
  manualGuidance(installation);

  const sidebarVersion = el('sidebar-update-version');
  const sidebarCommit = el('sidebar-update-commit');
  if (sidebarVersion) sidebarVersion.textContent = `Version ${version}`;
  if (sidebarCommit) {
    sidebarCommit.textContent = `Deployed ${deploymentLabel(data)} · ${shortCommit(data.commit)}`;
  }
  if (data.version) window._appVersion = data.version;
}

function renderReleaseLink(url) {
  const link = el('updater-release-link');
  if (!link) return;
  link.hidden = !url;
  link.href = url || 'https://github.com/MADPANDA3D/Pandamonium/releases';
}

function renderReleaseSummary(data = {}) {
  const summary = el('updater-release-summary');
  const check = data.release_check || {};
  if (!summary) return;
  if (check.status === 'unavailable' || data.update_status === 'unavailable') {
    summary.textContent = 'Release check could not reach GitHub. Your installed build is unchanged; retry when connectivity returns.';
    setPill('warning', 'Check unavailable');
    setProgress({
      state: 'error',
      title: 'Release check unavailable',
      detail: check.message || data.compatibility_reason || 'GitHub release metadata is unavailable.',
      progress: 0,
      phase: 'scan',
    });
    return;
  }
  if (data.update_available) {
    const mode = data.can_update
      ? 'The signed release is compatible and ready to install.'
      : 'This installation is updated from its host.';
    summary.textContent = `v${data.latest_version} is available. ${mode}`;
    setPill('available', `v${data.latest_version} available`);
    setProgress({
      state: 'ready',
      title: data.can_update ? 'Signed update ready' : 'Host update available',
      detail: data.can_update
        ? 'Review the target, then start the protected atomic update.'
        : (data.installation?.reason || 'Follow the host-managed update steps below.'),
      progress: 20,
      phase: 'verify',
    });
    return;
  }
  if (data.update_status === 'current') {
    summary.textContent = `v${data.latest_version || data.version} is current on the ${data.channel || 'stable'} channel.`;
    setPill('connected', 'Up to date');
    setProgress({
      state: 'complete',
      title: 'Pandamonium is up to date',
      detail: 'The installed build matches the latest signed release.',
      progress: 100,
      phase: 'complete',
    });
    return;
  }
  summary.textContent = data.compatibility_reason || 'Release state is not available yet.';
  setPill('warning', 'Needs attention');
}

function renderRelease(data, { preserveOperation = false } = {}) {
  lastRelease = data;
  renderFacts(data);
  renderReleaseLink(data.update_url);
  const action = el('sidebar-update-action');
  const apply = el('updater-apply');
  if (action) {
    action.hidden = !data.update_available;
    action.textContent = data.latest_version
      ? `${data.can_update ? 'Update to' : 'View'} v${data.latest_version}`
      : 'Review update';
  }
  if (apply) {
    apply.hidden = !(data.update_available && data.can_update);
    apply.textContent = data.latest_version ? `Install v${data.latest_version}` : 'Install update';
  }
  if (data.update_available) {
    setState(
      `v${data.latest_version} available · ${shortCommit(data.latest_commit)}`,
      data.compatible ? 'available' : 'unknown',
    );
    const detail = el('sidebar-update-detail');
    const compatibility = data.compatible
      ? (data.can_update ? 'Signed update ready' : data.installation?.reason)
      : data.compatibility_reason;
    if (detail && compatibility) {
      detail.hidden = false;
      detail.textContent = compatibility;
    }
  } else if (data.update_status === 'current') {
    setState('Up to date', 'current');
  } else if (data.update_status === 'unavailable') {
    setState('Release check unavailable', 'unknown');
  } else {
    setState(data.compatibility_reason || 'Update check unavailable', 'unknown');
  }
  if (!preserveOperation) renderReleaseSummary(data);
}

function renderOperation(operation = {}) {
  lastOperation = operation;
  const detail = el('sidebar-update-detail');
  const backup = el('sidebar-update-backup');
  const modalBackup = el('updater-backup');
  const rollback = el('sidebar-update-rollback');
  const modalRollback = el('updater-rollback');
  const action = el('sidebar-update-action');
  const checkButton = el('sidebar-update-check');
  const modalCheck = el('updater-check');
  const apply = el('updater-apply');
  const active = ACTIVE_STATUSES.has(operation.status);
  [action, checkButton, modalCheck, apply].filter(Boolean).forEach(button => {
    button.disabled = active;
  });
  if (active) {
    const progress = Number(operation.progress) || 0;
    if (detail) {
      detail.hidden = false;
      detail.textContent = `${operation.message || operation.phase || 'Working'} · ${progress}%`;
    }
    setState('Update in progress', 'checking');
    setPill('connecting', 'Updating');
    setProgress({
      state: 'working',
      title: operation.message || 'Applying signed update',
      detail: 'Pandamonium may briefly disconnect while the service restarts. This panel will reconnect automatically.',
      progress,
      phase: operationPhase(operation.phase),
    });
  } else if (operation.status === 'failed') {
    setState(operation.rollback_error ? 'Update and rollback failed' : 'Update failed', 'unknown');
    setPill('error', 'Update failed');
    setProgress({
      state: 'error',
      title: operation.rollback_error ? 'Update and rollback failed' : 'Update failed safely',
      detail: operation.message || 'The updater stopped without activating the release.',
      progress: Number(operation.progress) || 100,
      phase: operationPhase(operation.phase),
    });
  } else if (SUCCESS_STATUSES.has(operation.status)) {
    const rolledBack = operation.status !== 'succeeded';
    setState(rolledBack ? 'Rollback complete' : 'Update complete', 'current');
    setPill('connected', rolledBack ? 'Rolled back' : 'Updated');
    setProgress({
      state: 'complete',
      title: rolledBack ? 'Rollback verified' : 'Update installed',
      detail: operation.message || (rolledBack ? 'The previous release is healthy.' : 'The new release passed its health check.'),
      progress: 100,
      phase: 'complete',
    });
  }
  const backupLocation = operation.backup_location || '';
  if (backup) {
    backup.hidden = !backupLocation;
    backup.textContent = backupLocation ? `Backup: ${backupLocation}` : '';
  }
  if (modalBackup) {
    modalBackup.hidden = !backupLocation;
    modalBackup.textContent = backupLocation ? `Protected backup: ${backupLocation}` : '';
  }
  const showRollback = Boolean(operation.rollback_available && !active);
  if (rollback) rollback.hidden = !showRollback;
  if (modalRollback) modalRollback.hidden = !showRollback;
}

async function api(url, options = {}, timeoutMs = 8000) {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  const { headers = {}, ...requestOptions } = options;
  try {
    const response = await fetch(url, {
      credentials: 'same-origin',
      ...requestOptions,
      headers: { Accept: 'application/json', ...headers },
      signal: controller.signal,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || 'Update request failed');
    return data;
  } catch (error) {
    if (error?.name === 'AbortError') throw new Error('Update status timed out');
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

function stopPolling() {
  if (pollTimer) clearTimeout(pollTimer);
  pollTimer = null;
}

function schedulePoll(delay = POLL_INTERVAL_MS) {
  stopPolling();
  pollTimer = window.setTimeout(pollStatus, delay);
}

async function pollStatus() {
  if (pollInFlight) return schedulePoll();
  pollInFlight = true;
  try {
    const operation = await api('/api/update/status', {}, 5000);
    renderOperation(operation);
    if (ACTIVE_STATUSES.has(operation.status)) {
      schedulePoll();
    } else if (SUCCESS_STATUSES.has(operation.status)) {
      try {
        const release = await api('/api/version', {}, 5000);
        renderRelease(release, { preserveOperation: true });
        renderOperation(operation);
        stopPolling();
      } catch (_) {
        setPill('connecting', 'Reconnecting');
        setState('Reconnecting after update…', 'checking');
        setProgress({
          state: 'reconnecting',
          title: 'Reconnecting to Pandamonium',
          detail: 'The updater finished; waiting for the new application process to answer.',
          progress: 96,
          phase: 'complete',
        });
        schedulePoll(650);
      }
    } else {
      stopPolling();
    }
  } catch (_) {
    if (ACTIVE_STATUSES.has(lastOperation.status)) {
      setPill('connecting', 'Reconnecting');
      setState('Reconnecting after restart…', 'checking');
      setProgress({
        state: 'reconnecting',
        title: 'Pandamonium is restarting',
        detail: 'This brief disconnect is expected. The updater will resume status checks automatically.',
        progress: Number(lastOperation.progress) || 80,
        phase: operationPhase(lastOperation.phase),
      });
      schedulePoll(650);
    }
  } finally {
    pollInFlight = false;
  }
}

function closeModal() {
  const modal = el(MODAL_ID);
  if (!modal || modal.classList.contains('hidden')) return;
  modal.classList.add('hidden');
  modal.setAttribute('aria-hidden', 'true');
  const opener = modalOpener;
  modalOpener = null;
  window.setTimeout(() => opener?.focus?.(), 0);
}

function openModal({ checkNow = false, opener = null } = {}) {
  const modal = el(MODAL_ID);
  if (!modal) return;
  modalOpener = opener || document.activeElement || el('sidebar-update-check');
  modal.classList.remove('hidden');
  modal.removeAttribute('aria-hidden');
  window.setTimeout(() => el('close-updater-modal')?.focus(), 60);
  if (lastRelease) renderRelease(lastRelease, { preserveOperation: ACTIVE_STATUSES.has(lastOperation.status) });
  if (ACTIVE_STATUSES.has(lastOperation.status)) renderOperation(lastOperation);
  if (checkNow) check();
}

async function check() {
  const footerButton = el('sidebar-update-check');
  const modalButton = el('updater-check');
  [footerButton, modalButton].filter(Boolean).forEach(button => { button.disabled = true; });
  setState('Checking for updates…', 'checking');
  setPill('connecting', 'Scanning');
  setProgress({
    state: 'working',
    title: 'Scanning stable releases',
    detail: 'Contacting GitHub, then validating the signed Pandamonium release contract.',
    progress: 8,
    phase: 'scan',
  });
  try {
    renderRelease(await api('/api/update/check', { method: 'POST' }, 15000));
  } catch (error) {
    setState('Release check unavailable', 'unknown');
    setPill('warning', 'Check unavailable');
    const summary = el('updater-release-summary');
    if (summary) summary.textContent = 'Release check could not reach Pandamonium. Your installed build is unchanged; retry when connectivity returns.';
    setProgress({
      state: 'error',
      title: 'Release check unavailable',
      detail: error instanceof Error ? error.message : 'Update request failed.',
      progress: 0,
      phase: 'scan',
    });
  } finally {
    [footerButton, modalButton].filter(Boolean).forEach(button => {
      button.disabled = ACTIVE_STATUSES.has(lastOperation.status);
    });
  }
}

async function update() {
  if (!lastRelease?.latest_version || !lastRelease?.latest_commit || !window.confirm(
    `Install signed v${lastRelease.latest_version} (${shortCommit(lastRelease.latest_commit)})? Pandamonium will create and verify a rollback backup first.`,
  )) return;
  try {
    const operation = await api('/api/update/apply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        version: lastRelease.latest_version,
        commit: lastRelease.latest_commit,
      }),
    }, 15000);
    renderOperation(operation);
    schedulePoll(250);
  } catch (error) {
    setPill('error', 'Request failed');
    setProgress({
      state: 'error',
      title: 'Update did not start',
      detail: error instanceof Error ? error.message : 'Update request failed.',
      progress: 0,
      phase: 'verify',
    });
  }
}

async function rollback() {
  if (!window.confirm('Roll back to the retained previous immutable release?')) return;
  try {
    const operation = await api('/api/update/rollback', { method: 'POST' }, 15000);
    renderOperation(operation);
    schedulePoll(250);
  } catch (error) {
    setPill('error', 'Rollback failed');
    setProgress({
      state: 'error',
      title: 'Rollback did not start',
      detail: error instanceof Error ? error.message : 'Rollback request failed.',
      progress: 0,
      phase: 'activate',
    });
  }
}

async function init() {
  if (initialized) return;
  initialized = true;
  el('sidebar-update-check')?.addEventListener('click', event => {
    openModal({ checkNow: true, opener: event.currentTarget });
  });
  el('sidebar-update-action')?.addEventListener('click', event => {
    openModal({ checkNow: false, opener: event.currentTarget });
  });
  el('close-updater-modal')?.addEventListener('click', closeModal);
  el('updater-check')?.addEventListener('click', check);
  el('updater-apply')?.addEventListener('click', update);
  el('sidebar-update-rollback')?.addEventListener('click', event => {
    openModal({ checkNow: false, opener: event.currentTarget });
  });
  el('updater-rollback')?.addEventListener('click', rollback);
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !el(MODAL_ID)?.classList.contains('hidden')) {
      event.preventDefault();
      closeModal();
    }
  });
  window.addEventListener('online', () => {
    if (ACTIVE_STATUSES.has(lastOperation.status)) schedulePoll(0);
  });
  window.addEventListener('pageshow', () => {
    if (ACTIVE_STATUSES.has(lastOperation.status)) schedulePoll(0);
  });
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && ACTIVE_STATUSES.has(lastOperation.status)) schedulePoll(0);
  });
  try {
    renderRelease(await api('/api/version', {}, 15000));
    await pollStatus();
  } catch (_) {
    setState('Release check unavailable', 'unknown');
  }
}

export default { init };
