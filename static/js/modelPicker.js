// Model Picker — chatbox model selector dropdown
// Extracted from sessions.js

import { providerLogo } from './providers.js';
import uiModule from './ui.js';
import settingsModule from './settings.js';
import { MANAGED_BY_ADMIN_COPY, createModelSetupEntry } from './setupUi.js';

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

function _canonicalIdentityIcon(kind) {
  const icon = document.createElement('span');
  icon.className = 'model-picker-logo codex-browser-icon';
  icon.dataset.canonicalIdentityIcon = ['agent', 'worker'].includes(kind) ? kind : 'model';
  icon.setAttribute('aria-hidden', 'true');
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '1.8');
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');
  const paths = kind === 'worker'
    ? ['M8 7h8v6H8z', 'M5 17h14', 'M8 13v4', 'M16 13v4', 'M12 3v4']
    : ['M12 4a4 4 0 1 1 0 8 4 4 0 0 1 0-8Z', 'M5 21a7 7 0 0 1 14 0'];
  paths.forEach(value => {
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', value);
    svg.appendChild(path);
  });
  icon.appendChild(svg);
  return icon;
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
// MAD-930: saved identities from the MAD-929 registry. The composer's first
// chip lists them alongside the conversation targets, and the second chip shows
// the bound identity's attached default model.
let _identityItems = [];
let _identityActiveId = '';
let _identityMenuMode = 'identity';

function _workspaceAliases(values) {
  return (Array.isArray(values) ? values : [])
    .map(value => String(value || '').replace(/^workspace:/, ''))
    .filter(value => /^[a-z0-9][a-z0-9_-]{0,63}$/.test(value))
    .slice(0, 32);
}

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
        external: selection.external === true,
        governedTaskActions: selection.governedTaskActions === true,
        canStartTask: selection.canStartTask === true,
        canSteerTask: selection.canSteerTask === true,
        workspaces: _workspaceAliases(selection.workspaces),
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
  const defaultTarget = new URLSearchParams(window.location.search).has('codex_task') ? 'pc-codex' : 'jarvis';
  const defaultIdentity = _selectorItems.find(item => item.target === defaultTarget && !item.disabled)
    || _selectorItems.find(item => !item.disabled);
  return defaultIdentity ? {
    target: defaultIdentity.target,
    label: defaultIdentity.display,
    kind: defaultIdentity.kind,
    available: true,
    reason: '',
    external: defaultIdentity.external === true,
    governedTaskActions: defaultIdentity.governedTaskActions === true,
    canStartTask: defaultIdentity.canStartTask === true,
    canSteerTask: defaultIdentity.canSteerTask === true,
    workspaces: _workspaceAliases(defaultIdentity.workspaces),
  } : null;
}

export function getSelectedAgentTarget() {
  return _selectedAgent()?.target || '';
}

export function getSelectedAgentSelection() {
  const selected = _selectedAgent();
  const current = _selectorItems.find(item => item.target === selected?.target);
  return selected ? { ...selected, runtime: current?.runtime || '', location: current?.location || '' } : null;
}

export function clearPendingAgentTarget() {
  _selectedAgents.delete(_PENDING_AGENT_KEY);
}

export function preserveSelectedAgentForNewChat() {
  const selected = _selectedAgent();
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
      external: known?.external === true,
      governedTaskActions: known?.governedTaskActions === true,
      canStartTask: known?.canStartTask === true,
      canSteerTask: known?.canSteerTask === true,
      workspaces: _workspaceAliases(known?.workspaces),
    });
  }
  _saveAgentSelections();
}

