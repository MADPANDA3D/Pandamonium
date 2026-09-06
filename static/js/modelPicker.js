// Model Picker — chatbox model selector dropdown
// Extracted from sessions.js

import { providerLogo } from './providers.js';
import uiModule from './ui.js';
import settingsModule from './settings.js';

const API_BASE = window.location.origin;

// Preserve recent programmatic model switches for legacy compatibility.
const RECENT_KEY = 'odysseus-model-recent';
const RECENT_MAX = 5;
const AGENT_SELECTIONS_KEY = 'odysseus-agent-selections';
const AGENT_SELECTIONS_MAX = 100;

function _loadList(key) {
  try {
    const a = JSON.parse(localStorage.getItem(key) || '[]');
    return Array.isArray(a) ? a : [];
  } catch { return []; }
}
function _saveList(key, list) {
  try { localStorage.setItem(key, JSON.stringify(list)); } catch { /* quota / private mode */ }
}
function _loadRecent() { return _loadList(RECENT_KEY); }
function _pushRecent(mid) {
  if (!mid) return;
  const next = _loadRecent().filter(x => x !== mid);
  next.unshift(mid);
  _saveList(RECENT_KEY, next.slice(0, RECENT_MAX));
}
// ── Shared keyboard nav for model pickers ──
function _handlePickerKeydown(e, listEl, itemSelector, closeFn) {
  if (e.key === 'Escape') { closeFn(); return; }
  if (e.key === 'Enter') {
    e.preventDefault();
    const active = listEl.querySelector(itemSelector + '.kb-active') || listEl.querySelector(itemSelector);
    if (active) active.click();
    return;
  }
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    const items = [...listEl.querySelectorAll(itemSelector)].filter(el => el.style.display !== 'none');
    if (!items.length) return;
    const cur = items.findIndex(el => el.classList.contains('kb-active'));
    items.forEach(el => el.classList.remove('kb-active'));
    let next;
    if (e.key === 'ArrowDown') next = cur < items.length - 1 ? cur + 1 : 0;
    else next = cur > 0 ? cur - 1 : items.length - 1;
    items[next].classList.add('kb-active');
    items[next].scrollIntoView({ block: 'nearest' });
  }
}

// Dependencies injected via initModelPicker()
let _deps = null;
let _autoSelectingDefault = false;
let _defaultChatPickInFlight = false;
let _selectorItems = [];
let _selectorCatalogState = 'loading';
let _selectorCatalogError = '';
let _agentCatalogVerified = false;
let _lastConversationTargetEvent = '';
const _PENDING_AGENT_KEY = '__pending__';

function _loadAgentSelections() {
  try {
    const saved = JSON.parse(localStorage.getItem(AGENT_SELECTIONS_KEY) || '{}');
    if (!saved || typeof saved !== 'object' || Array.isArray(saved)) return new Map();
    return new Map(Object.entries(saved).slice(-AGENT_SELECTIONS_MAX).flatMap(([key, selection]) => {
      const sessionId = String(key || '').trim();
      const target = String(selection?.target || '').trim();
      if (!sessionId || sessionId === _PENDING_AGENT_KEY || !/^[a-z][a-z0-9_-]{0,63}$/.test(target)) return [];
      return [[sessionId, {
        target,
        label: String(selection.label || target).slice(0, 80),
        kind: selection.kind === 'worker' ? 'worker' : 'agent',
        available: selection.available !== false,
        reason: String(selection.reason || '').slice(0, 120),
      }]];
    }));
  } catch { return new Map(); }
}

function _saveAgentSelections() {
  try {
    const saved = Object.fromEntries(
      [..._selectedAgents.entries()]
        .filter(([key]) => key !== _PENDING_AGENT_KEY)
        .slice(-AGENT_SELECTIONS_MAX),
    );
    localStorage.setItem(AGENT_SELECTIONS_KEY, JSON.stringify(saved));
  } catch { /* quota / private mode */ }
}

const _selectedAgents = _loadAgentSelections();

function _agentSelectionKey() {
  return (_deps && _deps.getCurrentSessionId && _deps.getCurrentSessionId()) || _PENDING_AGENT_KEY;
}

