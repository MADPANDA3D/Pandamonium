import * as Modals from './modalManager.js';

const byId = (id) => document.getElementById(id);
const MODAL_ID = 'mad-mcp-modal';

function setPill(state, label) {
  const pill = byId('mad-mcp-status-pill');
  if (pill) {
    pill.dataset.state = state;
    pill.textContent = label;
  }
  const sidebar = byId('mad-mcp-sidebar-status');
  if (sidebar) {
    sidebar.dataset.state = state;
    sidebar.setAttribute('aria-label', label);
  }
}

function setPhases(state) {
  document.querySelectorAll('#mad-mcp-phase-list li').forEach((item) => {
    item.dataset.state = state;
  });
}

function setProgress({ state, title, detail }) {
  const card = byId('mad-mcp-progress-card');
  if (card) card.dataset.state = state;
  const titleEl = byId('mad-mcp-progress-title');
  const detailEl = byId('mad-mcp-progress-detail');
  if (titleEl) titleEl.textContent = title;
  if (detailEl) detailEl.textContent = detail;
}

function serviceCard(service) {
  const card = document.createElement('article');
  card.className = 'mad-mcp-service-card';
  card.dataset.state = service.configured ? 'configured' : 'available';

  const identity = document.createElement('div');
  const name = document.createElement('strong');
  const id = document.createElement('span');
  name.textContent = service.name || service.id;
  id.textContent = service.id;
  identity.append(name, id);

  const metrics = document.createElement('div');
  metrics.className = 'mad-mcp-service-metrics';
  const status = document.createElement('span');
  status.textContent = service.configured ? 'Ready' : 'Visible';
  const tools = document.createElement('span');
  tools.textContent = `${Number(service.tool_count || 0)} tools`;
  metrics.append(status, tools);

  card.append(identity, metrics);
  return card;
}

function renderCatalog(payload) {
  const services = Array.isArray(payload.services) ? payload.services : [];
  const list = byId('mad-mcp-service-list');
  if (list) {
    list.replaceChildren(...services.map(serviceCard));
  }

  const serviceCount = byId('mad-mcp-service-count');
  const toolCount = byId('mad-mcp-tool-count');
  const summary = byId('mad-mcp-catalog-summary');
  if (serviceCount) serviceCount.textContent = String(payload.service_count || services.length || 0);
  if (toolCount) toolCount.textContent = String(payload.catalog_tool_count || 0);
  if (summary) {
    const configured = Number(payload.configured_service_count || 0);
    summary.textContent = services.length
      ? `${configured} configured service${configured === 1 ? '' : 's'} ready for the native agent harness.`
      : 'No Portal catalog loaded.';
  }
}

function renderStatus(payload) {
  const configured = payload.configured === true;
  const connected = payload.status === 'connected';
  const disconnect = byId('mad-mcp-disconnect-btn');
  if (disconnect) disconnect.hidden = !configured;

  if (connected) {
    setPill('connected', 'Connected');
    setPhases('complete');
    setProgress({
      state: 'complete',
      title: 'MAD MCP is connected',
      detail: `${Number(payload.tool_count || 0)} Portal broker tools are registered with Pandamonium.`,
    });
  } else if (configured) {
    setPill('warning', 'Reconnect needed');
    setPhases('pending');
    setProgress({
      state: 'warning',
      title: 'Portal credential is saved',
      detail: 'The encrypted key is present, but the native MCP session is not connected yet.',
    });
  } else {
    setPill('disconnected', 'Disconnected');
    setPhases('pending');
    setProgress({
      state: 'idle',
      title: 'Waiting for a Portal key',
      detail: 'Connect once and the native MCP harness will discover your available services and tools.',
    });
  }
  renderCatalog(payload);
}

function setBusy(busy) {
  const connect = byId('mad-mcp-connect-btn');
  const disconnect = byId('mad-mcp-disconnect-btn');
  const input = byId('mad-mcp-master-key');
  if (connect) {
    connect.disabled = busy;
    connect.textContent = busy ? 'Connecting…' : 'Connect';
  }
  if (disconnect) disconnect.disabled = busy;
  if (input) input.disabled = busy;
}

