// Soundboard effects are independent of voice. Text and Settings only play on a click.
import uiModule from './ui.js';

let state = { installed: false, enabled: false, favorites: [], volume: 0.6, muted: false };
let player = null;
let playerButton = null;
let initialized = false;
let generation = 0;
let searchGeneration = 0;
const titles = new Map();
const esc = value => uiModule.esc(String(value));
const el = id => document.getElementById(id);

async function api(path = '', body, method = 'PUT') {
  const response = await fetch(`/api/soundboard${path}`, {
    credentials: 'same-origin', ...(body ? { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Soundboard unavailable');
  return data;
}

export function cueHtml(id, offset) {
  if (typeof document !== 'undefined' && !titles.has(id)) {
    if (titles.size >= 300) titles.delete(titles.keys().next().value);
    titles.set(id, id.replaceAll('-', ' '));
    api(`/sounds/${encodeURIComponent(id)}`).then(result => {
      const sound = result.sounds.find(item => item.id === id);
      if (!sound) return;
      titles.set(id, sound.title);
      document.querySelectorAll('button[data-sound-cue]').forEach(button => {
        if (button.dataset.soundId !== id) return;
        button.textContent = `▶ ${sound.title}`;
        button.setAttribute('aria-label', `Play sound: ${sound.title}`);
      });
    }).catch(() => {}); // Clicking still reports a provider/plugin failure to the user.
  }
  const title = titles.get(id) || id.replaceAll('-', ' ');
  return `<button type="button" class="sound-cue" data-sound-id="${esc(id)}" data-sound-cue="${offset}:${esc(id)}" aria-label="Play sound: ${esc(title)}">▶ ${esc(title)}</button>`;
}

export function stopSound() {
  generation += 1;
  if (player) { player.pause(); player.removeAttribute('src'); player.load(); }
  if (playerButton) { playerButton.setAttribute('aria-pressed', 'false'); playerButton.classList.remove('playing'); }
  player = null;
  playerButton = null;
}

async function play(id, button) {
  if (playerButton === button && player && !player.paused) { stopSound(); return; }
  stopSound();
  const token = generation;
  try {
    const current = await api();
    if (token !== generation) return;
    state = { ...state, ...current };
    if (!state.installed || !state.enabled) throw new Error('Enable the Soundboard plugin to play this sound');
    if (state.muted) throw new Error('Sound effects are muted in Soundboard settings');
    const resolved = await api(`/sounds/${encodeURIComponent(id)}`);
    if (token !== generation || !button.isConnected) return;
    const sound = resolved.sounds[0];
    button.textContent = `▶ ${sound.title}`;
    button.setAttribute('aria-label', `Play or pause sound: ${sound.title}`);
    player = new Audio(`/api/soundboard/sounds/${encodeURIComponent(id)}/audio`);
    player.volume = state.volume;
    playerButton = button;
    player.onended = () => { if (token === generation) stopSound(); };
    player.onerror = () => {
      if (token !== generation) return;
      stopSound(); button.title = 'This sound is unavailable. Try another sound.';
      uiModule.showToast?.(button.title, 'error');
    };
    await player.play();
    if (token !== generation) return;
    button.setAttribute('aria-pressed', 'true'); button.classList.add('playing');
  } catch (error) {
    if (token !== generation) return;
    stopSound();
    button.title = error.name === 'NotAllowedError' ? 'Your browser blocked sound. Click again to enable playback.' : error.message;
    uiModule.showToast?.(button.title, 'error');
  }
}

function renderRows(host, rows) {
  if (!host) return;
  host.innerHTML = rows.length ? rows.map(sound => {
    const saved = state.favorites.some(item => item.id === sound.id);
    return `<div class="soundboard-row"><button type="button" class="sound-cue" data-sound-id="${esc(sound.id)}" ${state.enabled ? '' : 'disabled'} aria-label="Preview ${esc(sound.title)}">▶ ${esc(sound.title)}</button><button type="button" class="admin-btn-sm" data-sound-favorite="${esc(sound.id)}" aria-pressed="${saved}" ${state.enabled ? '' : 'disabled'}>${saved ? '★ Saved' : '☆ Favorite'}</button><a href="${esc(sound.url)}" target="_blank" rel="noopener noreferrer">Myinstants</a></div>`;
  }).join('') : '<p class="settings-hint">No sounds here yet.</p>';
}

async function refresh() {
  const current = await api();
  state = { ...state, ...current };
  const tab = document.querySelector('[data-settings-tab="soundboard"]');
  if (tab) tab.classList.toggle('hidden', !state.installed);
  if (!state.enabled || state.muted) stopSound();
  if (!state.installed) {
    titles.clear();
    searchGeneration += 1;
    const panel = document.querySelector('[data-settings-panel="soundboard"]');
    if (panel && !panel.classList.contains('hidden')) {
      panel.classList.add('hidden'); document.querySelector('[data-settings-tab="services"]')?.click();
    }
    return;
  }
  if (el('soundboard-status')) el('soundboard-status').textContent = state.enabled
    ? 'Preview sounds and save favorites for your agent. Text responses play only when you click.'
    : 'Soundboard is disabled. Your favorites are saved; enable the plugin to use them.';
  el('soundboard-volume').value = state.volume;
  el('soundboard-muted').checked = state.muted;
  el('soundboard-search-button').disabled = !state.enabled;
  renderRows(el('soundboard-favorites'), state.favorites);
}

async function search(query = '') {
  const token = ++searchGeneration;
  const status = el('soundboard-search-status');
  status.textContent = 'Finding sounds…';
  try {
    const result = await api(`/sounds${query ? `?query=${encodeURIComponent(query)}` : ''}`);
    if (token !== searchGeneration) return;
    renderRows(el('soundboard-results'), result.sounds);
    status.textContent = result.sounds.length ? '' : 'No matching sounds. Try a different search.';
  } catch (error) { if (token === searchGeneration) status.textContent = error.message; }
}

export async function openSoundboard() {
  try { await refresh(); if (state.enabled) await search(); }
  catch (error) { if (el('soundboard-status')) el('soundboard-status').textContent = error.message; }
}

export function initSoundboard() {
  if (initialized) return;
  initialized = true;
  document.addEventListener('click', async event => {
    const button = event.target.closest?.('button[data-sound-id], button[data-sound-favorite]');
    if (!button || button.disabled) return;
    if (button.dataset.soundId) { await play(button.dataset.soundId, button); return; }
    const id = button.dataset.soundFavorite;
    button.disabled = true;
    try {
      await api(`/favorites/${encodeURIComponent(id)}`, { enabled: !state.favorites.some(item => item.id === id) });
      await refresh();
      document.querySelectorAll('[data-sound-favorite]').forEach(node => {
        const saved = state.favorites.some(item => item.id === node.dataset.soundFavorite);
        node.textContent = saved ? '★ Saved' : '☆ Favorite'; node.setAttribute('aria-pressed', String(saved));
      });
    } catch (error) { uiModule.showToast?.(error.message, 'error'); }
    finally { button.disabled = !state.enabled; }
  });
  el('soundboard-search')?.addEventListener('submit', event => { event.preventDefault(); search(el('soundboard-query').value.trim()); });
  const savePreferences = async () => {
    const muted = el('soundboard-muted').checked;
    const volume = Number(el('soundboard-volume').value);
    if (muted) stopSound();
    if (player) player.volume = volume;
    try { await api('/preferences', { muted, volume }); state = { ...state, muted, volume }; }
    catch (error) { el('soundboard-status').textContent = error.message; }
  };
  el('soundboard-muted')?.addEventListener('change', savePreferences);
  el('soundboard-volume')?.addEventListener('change', savePreferences);
  window.addEventListener('pagehide', stopSound);
  window.addEventListener('pandamonium:extensions-changed', () => { stopSound(); refreshSoundboard(); });
}

export async function refreshSoundboard() {
  initSoundboard();
  try { await refresh(); } catch { stopSound(); }
}