function _selectedAgent() {
  if (!_agentCatalogVerified) return null;
  const selected = _selectedAgents.get(_agentSelectionKey()) || _selectedAgents.get(_PENDING_AGENT_KEY);
  if (selected) return selected;
  const defaultIdentity = _selectorItems.find(item => item.target === 'jarvis' && !item.disabled)
    || _selectorItems.find(item => !item.disabled);
  return defaultIdentity ? {
    target: defaultIdentity.target,
    label: defaultIdentity.display,
    kind: defaultIdentity.kind,
    available: true,
    reason: '',
  } : null;
}

export function getSelectedAgentTarget() {
  return _selectedAgent()?.target || '';
}

export function getSelectedAgentSelection() {
  const selected = _selectedAgent();
  return selected ? { ...selected } : null;
}

export function clearPendingAgentTarget() {
  _selectedAgents.delete(_PENDING_AGENT_KEY);
}

export function preserveSelectedAgentForNewChat() {
  const selected = _selectedAgents.get(_agentSelectionKey());
  if (selected) _selectedAgents.set(_PENDING_AGENT_KEY, { ...selected });
  else clearPendingAgentTarget();
}

export function movePendingAgentTarget(sessionId) {
  const id = String(sessionId || '').trim();
  const pending = _selectedAgents.get(_PENDING_AGENT_KEY);
  if (!id || !pending) return;
  _selectedAgents.set(id, pending);
  _selectedAgents.delete(_PENDING_AGENT_KEY);
  _saveAgentSelections();
}

export function syncSessionAgentTargets(sessionItems = []) {
  for (const session of sessionItems) {
    const sessionId = String(session?.id || '').trim();
    const target = String(session?.agent_target || 'jarvis').trim();
    if (!sessionId || !/^[a-z][a-z0-9_-]{0,63}$/.test(target)) continue;
    const known = _selectorItems.find(item => item.target === target);
    _selectedAgents.set(sessionId, {
      target,
      label: known?.display || target,
      kind: known?.kind === 'worker' ? 'worker' : 'agent',
      available: known ? !known.disabled : target === 'jarvis',
      reason: known?.staleReason || (target === 'jarvis' ? '' : 'not currently available'),
    });
  }
  _saveAgentSelections();
}

function _emitConversationTarget(selectedAgent) {
  if (!selectedAgent) return;
  const detail = {
    target: selectedAgent.target,
    label: selectedAgent.label || selectedAgent.target,
    kind: selectedAgent.kind,
    available: selectedAgent.available !== false,
    reason: selectedAgent.reason || '',
  };
  const signature = JSON.stringify(detail);
  if (signature === _lastConversationTargetEvent) return;
  _lastConversationTargetEvent = signature;
  document.dispatchEvent(new CustomEvent('odysseus:conversation-target-changed', { detail }));
}

async function _refreshSelectorCatalog() {
  if (!_agentCatalogVerified) _selectorCatalogState = 'loading';
  try {
    const response = await fetch(`${API_BASE}/api/selector-catalog`, { credentials: 'same-origin' });
    if (!response.ok) throw new Error(`catalog_${response.status}`);
    const payload = await response.json();
    if (payload?.discovery?.schema_version !== 'pandamonium.discovery.v1') {
      throw new Error('invalid_selector_catalog');
    }
    const entities = Array.isArray(payload?.discovery?.entities) ? payload.discovery.entities : [];
    const selections = Array.isArray(payload?.selections) ? payload.selections : [];
    const entityById = new Map(entities.map(entity => [entity.id, entity]));
    _selectorItems = [];
    selections.forEach(selection => {
      const entity = entityById.get(selection.entity_id);
      if (!entity || !['model', 'agent', 'worker'].includes(entity.kind)) return;
      const item = {
        kind: entity.kind,
        target: String(selection.target || ''),
        mid: entity.kind === 'model' ? String(selection.model_id || '') : `${entity.kind}:${selection.target}`,
        modelId: String(selection.model_id || ''),
        endpointId: String(selection.endpoint_id || ''),
        display: String(entity.display_name || 'Configured choice'),
        epName: (selection.capabilities || []).includes('codex')
          ? 'Workstation Codex'
          : ((selection.capabilities || []).includes('hermes')
            ? 'Hermes'
            : ((selection.capabilities || []).includes('claude')
              ? 'Claude'
              : ((selection.capabilities || []).includes('model') ? 'Self-hosted model' : 'Configured identity'))),
        providerText: `${entity.kind} ${entity.health?.state || ''} ${selection.reason || ''}`,
        stale: selection.selectable !== true,
        disabled: selection.selectable !== true,
        staleReason: String(selection.reason || entity.health?.reason || 'unavailable').replace(/_/g, ' '),
        offline: entity.health?.state === 'unavailable',
      };
      if (entity.kind !== 'model' && item.target) {
        _selectorItems.push(item);
      }
    });
    _agentCatalogVerified = true;
    _selectorCatalogState = 'ready';
    _selectorCatalogError = '';
    const byTarget = new Map(_selectorItems.map(item => [item.target, item]));
    for (const [key, selection] of _selectedAgents.entries()) {
      const current = byTarget.get(selection.target);
      _selectedAgents.set(key, current ? {
        ...selection,
        label: current.display,
        kind: current.kind,
        available: !current.disabled,
        reason: current.staleReason || '',
      } : {
        ...selection,
        available: false,
        reason: 'no longer configured',
      });
    }
    _saveAgentSelections();
  } catch (_) {
    _selectorCatalogState = 'error';
    _selectorCatalogError = 'Selector discovery is unavailable. Existing choices were not rerouted.';
  }
}