async function readJson(response) {
  try {
    return await response.json();
  } catch (_) {
    return {};
  }
}

async function refreshStatus() {
  try {
    const response = await fetch('/api/mcp/portal/status', {
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
    });
    if (!response.ok) throw new Error('Status unavailable');
    renderStatus(await readJson(response));
  } catch (_) {
    setPill('warning', 'Status unavailable');
    setProgress({
      state: 'warning',
      title: 'Could not read Portal status',
      detail: 'Pandamonium could not reach its local MCP status endpoint.',
    });
  }
}

async function connectPortal(event) {
  event.preventDefault();
  const input = byId('mad-mcp-master-key');
  let key = input ? input.value.trim() : '';
  if (key.length < 20) {
    input?.focus();
    setProgress({
      state: 'error',
      title: 'A complete master key is required',
      detail: 'Paste the master key generated by your MAD MCP Portal account.',
    });
    return;
  }

  setBusy(true);
  setPill('connecting', 'Connecting');
  setPhases('working');
  setProgress({
    state: 'working',
    title: 'Connecting MAD MCP',
    detail: 'Authenticating, initializing the native MCP session, and reading the Portal catalog…',
  });

  const requestBody = JSON.stringify({ master_key: key });
  key = '';
  if (input) input.value = '';

  try {
    const response = await fetch('/api/mcp/portal/connect', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: requestBody,
    });
    const payload = await readJson(response);
    if (!response.ok) throw new Error(payload.detail || 'Portal connection failed');
    renderStatus(payload);
  } catch (error) {
    setPill('error', 'Connection failed');
    setPhases('failed');
    setProgress({
      state: 'error',
      title: 'MAD MCP did not connect',
      detail: error instanceof Error ? error.message : 'Portal connection failed.',
    });
  } finally {
    setBusy(false);
  }
}

async function disconnectPortal() {
  if (!window.confirm('Disconnect MAD MCP and remove the encrypted Portal key from Pandamonium?')) return;
  setBusy(true);
  try {
    const response = await fetch('/api/mcp/portal', {
      method: 'DELETE',
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
    });
    if (!response.ok) {
      const payload = await readJson(response);
      throw new Error(payload.detail || 'Disconnect failed');
    }
    renderStatus(await readJson(response));
  } catch (error) {
    setProgress({
      state: 'error',
      title: 'MAD MCP could not disconnect',
      detail: error instanceof Error ? error.message : 'Disconnect failed.',
    });
  } finally {
    setBusy(false);
  }
}

function closeModal() {
  const modal = byId(MODAL_ID);
  if (modal) modal.classList.add('hidden');
}

function openModal() {
  if (Modals.toggle(MODAL_ID)) {
    refreshStatus();
    return;
  }
  const modal = byId(MODAL_ID);
  if (!modal) return;
  if (!modal.classList.contains('hidden')) {
    closeModal();
    return;
  }
  modal.classList.remove('hidden');
  refreshStatus();
  window.setTimeout(() => byId('mad-mcp-master-key')?.focus(), 80);
}

function init() {
  const modal = byId(MODAL_ID);
  if (!modal) return;
  Modals.register(MODAL_ID, {
    railBtnId: 'rail-mad-mcp',
    sidebarBtnId: 'tool-mad-mcp-btn',
    label: 'MAD MCP',
    closeFn: closeModal,
    restoreFn: refreshStatus,
  });

  [byId('tool-mad-mcp-btn'), byId('rail-mad-mcp')].filter(Boolean).forEach((button) => {
    button.addEventListener('click', openModal);
    button.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        openModal();
      }
    });
  });
  byId('close-mad-mcp-modal')?.addEventListener('click', closeModal);
  byId('mad-mcp-connect-form')?.addEventListener('submit', connectPortal);
  byId('mad-mcp-disconnect-btn')?.addEventListener('click', disconnectPortal);
  refreshStatus();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init, { once: true });
} else {
  init();
}
