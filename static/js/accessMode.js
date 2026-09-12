// static/js/accessMode.js
//
// Operator access-level control (MAD-885). One shield button to the left of
// the Jarvis sphere in the chat input bar switches the authority gate between
// three per-user modes:
//   ask_for_approval — always ask to edit external files and use the internet
//   approve_for_me   — only ask for actions detected as potentially unsafe
//   full_access      — unrestricted access to the internet and any file
// The approval-card workflow itself is unchanged; this only changes the
// DEFAULT decision for gated actions. Persisted server-side per user via
// /api/prefs/access_mode.

import uiModule from './ui.js';

const API_BASE = window.location.origin;
const ACCESS_PREF_KEY = 'access_mode';

const ACCESS_MODE_LABELS = {
  ask_for_approval: 'Ask for approval',
  approve_for_me: 'Approve for me',
  full_access: 'Full access',
};
const DEFAULT_MODE = 'ask_for_approval';

function el(id) {
  return document.getElementById(id);
}

function applyAccessMode(mode) {
  const normalized = ACCESS_MODE_LABELS[mode] ? mode : DEFAULT_MODE;
  const label = ACCESS_MODE_LABELS[normalized];
  const btn = el('access-mode-btn');
  if (btn) {
    btn.dataset.accessMode = normalized;
    btn.title = `Agent access: ${label}`;
    btn.setAttribute('aria-label', `Agent access: ${label}`);
    btn.classList.toggle('access-mode-ask', normalized === 'ask_for_approval');
    btn.classList.toggle('access-mode-auto', normalized === 'approve_for_me');
    btn.classList.toggle('access-mode-full', normalized === 'full_access');
  }
  const menu = el('access-mode-menu');
  if (menu) {
    menu.querySelectorAll('.access-mode-option').forEach(option => {
      const active = option.dataset.accessValue === normalized;
      option.classList.toggle('active', active);
      option.setAttribute('aria-pressed', String(active));
    });
  }
  return normalized;
}

function closeAccessModeMenu() {
  const menu = el('access-mode-menu');
  const btn = el('access-mode-btn');
  if (menu) menu.hidden = true;
  if (btn) btn.setAttribute('aria-expanded', 'false');
}

async function loadAccessMode() {
  try {
    const res = await fetch(`${API_BASE}/api/prefs/${ACCESS_PREF_KEY}`, { credentials: 'same-origin' });
    if (!res.ok) return;
    const data = await res.json();
    applyAccessMode(data && data.value ? data.value : DEFAULT_MODE);
  } catch (err) {
    // Unauthenticated/offline — keep the default state.
  }
}

async function setAccessMode(mode) {
  const btn = el('access-mode-btn');
  const previous = (btn && btn.dataset.accessMode) || DEFAULT_MODE;
  applyAccessMode(mode);
  try {
    const res = await fetch(`${API_BASE}/api/prefs/${ACCESS_PREF_KEY}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ value: mode }),
    });
    if (!res.ok) throw new Error(`pref update failed: ${res.status}`);
    const label = ACCESS_MODE_LABELS[mode] || mode;
    if (uiModule && uiModule.showToast) uiModule.showToast(`Agent access: ${label}`);
  } catch (err) {
    // Revert the visible state — never claim a mode the server rejected.
    applyAccessMode(previous);
    if (uiModule && uiModule.showError) {
      uiModule.showError('Could not change agent access level. Try again.');
    }
  }
}

export function initAccessMode() {
  const btn = el('access-mode-btn');
  if (!btn) return;
  const menu = el('access-mode-menu');
  btn.addEventListener('click', () => {
    const opening = menu.hidden;
    closeAccessModeMenu();
    if (opening) {
      menu.hidden = false;
      // Position the fixed menu from the button's live rect; a containing
      // block inside the flex input bar must not influence hit-testing.
      const rect = btn.getBoundingClientRect();
      const width = 264;
      const left = Math.max(8, Math.min(window.innerWidth - width - 8, rect.right - width));
      const top = Math.max(8, rect.top - menu.offsetHeight - 8);
      menu.style.left = `${left}px`;
      menu.style.top = `${top}px`;
      btn.setAttribute('aria-expanded', 'true');
    }
  });
  if (menu) {
    menu.querySelectorAll('.access-mode-option').forEach(option => {
      option.addEventListener('click', () => {
        // Close only after the save settles — closing synchronously while the
        // clicked element is mid-interaction makes the browser treat the click
        // as unstable and retry against a hidden menu.
        setAccessMode(option.dataset.accessValue).finally(closeAccessModeMenu);
      });
    });
    document.addEventListener('click', event => {
      if (!btn.contains(event.target) && !menu.contains(event.target)) closeAccessModeMenu();
    });
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape') closeAccessModeMenu();
    });
  }
  loadAccessMode();
}

export default { initAccessMode, applyAccessMode, setAccessMode, closeAccessModeMenu, loadAccessMode };