function _isChatEndpoint(item) {
  return (item && (item.model_type || 'llm')) === 'llm';
}

function _modelExists(modelId, url) {
  if (!modelId || !window.modelsModule || !window.modelsModule.getCachedItems) return false;
  const items = window.modelsModule.getCachedItems() || [];
  if (!items.length) return true;
  const targetUrl = (url || '').replace(/\/+$/, '');
  return items.some(item => {
    if (item.offline || !_isChatEndpoint(item)) return false;
    const itemUrl = (item.url || '').replace(/\/+$/, '');
    const models = (item.models || []).concat(item.models_extra || []);
    return models.includes(modelId) && (!targetUrl || itemUrl === targetUrl);
  });
}

function _modelDisplayName(modelId) {
  if (!modelId || !window.modelsModule || !window.modelsModule.getCachedItems) {
    return modelId ? modelId.split('/').pop() : 'Select model';
  }
  for (const item of window.modelsModule.getCachedItems() || []) {
    const models = (item.models || []).concat(item.models_extra || []);
    const displays = (item.models_display || []).concat(item.models_extra_display || []);
    const index = models.indexOf(modelId);
    if (index >= 0) return (displays[index] || modelId).split('/').pop();
  }
  return modelId.split('/').pop();
}

function _firstAvailableModel() {
  if (!window.modelsModule || !window.modelsModule.getCachedItems) return null;
  const items = window.modelsModule.getCachedItems() || [];
  for (const item of items) {
    if (item.offline) continue;
    const models = (item.models || []).concat(item.models_extra || []);
    if (!models.length) continue;
    return {
      url: item.url,
      modelId: models[0],
      endpointId: item.endpoint_id || '',
    };
  }
  return null;
}

async function _ensureModelCacheForFallback() {
  if (!window.modelsModule || !window.modelsModule.getCachedItems) return;
  const items = window.modelsModule.getCachedItems() || [];
  if (items.length) return;
  if (typeof window.modelsModule.refreshModels === 'function') {
    try { await window.modelsModule.refreshModels(false); } catch (_) {}
  }
}

async function _ensureDefaultPendingChat() {
  if (!_deps || _defaultChatPickInFlight) return;
  if (_deps.getCurrentSessionId && _deps.getCurrentSessionId()) return;
  const pending = _deps.getPendingChat && _deps.getPendingChat();
  if (pending && pending.modelId && pending.source === 'manual') return;
  _defaultChatPickInFlight = true;
  try {
    await _ensureModelCacheForFallback();
    let dc = null;
    try {
      const res = await fetch(`${API_BASE}/api/default-chat`, { credentials: 'same-origin' });
      if (res.ok) dc = await res.json();
    } catch (_) {}
    // New Chat deliberately resolves discovery after the visible navigation.
    // Do not let that late response overwrite a session/model the user picked
    // while discovery was in flight.
    if (_deps.getCurrentSessionId && _deps.getCurrentSessionId()) return;
    const latestPending = _deps.getPendingChat && _deps.getPendingChat();
    if (latestPending && latestPending.source === 'manual') return;
    if (dc && dc.endpoint_url && dc.model && _modelExists(dc.model, dc.endpoint_url)) {
      const pendingUrl = String((latestPending && latestPending.url) || '').replace(/\/+$/, '');
      const defaultUrl = String(dc.endpoint_url || '').replace(/\/+$/, '');
      _deps.setPendingChat({
        url: dc.endpoint_url,
        modelId: dc.model,
        endpointId: dc.endpoint_id || '',
        source: 'default',
      });
      try { window.__odysseusDefaultChat = dc; } catch (_) {}
      if (!latestPending || latestPending.modelId !== dc.model || pendingUrl !== defaultUrl || latestPending.source !== 'default') {
        updateModelPicker();
      }
      return;
    }
    if (latestPending && latestPending.modelId) return;
    // No configured default, or the configured default is gone/offline:
    // preserve the convenience fallback and keep the picker usable.
    const fallback = _firstAvailableModel();
    if (fallback) {
      _deps.setPendingChat({ ...fallback, source: 'fallback' });
      updateModelPicker();
    }
  } finally {
    _defaultChatPickInFlight = false;
  }
}

