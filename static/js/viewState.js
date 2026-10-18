// static/js/viewState.js — restore the active view across reloads (MAD-986)
//
// Full-screen surfaces (Entertainment, Settings) are toggled in JS with no
// persistence, and docked tool windows only remember their dock side. This
// module records the focused surface and the set of open tool windows so a
// reload returns the operator to where they were instead of chat.
//
// Scope is deliberately narrow: it never writes the URL hash, so the existing
// session (`#<id>`) and entity (`#document-…`) routing is untouched.

const VIEW_KEY = 'odysseus-active-view';
const TOOLS_KEY = 'odysseus-open-tools';
const MAX_RESTORE_TOOLS = 4;

// Only persist windows that modalManager can reopen from a stable launcher
// after a full page load. Dynamic windows (for example email-reader-{uid}-{n})
// have no serialized restore contract and must never evict these entries.
export const RESTORABLE_TOOL_IDS = new Set([
  'mad-mcp-modal',
  'cookbook-modal',
  'calendar-modal',
  'gallery-modal',
  'tasks-modal',
  'doclib-modal',
  'memory-modal',
  'notes-panel',
  'email-lib-modal',
  'research-overlay',
  'theme-modal',
  'bug-report-modal',
  'compare-model-overlay',
  'marketplace-modal',
]);

function _readJson(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch (_) {
    return fallback;
  }
}

function _writeJson(key, value) {
  try {
    if (value == null) localStorage.removeItem(key);
    else localStorage.setItem(key, JSON.stringify(value));
  } catch (_) {}
}

// ── focused full-screen surface ──────────────────────────────────────────

export function setView(kind, id = '') {
  if (!kind || kind === 'chat') _writeJson(VIEW_KEY, null);
  else _writeJson(VIEW_KEY, { kind: String(kind), id: String(id || '') });
}

export function clearView(kind) {
  const current = _readJson(VIEW_KEY, null);
  if (!current || current.kind === kind) _writeJson(VIEW_KEY, null);
}

export function getView() {
  const current = _readJson(VIEW_KEY, null);
  if (current && (current.kind === 'entertainment' || current.kind === 'settings')) return current;
  return { kind: 'chat', id: '' };
}

// ── open tool windows ────────────────────────────────────────────────────

export function rememberOpenTool(id) {
  const normalized = String(id || '');
  if (!RESTORABLE_TOOL_IDS.has(normalized)) return;
  const open = _readJson(TOOLS_KEY, []);
  const set = new Set(
    (Array.isArray(open) ? open : []).filter((item) => RESTORABLE_TOOL_IDS.has(String(item)))
  );
  set.add(normalized);
  _writeJson(TOOLS_KEY, [...set].slice(-MAX_RESTORE_TOOLS));
}

export function forgetOpenTool(id) {
  if (!id) return;
  const open = _readJson(TOOLS_KEY, []);
  const next = (Array.isArray(open) ? open : []).filter((item) => item !== String(id));
  _writeJson(TOOLS_KEY, next.length ? next : null);
}

export function getOpenTools() {
  const open = _readJson(TOOLS_KEY, []);
  return Array.isArray(open)
    ? open.filter((id) => RESTORABLE_TOOL_IDS.has(String(id))).slice(0, MAX_RESTORE_TOOLS)
    : [];
}

// ── boot restore ─────────────────────────────────────────────────────────

export async function restoreView() {
  const view = getView();
  if (view.kind === 'entertainment') {
    try { await (await import('./entertainment.js')).openEntertainment(); } catch (_) {}
  } else if (view.kind === 'settings') {
    try { await (await import('./settings.js')).open(); } catch (_) {}
  }
  // Tool windows: best-effort. Tools that register only when opened are
  // reopened through their normal launcher; unknown ids are skipped.
  const tools = getOpenTools();
  if (!tools.length) return;
  try {
    const modalManager = (await import('./modalManager.js')).default;
    for (const id of tools) {
      try {
        modalManager.restorePersisted(id);
      } catch (_) {}
    }
  } catch (_) {}
}

export default { setView, clearView, getView, rememberOpenTool, forgetOpenTool, getOpenTools, restoreView };