function _emitConversationTarget(selectedAgent) {
  if (!selectedAgent) return;
  const detail = {
    target: selectedAgent.target,
    runtime: _selectorItems.find(item => item.target === selectedAgent.target)?.runtime || '',
    location: _selectorItems.find(item => item.target === selectedAgent.target)?.location || '',
    label: selectedAgent.label || selectedAgent.target,
    kind: selectedAgent.kind,
    available: selectedAgent.available !== false,
    reason: selectedAgent.reason || '',
    external: selectedAgent.external === true,
    governedTaskActions: selectedAgent.governedTaskActions === true,
    canStartTask: selectedAgent.canStartTask === true,
    canSteerTask: selectedAgent.canSteerTask === true,
    workspaces: _workspaceAliases(selectedAgent.workspaces),
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
      const capabilities = Array.isArray(selection.capabilities) ? selection.capabilities : [];
      const runtime = String(selection.runtime || (capabilities.includes('codex') ? 'Codex' : capabilities.includes('claude') ? 'Claude' : capabilities.includes('hermes') ? 'Hermes' : capabilities.includes('external_agent') ? 'External agent' : 'Model-backed agent'));
      const item = {
        kind: entity.kind,
        target: String(selection.target || ''),
        mid: entity.kind === 'model' ? String(selection.model_id || '') : `${entity.kind}:${selection.target}`,
        modelId: String(selection.model_id || ''),
        endpointId: String(selection.endpoint_id || ''),
        display: String(entity.display_name || 'Configured choice'),
        runtime,
        location: String(selection.location || ''),
        epName: [runtime, selection.location].filter(Boolean).join(' · '),
        providerText: `${entity.kind} ${entity.health?.state || ''} ${selection.reason || ''}`,
        stale: selection.selectable !== true,
        disabled: selection.selectable !== true,
        staleReason: String(selection.reason || entity.health?.reason || 'unavailable').replace(/_/g, ' '),
        offline: entity.health?.state === 'unavailable',
        external: capabilities.includes('external_agent'),
        governedTaskActions: capabilities.includes('governed_task_actions'),
        canStartTask: capabilities.includes('task.start'),
        canSteerTask: capabilities.includes('task.steer'),
        workspaces: _workspaceAliases(entity.permissions?.configured_scopes),
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
        external: current.external === true,
        governedTaskActions: current.governedTaskActions === true,
        canStartTask: current.canStartTask === true,
        canSteerTask: current.canSteerTask === true,
        workspaces: _workspaceAliases(current.workspaces),
      } : {
        ...selection,
        available: false,
        reason: 'no longer configured',
        governedTaskActions: false,
        canStartTask: false,
        canSteerTask: false,
        workspaces: [],
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

/* ── Saved identities (MAD-930, backed by the MAD-929 registry) ── */

async function _refreshIdentityCatalog() {
  try {
    const response = await fetch(`${API_BASE}/api/auth/identities`, { credentials: 'same-origin' });
    if (!response.ok) throw new Error(`identities_${response.status}`);
    const payload = await response.json();
    _identityItems = (Array.isArray(payload?.identities) ? payload.identities : [])
      .filter(item => item && typeof item.id === 'string' && item.id.trim())
      .map(item => ({
        id: String(item.id).trim(),
        display: String(item.display_name || item.id).slice(0, 80),
        modelProfile: (item.model_profile && typeof item.model_profile === 'object') ? item.model_profile : {},
      }));
    _identityActiveId = String(payload?.active_id || '');
    if (_identityActiveId && !_identityItems.some(item => item.id === _identityActiveId)) {
      _identityActiveId = _identityItems[0]?.id || '';
    }
  } catch (_) {
    // Additive discovery: without the registry the conversation-target list
    // keeps its pre-MAD-929 behavior.
    _identityItems = [];
    _identityActiveId = '';
  }
}

function _identityById(identityId) {
  const id = String(identityId || '').trim();
  if (!id) return null;
  return _identityItems.find(item => item.id === id) || null;
}

function _defaultIdentity() {
  return _identityById(_identityActiveId) || _identityItems[0] || null;
}

function _boundIdentityId() {
  let session = null;
  let pending = null;
  try {
    session = (_deps?.getSessions?.() || []).find(s => s.id === _deps?.getCurrentSessionId?.());
    pending = _deps?.getPendingChat?.();
  } catch (_) { /* deps not ready yet */ }
  return String((session && session.identity_id) || (pending && pending.identityId) || '').trim();
}

function _boundIdentity() {
  return _identityById(_boundIdentityId()) || null;
}

function _identityChatModel(identity) {
  return String(identity?.modelProfile?.chat?.model || '').trim();
}

function _identityChoices() {
  return _identityItems.map(item => ({
    kind: 'identity',
    identityId: item.id,
    target: '',
    display: item.display,
    epName: _identityChatModel(item)
      ? `Identity · ${_identityChatModel(item).split('/').pop()}`
      : 'Saved identity',
    providerText: `identity ${item.id}`,
    stale: false,
    disabled: false,
    offline: false,
  }));
}

function _modelChoices() {
  const items = (window.modelsModule && window.modelsModule.getCachedItems)
    ? (window.modelsModule.getCachedItems() || [])
    : [];
  const choices = [];
  for (const item of items) {
    if (!item || item.offline) continue;
    if (!_isChatEndpoint(item)) continue;
    const models = (item.models || []).concat(item.models_extra || []);
    const displays = (item.models_display || []).concat(item.models_extra_display || []);
    models.forEach((rawId, index) => {
      const mid = String(rawId || '').trim();
      if (!mid) return;
      choices.push({
        kind: 'model',
        mid,
        display: String(displays[index] || mid).split('/').pop(),
        url: item.url || '',
        endpointId: item.endpoint_id || '',
        epName: [item.endpoint_name || item.host || '', item.category || ''].filter(Boolean).join(' · '),
        providerText: [item.endpoint_name || '', item.url || ''].filter(Boolean).join(' '),
        stale: false,
        disabled: false,
        offline: false,
      });
    });
  }
  return choices;
}

function _findModelChoice(modelId, endpointId) {
  const wanted = String(modelId || '').trim();
  if (!wanted) return null;
  const ep = String(endpointId || '').trim();
  const choices = _modelChoices();
  return choices.find(item => item.mid === wanted && (!ep || item.endpointId === ep))
    || choices.find(item => item.mid === wanted)
    || null;
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
    if ((item.model_type || 'llm') !== 'llm') continue;
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
    updateModelPicker();
    try { document.dispatchEvent(new CustomEvent('odysseus:model-picked')); } catch (_) {}
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
  Promise.all([_refreshSelectorCatalog(), _refreshIdentityCatalog()])
    .then(() => updateModelPicker())
    .catch(() => {});
}

function _initModelPickerDropdown() {
  const wrap = document.getElementById('model-picker-wrap');
  const btn = document.getElementById('model-picker-btn');
  const modelBtn = document.getElementById('identity-model-btn');
  const menu = document.getElementById('model-picker-menu');
  const heading = document.getElementById('model-picker-heading');
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
    btn.setAttribute('aria-expanded', 'false');
    if (modelBtn) modelBtn.setAttribute('aria-expanded', 'false');
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
    const top = wrap.getBoundingClientRect().top;
    menu.style.maxHeight = `${Math.max(120, top - 24)}px`;
    menu.style.overflowY = 'auto';
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
    // Saved identities lead the list. The catalog's own "jarvis" row is
    // redundant once the registry exposes identities, so it is dropped then.
    const choices = _identityItems.length
      ? [..._identityChoices(), ..._selectorItems.filter(item => item.target !== 'jarvis')]
      : [..._selectorItems];
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
        external: selected.external === true,
        governedTaskActions: selected.governedTaskActions === true,
        canStartTask: selected.canStartTask === true,
        canSteerTask: selected.canSteerTask === true,
        workspaces: _workspaceAliases(selected.workspaces),
      });
    }
    const seen = new Set();
    return choices.filter(item => {
      const keys = item.identityId
        ? [`identity:${item.identityId}`]
        : [`target:${item.target}`, `name:${item.display.trim().toLowerCase()}`];
      if (keys.some(key => seen.has(key))) return false;
      keys.forEach(key => seen.add(key));
      return true;
    });
  }

  function _populate(filter) {
    listEl.innerHTML = '';
    const modelMode = _identityMenuMode === 'model';
    const all = modelMode ? _modelChoices() : _getConversationTargets();
    const q = (filter || '').trim().toLowerCase();
    const hasAnyChoice = all.length > 0;
    listEl.classList.toggle('is-empty', !hasAnyChoice);
    menu.classList.toggle('no-models', !hasAnyChoice);
    if (heading) heading.textContent = modelMode ? 'Choose a model for this session' : 'Select who to talk to';
    listEl.setAttribute('aria-label', modelMode ? 'Models available for this session' : 'Configured conversation identities');
    if (search) {
      search.placeholder = modelMode
        ? (hasAnyChoice ? 'Search models…' : 'No models discovered')
        : (hasAnyChoice ? 'Search who you can talk to…' : 'No identities discovered');
    }
    if (searchRow) {
      searchRow.classList.toggle('searching', !!q);
    }

    if (!modelMode && _selectorCatalogState === 'loading') {
      listEl.classList.remove('is-empty');
      menu.classList.remove('no-models');
      const loading = document.createElement('div');
      loading.className = 'model-switch-status';
      loading.setAttribute('role', 'status');
      loading.textContent = 'Discovering who you can talk to…';
      listEl.appendChild(loading);
      return;
    }
    if (!modelMode && _selectorCatalogState === 'error') {
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
      if (modelMode) {
        empty.textContent = window._isAdmin === false
          ? MANAGED_BY_ADMIN_COPY
          : 'No models are available for this session.';
        if (window._isAdmin !== false) {
          empty.appendChild(document.createElement('br'));
          empty.appendChild(createModelSetupEntry());
        }
      } else if (window._isAdmin === false) {
        empty.textContent = MANAGED_BY_ADMIN_COPY;
      } else {
        empty.textContent = 'No configured identities are available.';
        empty.appendChild(document.createElement('br'));
        empty.appendChild(createModelSetupEntry());
      }
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
      row.setAttribute(
        'aria-label',
        [m.display, m.epName, m.disabled === true ? m.staleReason : 'available'].filter(Boolean).join(', '),
      );
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
      row.append(_canonicalIdentityIcon(m.kind), nameSpan);
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

      // Mark the current selection (MAD-888): identity rows by target, model
      // rows by the session's active model. The check is the compact-selector
      // selected affordance; behavior and routing are unchanged.
      const _selectedNow = _selectedAgent();
      const _sessionModel = (_deps.getSessions().find(s => s.id === _deps.getCurrentSessionId()) || {}).model || '';
      const _isSelected = (m.target && _selectedNow?.target === m.target)
        || (m.kind === 'identity' && m.identityId === _boundIdentityId())
        || (!m.target && !m.identityId && !!m.mid && m.mid === _sessionModel);
      row.setAttribute('aria-selected', _isSelected ? 'true' : 'false');
      if (_isSelected) {
        row.classList.add('is-selected');
        const check = document.createElement('span');
        check.className = 'model-switch-check';
        check.setAttribute('aria-hidden', 'true');
        check.textContent = '✓';
        row.appendChild(check);
      }

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
    if (m.kind === 'identity') {
      // Bind a saved identity (MAD-929). For an existing session the server
      // also applies the identity's attached model profile and reasoning level;
      // for a new chat we preload them into the pending chat so the session
      // materializes already bound.
      const identityId = String(m.identityId || '');
      if (currentSessionId) {
        const fd = new FormData();
        fd.append('identity_id', identityId);
        try {
          const response = await fetch(`${API_BASE}/api/session/${currentSessionId}`, {
            method: 'PATCH',
            body: fd,
          });
          if (!response.ok) throw new Error(`identity_${response.status}`);
          const payload = await response.json().catch(() => ({}));
          const session = _deps.getSessions().find(item => item.id === currentSessionId);
          if (session) {
            session.identity_id = identityId;
            if (payload.model) session.model = payload.model;
            if (payload.endpoint_url) session.endpoint_url = payload.endpoint_url;
            if ('reasoning_level' in payload) session.reasoning_level = payload.reasoning_level || '';
          }
        } catch (_) {
          uiModule.showError(`Failed to select ${m.display}`);
          return;
        }
        try { document.dispatchEvent(new CustomEvent('odysseus:model-picked', { detail: m })); } catch {}
        updateModelPicker();
        uiModule.showToast(`Talking to ${m.display} — its default model is loaded for this session`);
        return;
      }
      const pending = _pendingChat || {};
      const identity = _identityById(identityId);
      const chat = (identity?.modelProfile?.chat) || {};
      const next = { ...pending, identityId, reasoningLevel: String(chat.reasoning_level || '') };
      if (chat.model) {
        next.modelId = String(chat.model);
        next.source = 'identity';
        const resolved = _findModelChoice(chat.model, chat.endpoint_id || '');
        if (resolved) {
          next.url = resolved.url;
          next.endpointId = resolved.endpointId || chat.endpoint_id || '';
        }
      }
      _deps.setPendingChat(next);
      try { document.dispatchEvent(new CustomEvent('odysseus:model-picked', { detail: m })); } catch {}
      updateModelPicker();
      uiModule.showToast(`Talking to ${m.display}`);
      return;
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
        external: m.external === true,
        governedTaskActions: m.governedTaskActions === true,
        canStartTask: m.canStartTask === true,
        canSteerTask: m.canSteerTask === true,
        workspaces: _workspaceAliases(m.workspaces),
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
      // Already have a deferred session — just update the model. Any pending
      // identity (and its reasoning level) survives a composer model override.
      _deps.setPendingChat({
        ..._pendingChat,
        url: m.url,
        modelId: m.mid,
        endpointId: m.endpointId,
        source: 'manual',
      });
      clearSelectedAgent();
      // Header stays as session name — model switch only updates picker
      updateModelPicker();
      try { document.dispatchEvent(new CustomEvent('odysseus:model-picked', { detail: m })); } catch {}
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
    // The reasoning chip follows the session's active model, so re-render it
    // once the async session update has landed (MAD-930).
    try { document.dispatchEvent(new CustomEvent('odysseus:model-picked', { detail: m })); } catch {}
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
      if ((item.model_type || 'llm') !== 'llm') continue;
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

  function _openMenu(mode) {
    _identityMenuMode = mode === 'model' ? 'model' : 'identity';
    // Force-clear any in-progress close animation
    _cancelPendingClose();
    menu.classList.remove('closing', 'hidden');
    _populate('');
    _fitMenuToViewport();
    btn.setAttribute('aria-expanded', String(_identityMenuMode === 'identity'));
    if (modelBtn) modelBtn.setAttribute('aria-expanded', String(_identityMenuMode === 'model'));
    if (window.modelsModule && window.modelsModule.refreshModels) {
      window.modelsModule.refreshModels().then(() => {
        if (!menu.classList.contains('hidden')) {
          _populate(search.value || '');
          _fitMenuToViewport();
        }
      }).catch(() => {});
    }
    _refreshSelectorCatalog().then(() => {
      if (!menu.classList.contains('hidden') && _identityMenuMode === 'identity') {
        _populate(search.value || '');
        _fitMenuToViewport();
      }
      updateModelPicker();
    }).catch(() => {});
    _refreshIdentityCatalog().then(() => {
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
  }

  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    const open = !menu.classList.contains('hidden') && !menu.classList.contains('closing');
    if (open && _identityMenuMode === 'identity') {
      _close();
      return;
    }
    _openMenu('identity');
  });

  if (modelBtn) {
    modelBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      const open = !menu.classList.contains('hidden') && !menu.classList.contains('closing');
      if (open && _identityMenuMode === 'model') {
        _close();
        return;
      }
      _openMenu('model');
    });
  }

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
        await _refreshIdentityCatalog();
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
    if (
      !menu.classList.contains('hidden')
      && !menu.contains(e.target)
      && e.target !== btn
      && !(modelBtn && (e.target === modelBtn || modelBtn.contains(e.target)))
    ) {
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
  const target = (selectedAgent && selectedAgent.target) || (s && s.agent_target) || 'jarvis';
  if (!currentSessionId && !_deps.getPendingChat()) _ensureDefaultPendingChat();
  if (selectedAgent) _emitConversationTarget(selectedAgent);
  // A saved identity owns the jarvis-routed chip label (MAD-930). Worker and
  // other conversation targets keep their existing labels and routing.
  const boundIdentity = target === 'jarvis' ? _boundIdentity() : null;
  if (boundIdentity) {
    label.title = `Talking to ${boundIdentity.display}`;
    label.textContent = boundIdentity.display;
    _renderIdentityModelChip();
    return;
  }
  if (selectedAgent) {
    label.title = selectedAgent.label;
    label.textContent = selectedAgent.label || selectedAgent.target;
    if (selectedAgent.available === false) {
      label.title = `${selectedAgent.label}: ${selectedAgent.reason || 'unavailable'}`;
    }
    _renderIdentityModelChip();
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
      if ((item.model_type || 'llm') !== 'llm') return;
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
  _renderIdentityModelChip();
}

/**
 * Render the identity-default model chip (MAD-930).
 *
 * Shows the model that will load for this session: the session's own model
 * (which the server seeds from the bound identity's attached profile), the
 * pending pick for a new chat, or the identity's saved default. A model picked
 * in the composer only PATCHes the session, so it is flagged as an override
 * while the identity's saved default stays untouched.
 */
function _renderIdentityModelChip() {
  const chip = document.getElementById('identity-model-btn');
  if (!chip || !_deps) return;
  const labelEl = document.getElementById('identity-model-label');
  const logoEl = document.getElementById('identity-model-logo');
  const selectedAgent = _selectedAgent();
  let session = null;
  let pending = null;
  try {
    session = (_deps.getSessions() || []).find(x => x.id === _deps.getCurrentSessionId()) || null;
    pending = _deps.getPendingChat ? _deps.getPendingChat() : null;
  } catch (_) { /* deps not ready */ }
  const target = (selectedAgent && selectedAgent.target) || (session && session.agent_target) || 'jarvis';
  const applicable = target === 'jarvis';
  chip.hidden = !applicable;
  if (!applicable) {
    chip.classList.remove('is-override');
    return;
  }
  const identity = _boundIdentity() || _defaultIdentity();
  const identityModel = _identityChatModel(identity);
  let modelId = '';
  if (session && session.model) modelId = String(session.model);
  else if (pending && pending.modelId) modelId = String(pending.modelId);
  if (!modelId && identityModel) modelId = identityModel;
  const isIdentityDefault = !!(modelId && identityModel && modelId === identityModel);
  const isOverride = !!(modelId && identityModel && modelId !== identityModel);
  const display = modelId ? _modelDisplayName(modelId) : 'Select model';
  if (labelEl) labelEl.textContent = display;
  if (logoEl) logoEl.innerHTML = modelId ? (providerLogo(modelId) || '') : '';
  chip.classList.toggle('is-override', isOverride);
  const owner = identity ? identity.display : '';
  if (isOverride) {
    chip.title = `${modelId} — session override; ${owner ? `${owner}'s` : 'the identity'} saved default (${identityModel}) is unchanged`;
  } else if (isIdentityDefault) {
    chip.title = `${modelId} — default from ${owner || 'the identity'}`;
  } else if (modelId) {
    chip.title = modelId;
  } else {
    chip.title = 'Choose the model for this session';
  }
  chip.setAttribute('aria-label', `Model: ${display}. Click to change.`);
}