/**
 * Initialize the model picker dropdown.
 * @param {Object} deps
 * @param {function} deps.getCurrentSessionId - returns current session ID
 * @param {function} deps.getSessions - returns sessions array
 * @param {function} deps.getPendingChat - returns _pendingChat object
 * @param {function} deps.setPendingChat - sets _pendingChat object
 * @param {function} deps.createDirectChat - creates a new direct chat session
 */
export function initModelPicker(deps) {
  _deps = deps;
  _initModelPickerDropdown();
  _refreshSelectorCatalog().then(() => updateModelPicker()).catch(() => {});
}

function _initModelPickerDropdown() {
  const wrap = document.getElementById('model-picker-wrap');
  const btn = document.getElementById('model-picker-btn');
  const menu = document.getElementById('model-picker-menu');
  const search = document.getElementById('model-picker-search');
  const listEl = document.getElementById('model-picker-list');
  const searchRow = menu ? menu.querySelector('.model-picker-search-row') : null;
  const refreshBtn = document.getElementById('model-picker-refresh-btn');
  if (!wrap || !btn || !menu || !search || !listEl) return;
  if (wrap.dataset.modelPickerBound === 'true') return;
  wrap.dataset.modelPickerBound = 'true';

  let _closeFallbackTimer = null;
  let _closeAnimationHandler = null;

  function _cancelPendingClose() {
    if (_closeFallbackTimer) clearTimeout(_closeFallbackTimer);
    _closeFallbackTimer = null;
    if (_closeAnimationHandler) menu.removeEventListener('animationend', _closeAnimationHandler);
    _closeAnimationHandler = null;
  }

  function _finishClose() {
    _cancelPendingClose();
    menu.classList.remove('closing');
    menu.classList.add('hidden');
    search.value = '';
    document.dispatchEvent(new CustomEvent('odysseus:model-picker-closed'));
  }

  function _close() {
    if (menu.classList.contains('hidden')) return;
    _cancelPendingClose();
    // Restore scroll button
    const _scrollBtn = document.getElementById('scroll-bottom-btn');
    if (_scrollBtn) _scrollBtn.style.display = '';
    menu.classList.add('closing');
    _closeAnimationHandler = _finishClose;
    menu.addEventListener('animationend', _closeAnimationHandler, { once: true });
    // Fallback if animationend doesn't fire
    _closeFallbackTimer = setTimeout(_finishClose, 200);
  }

  function _fitMenuToViewport() {
    menu.style.right = '';
    const width = menu.offsetWidth;
    if (!width) return;
    const inset = 8;
    const anchorRight = wrap.getBoundingClientRect().right;
    const desiredRight = Math.min(
      Math.max(anchorRight, inset + width),
      Math.max(inset + width, window.innerWidth - inset),
    );
    menu.style.right = `${anchorRight - desiredRight}px`;
  }

  function _openPickerShortcut(kind) {
    _close();
    try {
      if (kind === 'cookbook') {
        if (window.cookbookModule && typeof window.cookbookModule.open === 'function') {
          window.cookbookModule.open();
        } else {
          const btn = document.getElementById('tool-cookbook-btn') || document.getElementById('rail-cookbook');
          if (btn) btn.click();
          else location.hash = '#cookbook';
        }
      } else if (kind === 'settings') {
        if (settingsModule && typeof settingsModule.open === 'function') settingsModule.open();
      } else if (window.adminModule && typeof window.adminModule.open === 'function') {
        window.adminModule.open('services');
      } else if (settingsModule && typeof settingsModule.open === 'function') {
        settingsModule.open('services');
      }
    } catch (_) {}
  }

  // Local endpoint health — only probed for LOCAL endpoints, since
  // cloud APIs are essentially always up. Cached briefly on the
  // server side too (8s TTL). Picker opens trigger a refresh.
  let _localProbe = {};            // {endpoint_id: {alive, latency_ms, error}}
  let _localProbeFetchedAt = 0;
  const _LOCAL_PROBE_TTL_MS = 5000;

  async function _refreshLocalProbe() {
    try {
      if (window.__odysseusChatBusy || Date.now() < (window.__odysseusChatBusyUntil || 0)) return;
    } catch (_) {}
    const now = Date.now();
    if (now - _localProbeFetchedAt < _LOCAL_PROBE_TTL_MS) return;
    _localProbeFetchedAt = now;
    try {
      const r = await fetch('/api/model-endpoints/probe-local', { credentials: 'same-origin' });
      if (r.ok) _localProbe = (await r.json()) || {};
    } catch (_) { /* leave stale data; picker still works */ }
  }

  function _getConversationTargets() {
    const choices = [..._selectorItems];
    const selected = _selectedAgent();
    if (selected?.available === false && !choices.some(item => item.target === selected.target)) {
      choices.push({
        kind: selected.kind,
        target: selected.target,
        mid: `${selected.kind}:${selected.target}`,
        display: selected.label,
        epName: `Unavailable · ${selected.reason || 'no longer configured'}`,
        providerText: selected.reason || 'no longer configured',
        stale: true,
        disabled: true,
        staleReason: selected.reason || 'no longer configured',
        offline: true,
      });
    }
    const seen = new Set();
    return choices.filter(item => {
      const keys = [`target:${item.target}`, `name:${item.display.trim().toLowerCase()}`];
      if (keys.some(key => seen.has(key))) return false;
      keys.forEach(key => seen.add(key));
      return true;
    });
  }

  function _populate(filter) {
    listEl.innerHTML = '';
    const all = _getConversationTargets();
    const q = (filter || '').trim().toLowerCase();
    const hasAnyChoice = all.length > 0;
    listEl.classList.toggle('is-empty', !hasAnyChoice);
    menu.classList.toggle('no-models', !hasAnyChoice);
    if (search) {
      search.placeholder = hasAnyChoice ? 'Search who you can talk to…' : 'No identities discovered';
    }
    if (searchRow) {
      searchRow.classList.toggle('searching', !!q);
    }

    if (_selectorCatalogState === 'loading') {
      listEl.classList.remove('is-empty');
      menu.classList.remove('no-models');
      const loading = document.createElement('div');
      loading.className = 'model-switch-status';
      loading.setAttribute('role', 'status');
      loading.textContent = 'Discovering who you can talk to…';
      listEl.appendChild(loading);
      return;
    }
    if (_selectorCatalogState === 'error') {
      listEl.classList.remove('is-empty');
      menu.classList.remove('no-models');
      const failure = document.createElement('div');
      failure.className = 'model-switch-status is-error';
      failure.setAttribute('role', 'alert');
      failure.textContent = _selectorCatalogError;
      listEl.appendChild(failure);
      if (!hasAnyChoice) return;
    }
    if (!hasAnyChoice) {
      listEl.classList.remove('is-empty');
      menu.classList.remove('no-models');
      const empty = document.createElement('div');
      empty.className = 'model-switch-status';
      empty.setAttribute('role', 'status');
      empty.textContent = 'No configured identities are available.';
      listEl.appendChild(empty);
      return;
    }

    function _addEmpty(text) {
      const empty = document.createElement('div');
      empty.className = 'model-switch-empty';
      empty.textContent = text;
      listEl.appendChild(empty);
    }
    function _addRow(m) {
      const row = document.createElement('div');
      row.className = 'model-switch-item';
      row.dataset.kind = m.kind || 'model';
      row.setAttribute('role', 'option');
      row.tabIndex = m.disabled === true ? -1 : 0;
      row.setAttribute('aria-disabled', m.disabled === true ? 'true' : 'false');
      if (m.stale) {
        row.classList.add('model-switch-stale');
        row.title = `${m.display} is unavailable: ${m.staleReason}. Pandamonium will not reroute this choice.`;
      }
      const nameSpan = document.createElement('span');
      nameSpan.className = 'mp-model-name';
      nameSpan.textContent = m.display;
      // Long model names are clipped with ellipsis — expose the full name on
      // hover so the suffix/variant tag is still discoverable (#1982).
      nameSpan.title = m.display;
      row.appendChild(nameSpan);
      // Offline state is already conveyed by the row's reduced opacity —
      // a redundant "offline" pill on top of that just added clutter.
      // (Class kept on `row` so the opacity rule still applies; the text
      // badge is gone.)
      const epSpan = document.createElement('span');
      epSpan.className = 'model-switch-ep';
      // Describe the owner behind the conversational identity, not its endpoint.
      const _epDisplay = m.epName || '';
      epSpan.textContent = _epDisplay;
      row.appendChild(epSpan);

      row.addEventListener('click', () => _pick(m));
      row.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          _pick(m);
        }
      });
      listEl.appendChild(row);
    }

    // ── Search mode: flat, filtered results across the whole catalog ──
    if (q) {
      const matches = all.filter(m => {
        return [m.mid, m.display, m.epName, m.providerText]
          .filter(Boolean).join(' ').toLowerCase().includes(q);
      });
      if (matches.length === 0) _addEmpty('No matching choices');
      else matches.forEach(_addRow);
      return;
    }

    all.forEach(_addRow);
  }

  async function _pick(m) {
    const currentSessionId = _deps.getCurrentSessionId();
    const _pendingChat = _deps.getPendingChat();

    // Remember this pick so it surfaces under "Recent" next time the picker
    // opens — the whole point of quick-switch.
    if (m?.disabled) {
      uiModule.showToast(`${m.display} is unavailable: ${m.staleReason}`);
      return;
    }
    if (m && m.mid && m.kind === 'model') _pushRecent(m.mid);

    // Broadcast immediately so listeners (e.g. the tour) can advance without
    // waiting for the async session-create/PATCH that follows.
    try { document.dispatchEvent(new CustomEvent('odysseus:model-picked', { detail: m })); } catch {}

    // Blur search input before closing to dismiss keyboard on mobile
    if (document.activeElement) document.activeElement.blur();
    _close();
    // Refocus main textarea — skip on mobile to avoid keyboard bounce
    if (window.innerWidth >= 768) {
      const _ta = document.getElementById('message');
      if (_ta) setTimeout(() => _ta.focus(), 50);
    }
    if (m.kind === 'agent' || m.kind === 'worker') {
      if (currentSessionId) {
        const fd = new FormData();
        fd.append('agent_target', m.target);
        try {
          const response = await fetch(`${API_BASE}/api/session/${currentSessionId}`, {
            method: 'PATCH',
            body: fd,
          });
          if (!response.ok) throw new Error(`target_${response.status}`);
          const session = _deps.getSessions().find(item => item.id === currentSessionId);
          if (session) session.agent_target = m.target;
        } catch (_) {
          uiModule.showError(`Failed to select ${m.display}`);
          return;
        }
      }
      _selectedAgents.set(_agentSelectionKey(), {
        target: m.target,
        label: m.display,
        kind: m.kind,
        available: true,
        reason: '',
      });
      _saveAgentSelections();
      updateModelPicker();
      uiModule.showToast(
        m.target === 'pc-codex'
          ? `${m.display} selected — choose an approved project or task in the sidebar`
          : `Talking to ${m.display}`,
      );
      return;
    }
    const agentSelectionKey = _agentSelectionKey();
    const clearSelectedAgent = () => {
      _selectedAgents.delete(agentSelectionKey);
      _saveAgentSelections();
    };
    if (!currentSessionId && _pendingChat) {
      // Already have a deferred session — just update the model
      _deps.setPendingChat({ url: m.url, modelId: m.mid, endpointId: m.endpointId, source: 'manual' });
      clearSelectedAgent();
      // Header stays as session name — model switch only updates picker
      updateModelPicker();
      uiModule.showToast(`Using ${m.display}`);
      return;
    } else if (!currentSessionId) {
      // No session yet — create one with this model
      await _deps.createDirectChat(m.url, m.mid, m.endpointId, 'manual');
      clearSelectedAgent();
    } else {
      // Existing session with no model — PATCH it
      const fd = new FormData();
      fd.append('model', m.mid);
      fd.append('endpoint_url', m.url);
      fd.append('agent_target', 'jarvis');
      if (m.endpointId) fd.append('endpoint_id', m.endpointId);
      try {
        const res = await fetch(`${API_BASE}/api/session/${currentSessionId}`, { method: 'PATCH', body: fd });
        if (!res.ok) {
          uiModule.showError('Failed to set model');
          return;
        }
        const sessions = _deps.getSessions();
        const s = sessions.find(x => x.id === currentSessionId);
        if (s) { s.model = m.mid; s.endpoint_url = m.url; s.agent_target = 'jarvis'; }
        clearSelectedAgent();
        // Header stays as session name — model info shown in picker only
      } catch (e) {
        uiModule.showError('Failed to set model: ' + e);
        return;
      }
    }
    // Update picker visibility — model is now set
    updateModelPicker();
    uiModule.showToast(`Using ${m.display}`);
  }

  document.addEventListener('odysseus:auto-select-model', async (e) => {
    const detail = (e && e.detail) || {};
    const currentSessionId = _deps.getCurrentSessionId();
    const sessions = _deps.getSessions();
    const current = sessions.find(x => x.id === currentSessionId);
    const pending = _deps.getPendingChat();
    if (!detail.force && ((current && current.model) || (pending && pending.modelId))) return;

    if (window.modelsModule && window.modelsModule.refreshModels) {
      try { await window.modelsModule.refreshModels(false); } catch (_) {}
    }
    const items = window.modelsModule && window.modelsModule.getCachedItems ? window.modelsModule.getCachedItems() : [];
    const targetEndpointId = detail.endpointId ? String(detail.endpointId) : '';
    const targetModel = detail.modelId || '';
    let match = null;
    for (const item of items) {
      if (item.offline) continue;
      if (targetEndpointId && String(item.endpoint_id || '') !== targetEndpointId) continue;
      const models = (item.models || []).concat(item.models_extra || []);
      const displays = (item.models_display || []).concat(item.models_extra_display || []);
      const idx = targetModel ? models.indexOf(targetModel) : (models.length ? 0 : -1);
      if (idx >= 0) {
        match = {
          mid: models[idx],
          display: (displays[idx] || models[idx]).split('/').pop(),
          url: item.url || detail.url || '',
          endpointId: item.endpoint_id || detail.endpointId || '',
          epName: item.endpoint_name || detail.endpointName || '',
          providerText: [item.endpoint_name || detail.endpointName || '', item.url || detail.url || ''].filter(Boolean).join(' '),
        };
        break;
      }
    }
    if (!match && detail.modelId && detail.url) {
      match = {
        mid: detail.modelId,
        display: String(detail.modelId).split('/').pop(),
        url: detail.url,
        endpointId: detail.endpointId || '',
        epName: detail.endpointName || '',
        providerText: [detail.endpointName || '', detail.url || ''].filter(Boolean).join(' '),
      };
    }
    if (match) await _pick(match);
  });

  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    if (menu.classList.contains('hidden') || menu.classList.contains('closing')) {
      // Force-clear any in-progress close animation
      _cancelPendingClose();
      menu.classList.remove('closing', 'hidden');
      _populate('');
      _fitMenuToViewport();
      if (window.modelsModule && window.modelsModule.refreshModels) {
        window.modelsModule.refreshModels().then(() => {
          if (!menu.classList.contains('hidden')) {
            _populate(search.value || '');
            _fitMenuToViewport();
          }
          updateModelPicker();
        }).catch(() => {});
      }
      _refreshSelectorCatalog().then(() => {
        if (!menu.classList.contains('hidden')) {
          _populate(search.value || '');
          _fitMenuToViewport();
        }
        updateModelPicker();
      }).catch(() => {});
      if (window.innerWidth >= 768) search.focus();
      // Hide scroll button so it doesn't overlap
      const _scrollBtn = document.getElementById('scroll-bottom-btn');
      if (_scrollBtn) _scrollBtn.style.display = 'none';
    } else {
      _close();
    }
  });

  search.addEventListener('input', () => _populate(search.value));
  window.addEventListener('resize', () => {
    if (!menu.classList.contains('hidden')) _fitMenuToViewport();
  });
  search.addEventListener('click', (e) => e.stopPropagation());
  if (refreshBtn) {
    refreshBtn.addEventListener('click', async (e) => {
      e.stopPropagation();
      refreshBtn.disabled = true;
      refreshBtn.classList.add('spinning');
      try {
        if (window.modelsModule && window.modelsModule.refreshModels) {
          await window.modelsModule.refreshModels(true);
        }
        await _refreshLocalProbe();
        await _refreshSelectorCatalog();
        if (!menu.classList.contains('hidden')) _populate(search.value || '');
        updateModelPicker();
      } catch (_) {
        uiModule.showToast('Model refresh failed');
      } finally {
        refreshBtn.disabled = false;
        refreshBtn.classList.remove('spinning');
      }
    });
  }
  search.addEventListener('keydown', (e) => {
    _handlePickerKeydown(e, listEl, '.model-switch-item', _close);
  });
  const addModelsBtn = document.getElementById('model-picker-add-models-btn');
  if (addModelsBtn) {
    addModelsBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      _openPickerShortcut('models');
    });
  }
  document.addEventListener('click', (e) => {
    if (!menu.classList.contains('hidden') && !menu.contains(e.target) && e.target !== btn) {
      _close();
    }
  });
}

/**
 * Update the model picker label to show the current model.
 * Always visible — shows current model name or "Select model" if none.
 * Called after selectSession, createDirectChat, and model switch.
 */
export function updateModelPicker() {
  if (!_deps) return;
  const label = document.getElementById('model-picker-label');
  if (!label) return;
  // Hide model picker when group chat is active
  const wrap = document.getElementById('model-picker-wrap');
  if (window.groupModule && window.groupModule.isActive()) {
    if (wrap) { wrap.style.display = 'none'; }
    return;
  }
  // Reset inline visibility (may have been hidden by typing in previous session)
  if (wrap) {
    wrap.style.display = '';
    wrap.style.opacity = '';
    wrap.style.pointerEvents = '';
  }
  const currentSessionId = _deps.getCurrentSessionId();
  const sessions = _deps.getSessions();
  const _pendingChat = _deps.getPendingChat();
  const s = sessions.find(x => x.id === currentSessionId);
  const selectedAgent = _selectedAgent();
  if (selectedAgent) {
    if (!currentSessionId && !_deps.getPendingChat()) _ensureDefaultPendingChat();
    label.title = selectedAgent.label;
    label.textContent = selectedAgent.label || selectedAgent.target;
    if (selectedAgent.available === false) {
      label.title = `${selectedAgent.label}: ${selectedAgent.reason || 'unavailable'}`;
    }
    _emitConversationTarget(selectedAgent);
    return;
  }
  let modelId = null;
  if (s && s.model) {
    modelId = s.model;
    if (!_modelExists(modelId, s.endpoint_url || '')) {
      modelId = null;
    }
  } else if (_pendingChat && _pendingChat.modelId) {
    modelId = _pendingChat.modelId;
    if (!_modelExists(modelId, _pendingChat.url || '')) {
      _deps.setPendingChat(null);
      modelId = null;
    }
  }
  // SECURITY: deliberately NOT auto-injecting `odysseus-model-favorites[0]`
  // here. localStorage favorites are per-browser, not per-user, so on a
  // shared browser the previous account's first favorited model would
  // silently pre-populate the chatbox of the next user that signed in. If
  // we have no session model and no pending-chat pick, fall through to
  // the "Select model" placeholder below.
  //
  // Check if selected model is still available — fall back ONLY for pending chats with no user selection
  // Never override an existing session's model — the user explicitly chose it
  if (modelId && !currentSessionId && _pendingChat && window.modelsModule && window.modelsModule.getCachedItems) {
    const items = window.modelsModule.getCachedItems();
    const allAvailable = [];
    items.forEach(item => {
      if (item.offline) return;
      (item.models || []).concat(item.models_extra || []).forEach(m => allAvailable.push(m));
    });
    if (allAvailable.length > 0 && !allAvailable.includes(modelId)) {
      // Model no longer available — switch to first available
      const fallback = items.find(item => !item.offline && (item.models || []).length > 0);
      if (fallback) {
        modelId = fallback.models[0];
        _deps.setPendingChat({ url: fallback.url, modelId, endpointId: fallback.endpoint_id, source: 'fallback' });
      }
    }
  }
  const latestPending = _deps.getPendingChat && _deps.getPendingChat();
  if (
    !currentSessionId &&
    !_autoSelectingDefault &&
    window.modelsModule &&
    window.modelsModule.getCachedItems &&
    (!modelId || (latestPending && latestPending.source === 'fallback'))
  ) {
    _ensureDefaultPendingChat();
  }

  const displayName = _modelDisplayName(modelId);
  // The header indicator clips long names with ellipsis; show the full model
  // identifier on hover (#1982). No tooltip on the "Select model" placeholder.
  label.title = modelId || '';
  const logo = modelId ? providerLogo(modelId) : null;
  if (logo) {
    label.innerHTML = '<span class="model-picker-logo">' + logo + '</span> ' + displayName;
  } else {
    label.textContent = displayName;
  }
}
