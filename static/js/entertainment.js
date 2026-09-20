import uiModule from './ui.js';

const el = id => document.getElementById(id);
const esc = value => uiModule.esc(String(value));
let providers = [];
let active = null;
let query = '';
let selection = null;
let hls = null;
let initialized = false;
let playback = null;

const PREFS_KEY = 'entertainment';
const DEFAULT_PREFS = { provider: '', language: 'sub', quality: 'best', autoplay: false, favorites: [], resume: null };
const LANGUAGES = ['sub', 'dub'];
const QUALITIES = ['best', '1080p', '720p', '480p', '360p'];
let prefs = { ...DEFAULT_PREFS };
let prefsLoaded = false;

async function api(path = '', body) {
  const response = await fetch(`/api/entertainment${path}`, {
    credentials: 'same-origin',
    ...(body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Entertainment unavailable');
  return data;
}

function normalizePrefs(value) {
  const merged = { ...DEFAULT_PREFS, ...(value && typeof value === 'object' ? value : {}) };
  merged.autoplay = Boolean(merged.autoplay);
  merged.favorites = Array.isArray(merged.favorites) ? merged.favorites.filter(item => item && item.key && item.item).slice(0, 100) : [];
  merged.resume = merged.resume && typeof merged.resume === 'object' && merged.resume.item ? merged.resume : null;
  return merged;
}

async function loadPrefs() {
  try {
    const response = await fetch(`/api/prefs/${PREFS_KEY}`, { credentials: 'same-origin' });
    const data = await response.json();
    prefs = normalizePrefs(data && data.value);
  } catch (_) {
    prefs = { ...DEFAULT_PREFS };
  }
  prefsLoaded = true;
  return prefs;
}

async function savePrefs() {
  const statusEl = el('entertainment-settings-status');
  try {
    const response = await fetch(`/api/prefs/${PREFS_KEY}`, {
      method: 'PUT',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value: prefs }),
    });
    if (!response.ok) throw new Error('Could not save Entertainment defaults');
    if (statusEl) statusEl.textContent = 'Saved.';
  } catch (error) {
    if (statusEl) statusEl.textContent = error.message;
  }
}

function applyPrefs() {
  const mode = el('entertainment-mode');
  if (mode) mode.value = LANGUAGES.includes(prefs.language) ? prefs.language : 'sub';
  const quality = el('entertainment-quality');
  if (quality) quality.value = QUALITIES.includes(prefs.quality) ? prefs.quality : 'best';
  const autoplay = el('entertainment-autoplay');
  if (autoplay) autoplay.checked = Boolean(prefs.autoplay);
}

function renderSettingsPanel() {
  const providerSelect = el('entertainment-default-provider');
  if (providerSelect) {
    providerSelect.innerHTML = '<option value="">Ask each time</option>' + providers
      .filter(provider => provider.enabled)
      .map(provider => `<option value="${esc(provider.id)}">${esc(provider.label)}</option>`)
      .join('');
    providerSelect.value = providers.some(provider => provider.id === prefs.provider && provider.enabled) ? prefs.provider : '';
  }
  const language = el('entertainment-default-language');
  if (language) language.value = LANGUAGES.includes(prefs.language) ? prefs.language : 'sub';
  const quality = el('entertainment-default-quality');
  if (quality) quality.value = QUALITIES.includes(prefs.quality) ? prefs.quality : 'best';
  const autoplay = el('entertainment-default-autoplay');
  if (autoplay) autoplay.checked = Boolean(prefs.autoplay);
}

function readSettingsPanel() {
  prefs = {
    ...prefs,
    provider: el('entertainment-default-provider')?.value || '',
    language: el('entertainment-default-language')?.value || 'sub',
    quality: el('entertainment-default-quality')?.value || 'best',
    autoplay: Boolean(el('entertainment-default-autoplay')?.checked),
  };
  savePrefs();
}

function favKey(provider, item) {
  return `${provider}:${item?.id ?? item?.selection ?? item?.title ?? ''}`;
}

function isFavorite(provider, item) {
  const key = favKey(provider, item);
  return prefs.favorites.some(entry => entry.key === key);
}

function toggleFavorite(provider, item) {
  if (!item) return;
  const key = favKey(provider, item);
  const index = prefs.favorites.findIndex(entry => entry.key === key);
  if (index >= 0) prefs.favorites.splice(index, 1);
  else prefs.favorites.unshift({ key, provider, query, item, title: item.title || 'Title' });
  prefs.favorites = prefs.favorites.slice(0, 100);
  savePrefs();
  renderSaved();
}

function rememberResume(record) {
  prefs.resume = { ...record, at: Date.now() };
  savePrefs();
}

function renderSaved() {
  const host = el('entertainment-saved');
  if (!host) return;
  const resumeEl = el('entertainment-resume');
  const favoritesEl = el('entertainment-favorites');
  const resume = prefs.resume && prefs.resume.provider === active ? prefs.resume : null;
  if (resumeEl) {
    resumeEl.classList.toggle('hidden', !resume);
    resumeEl.innerHTML = resume
      ? `<small>CONTINUE WATCHING</small><span>${esc(resume.title || resume.item?.title || 'Resume')}</span>`
      : '';
  }
  const favorites = prefs.favorites.filter(entry => entry.provider === active);
  if (favoritesEl) {
    favoritesEl.innerHTML = favorites.map(entry =>
      `<button type="button" class="entertainment-favorite" data-ent-open-fav="${esc(entry.key)}">★ ${esc(entry.title)}</button>`
    ).join('');
  }
  host.classList.toggle('hidden', !resume && favorites.length === 0);
}

function stopPlayer() {
  if (hls) { hls.destroy(); hls = null; }
  const video = el('entertainment-video');
  if (video) { video.pause(); video.removeAttribute('src'); video.replaceChildren(); video.load(); }
}

function status(text = '') { el('entertainment-status').textContent = text; }

function buttons(items, action, label) {
  const favoritable = action === 'title';
  el('entertainment-results').innerHTML = items.length ? items.map((item, index) => {
    const row = `<button type="button" class="entertainment-result" data-ent-action="${action}" data-ent-index="${index}"><span>${esc(label(item))}</span><b>›</b></button>`;
    if (!favoritable) return row;
    const saved = isFavorite(active, item);
    return `<div class="entertainment-result-row">${row}<button type="button" class="entertainment-fav" data-ent-fav="${index}" aria-pressed="${saved}" aria-label="${saved ? 'Remove favorite' : 'Save favorite'}: ${esc(item.title)}">${saved ? '★' : '☆'}</button></div>`;
  }).join('') : '<p class="entertainment-empty">Nothing found. Try a different search.</p>';
  el('entertainment-results')._items = items;
}

function episodeButtons(result, action, path, body) {
  buttons(result.items, action, item => item.label);
  if (result.next_offset >= 0) {
    el('entertainment-results').insertAdjacentHTML('beforeend', `<button type="button" class="entertainment-result" data-ent-more="${result.next_offset}"><span>Load more episodes</span><b>+</b></button>`);
    el('entertainment-results')._page = { action, path, body };
  }
}

function showProvider(id) {
  active = id;
  selection = null;
  playback = null;
  stopPlayer();
  el('entertainment-landing').classList.add('hidden');
  el('entertainment-player').classList.add('hidden');
  el('entertainment-browser').classList.remove('hidden');
  const anime = id === 'ani-cli';
  el('entertainment-heading').textContent = anime ? 'Anime' : 'Movies & Shows';
  el('entertainment-copy').textContent = anime ? 'Search AniCLI, choose sub or dub, then pick an episode and quality.' : 'Search PandaFlix for a movie or series, then choose a season and episode.';
  el('entertainment-mode').classList.toggle('hidden', !anime);
  el('entertainment-quality').classList.toggle('hidden', !anime);
  el('entertainment-history').classList.toggle('hidden', !anime);
  el('entertainment-jump').classList.add('hidden');
  el('entertainment-query').placeholder = anime ? 'Search anime' : 'Search movies and shows';
  el('entertainment-results').replaceChildren();
  status('');
  renderSaved();
  el('entertainment-tabs').querySelectorAll('button').forEach(button => button.classList.toggle('active', button.dataset.provider === id));
  el('entertainment-query').focus();
}

function showLanding() {
  if (providers.length === 1) { showProvider(providers[0].id); return; }
  stopPlayer(); active = null; playback = null;
  el('entertainment-browser').classList.add('hidden');
  el('entertainment-player').classList.add('hidden');
  el('entertainment-landing').classList.remove('hidden');
  el('entertainment-landing').innerHTML = '<div class="entertainment-landing-copy"><p>PRIVATE ENTERTAINMENT</p><h1>What are we watching?</h1></div>' + providers.map(provider =>
    `<button type="button" class="entertainment-choice" data-provider="${provider.id}" ${provider.enabled ? '' : 'disabled'}><span>${provider.id === 'ani-cli' ? 'ANIME' : 'MOVIES · SHOWS'}</span><strong>${esc(provider.label)}</strong><small>${provider.enabled ? 'Open provider' : 'Enable this plugin first'}</small></button>`
  ).join('');
}

async function play(result) {
  stopPlayer();
  el('entertainment-browser').classList.add('hidden');
  el('entertainment-player').classList.remove('hidden');
  el('entertainment-now-playing').textContent = result.title;
  const video = el('entertainment-video');
  for (const subtitle of result.subtitles || []) {
    const track = document.createElement('track');
    track.kind = 'subtitles'; track.src = subtitle.url; track.label = subtitle.label; track.srclang = subtitle.language;
    video.appendChild(track);
  }
  if (result.format === 'hls' && window.Hls?.isSupported()) {
    hls = new window.Hls({ enableWorker: true, maxBufferLength: 30 });
    hls.loadSource(result.url); hls.attachMedia(video);
  } else video.src = result.url;
  try { await video.play(); } catch (_) { status('Press play to start.'); }
}

async function search() {
  query = el('entertainment-query').value.trim();
  if (!query) return;
  status('Searching upstream…'); el('entertainment-results').replaceChildren();
  const body = active === 'ani-cli' ? { query, dub: el('entertainment-mode').value === 'dub' } : { query };
  const result = await api(`/${active}/search`, body);
  buttons(result.items, 'title', item => active === 'ani-cli' ? item.title : `${item.title} · ${item.kind === 'series' ? 'Series' : 'Movie'}`);
  status(`${result.items.length} result${result.items.length === 1 ? '' : 's'}`);
}

async function chooseTitle(item) {
  selection = item;
  status('Loading title…');
  if (active === 'ani-cli') {
    const body = { query, selection_index: item.id, dub: el('entertainment-mode').value === 'dub' };
    const result = await api('/ani-cli/episodes', { ...body, offset: 0 });
    episodeButtons(result, 'anime-episode', '/ani-cli/episodes', body);
    el('entertainment-jump').classList.remove('hidden');
    status(`Choose an episode · ${result.total} available.`);
  } else if (item.kind === 'movie') {
    await resolvePanda(0, 0);
  } else {
    const result = await api('/pandaflix/seasons', { query, selection: item.selection });
    buttons(result.items, 'season', value => value.label); status('Choose a season.');
  }
}

async function resolvePanda(season, episode) {
  status('Resolving stream…');
  const result = await api('/pandaflix/resolve', { query, selection: selection.selection, kind: selection.kind, season, episode });
  playback = { provider: 'pandaflix', query, item: selection, season, episode, items: el('entertainment-results')._items, title: result.title };
  rememberResume({ provider: 'pandaflix', query, item: selection, season, episode, title: result.title });
  await play(result);
}

async function playAnimeEpisode(number) {
  const dub = el('entertainment-mode').value === 'dub';
  const quality = el('entertainment-quality').value;
  status('Resolving stream…');
  const result = await api('/ani-cli/resolve', { query, selection_index: selection.id, dub, episode: number, quality });
  playback = { provider: 'ani-cli', query, item: selection, episode: number, dub, quality, items: el('entertainment-results')._items, title: result.title };
  rememberResume({ provider: 'ani-cli', query, item: selection, episode: number, dub, quality, title: result.title });
  await play(result);
}

async function choose(action, item) {
  if (action === 'title') return chooseTitle(item);
  if (action === 'anime-episode') return playAnimeEpisode(item.number);
  if (action === 'season') {
    selection.season = item.number;
    const body = { query, selection: selection.selection, season: item.number };
    const result = await api('/pandaflix/episodes', { ...body, offset: 0 });
    episodeButtons(result, 'panda-episode', '/pandaflix/episodes', body); status(`Choose an episode · ${result.total} available.`); return;
  }
  if (action === 'panda-episode') return resolvePanda(selection.season, item.number);
  if (action === 'history') {
    status('Resolving your next episode…');
    return play(await api('/ani-cli/continue', { history_index: item.index, dub: el('entertainment-mode').value === 'dub', quality: el('entertainment-quality').value }));
  }
}

function nextNumber(current, items) {
  if (Array.isArray(items)) {
    const index = items.findIndex(value => String(value.number) === String(current));
    if (index >= 0 && index + 1 < items.length) return items[index + 1].number;
  }
  const numeric = Number(current);
  return Number.isFinite(numeric) ? String(numeric + 1) : null;
}

async function nextEpisode() {
  if (!playback) { status('No episode is playing.'); return; }
  const context = playback;
  if (context.provider === 'ani-cli') {
    const next = nextNumber(context.episode, context.items);
    if (next == null) { status('No next episode.'); return; }
    status('Loading next episode…');
    const result = await api('/ani-cli/resolve', { query: context.query, selection_index: context.item.id, dub: context.dub, episode: next, quality: context.quality });
    playback = { ...context, episode: next, title: result.title };
    rememberResume({ provider: 'ani-cli', query: context.query, item: context.item, episode: next, dub: context.dub, quality: context.quality, title: result.title });
    await play(result);
    return;
  }
  if (context.provider === 'pandaflix' && context.item?.kind !== 'movie') {
    const next = nextNumber(context.episode, context.items);
    if (next == null) { status('No next episode.'); return; }
    status('Loading next episode…');
    const result = await api('/pandaflix/resolve', { query: context.query, selection: context.item.selection, kind: context.item.kind, season: context.season, episode: next });
    playback = { ...context, episode: next, title: result.title };
    rememberResume({ provider: 'pandaflix', query: context.query, item: context.item, season: context.season, episode: next, title: result.title });
    await play(result);
    return;
  }
  status('No next episode.');
}

async function jumpEpisode() {
  if (active !== 'ani-cli' || !selection) return;
  const value = el('entertainment-jump-input').value.trim();
  if (!value) return;
  await playAnimeEpisode(value);
}

async function openFavorite(key) {
  const entry = prefs.favorites.find(favorite => favorite.key === key);
  if (!entry) return;
  if (!providers.some(provider => provider.id === entry.provider && provider.enabled)) { status('That provider is not installed.'); return; }
  if (active !== entry.provider) showProvider(entry.provider);
  query = entry.query || '';
  el('entertainment-query').value = query;
  selection = null;
  try { await chooseTitle(entry.item); } catch (error) { status(error.message); }
}

async function resumeSaved() {
  const resume = prefs.resume;
  if (!resume) return;
  if (!providers.some(provider => provider.id === resume.provider && provider.enabled)) { status('That provider is not installed.'); return; }
  if (active !== resume.provider) showProvider(resume.provider);
  query = resume.query || '';
  selection = resume.item;
  el('entertainment-query').value = query;
  try {
    if (resume.provider === 'ani-cli') {
      await playAnimeEpisode(resume.episode);
    } else {
      await resolvePanda(resume.season, resume.episode);
    }
  } catch (error) { status(error.message); }
}

function init() {
  if (initialized) return;
  initialized = true;
  el('entertainment-close')?.addEventListener('click', closeEntertainment);
  el('entertainment-home')?.addEventListener('click', showLanding);
  el('entertainment-back')?.addEventListener('click', () => { stopPlayer(); showProvider(active); });
  el('entertainment-fullscreen')?.addEventListener('click', () => el('entertainment-video').requestFullscreen?.());
  el('entertainment-next')?.addEventListener('click', () => nextEpisode().catch(error => status(error.message)));
  el('entertainment-video')?.addEventListener('ended', () => { if (prefs.autoplay) nextEpisode().catch(error => status(error.message)); });
  el('entertainment-autoplay')?.addEventListener('change', event => {
    prefs.autoplay = event.target.checked;
    const setting = el('entertainment-default-autoplay');
    if (setting) setting.checked = prefs.autoplay;
    savePrefs();
  });
  el('entertainment-resume')?.addEventListener('click', () => resumeSaved().catch(error => status(error.message)));
  el('entertainment-search')?.addEventListener('submit', event => { event.preventDefault(); search().catch(error => status(error.message)); });
  el('entertainment-jump')?.addEventListener('submit', event => { event.preventDefault(); jumpEpisode().catch(error => status(error.message)); });
  el('entertainment-history')?.addEventListener('click', async () => {
    try { status('Loading history…'); const result = await api('/ani-cli/history', {}); buttons(result.items, 'history', item => `${item.title} · Episode ${item.episode}`); status('Continue watching.'); }
    catch (error) { status(error.message); }
  });
  el('entertainment-section')?.addEventListener('click', () => openEntertainment());
  el('entertainment-section')?.addEventListener('keydown', event => {
    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); openEntertainment(); }
  });
  el('entertainment-default-provider')?.addEventListener('change', readSettingsPanel);
  el('entertainment-default-language')?.addEventListener('change', readSettingsPanel);
  el('entertainment-default-quality')?.addEventListener('change', readSettingsPanel);
  el('entertainment-default-autoplay')?.addEventListener('change', readSettingsPanel);
  document.addEventListener('click', event => {
    const provider = event.target.closest?.('[data-provider]');
    if (provider) { showProvider(provider.dataset.provider); return; }
    const favorite = event.target.closest?.('[data-ent-fav]');
    if (favorite) {
      const item = el('entertainment-results')._items?.[Number(favorite.dataset.entFav)];
      if (item) {
        toggleFavorite(active, item);
        const saved = isFavorite(active, item);
        favorite.setAttribute('aria-pressed', String(saved));
        favorite.textContent = saved ? '★' : '☆';
      }
      return;
    }
    const openFav = event.target.closest?.('[data-ent-open-fav]');
    if (openFav) { openFavorite(openFav.dataset.entOpenFav).catch(error => status(error.message)); return; }
    const button = event.target.closest?.('[data-ent-action]');
    const more = event.target.closest?.('[data-ent-more]');
    if (more) {
      const host = el('entertainment-results'); const page = host._page;
      api(page.path, { ...page.body, offset: Number(more.dataset.entMore) }).then(result => {
        const existing = host._items || []; const combined = [...existing, ...result.items];
        episodeButtons({ ...result, items: combined }, page.action, page.path, page.body);
      }).catch(error => status(error.message));
      return;
    }
    if (!button) return;
    const item = el('entertainment-results')._items?.[Number(button.dataset.entIndex)];
    if (item) choose(button.dataset.entAction, item).catch(error => status(error.message));
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !el('entertainment-modal')?.classList.contains('hidden')) closeEntertainment();
  });
  window.addEventListener('pandamonium:extensions-changed', refreshEntertainment);
}

export async function refreshEntertainment() {
  init();
  if (!prefsLoaded) await loadPrefs();
  try { providers = (await api()).providers; } catch (_) { providers = []; }
  const installed = providers.length > 0;
  document.querySelector('[data-settings-tab="entertainment"]')?.classList.toggle('hidden', !installed);
  el('entertainment-section')?.classList.toggle('hidden', !installed);
  renderSettingsPanel();
  if (!installed) closeEntertainment();
}

export async function openEntertainment() {
  await refreshEntertainment();
  if (!providers.length) return;
  applyPrefs();
  const tabs = el('entertainment-tabs');
  tabs.innerHTML = providers.length > 1 ? providers.map(provider => `<button type="button" data-provider="${provider.id}">${esc(provider.label)}</button>`).join('') : '';
  el('entertainment-modal').classList.remove('hidden');
  const preferred = providers.find(provider => provider.id === prefs.provider && provider.enabled);
  if (preferred) showProvider(preferred.id); else showLanding();
}

export function closeEntertainment() {
  stopPlayer();
  el('entertainment-modal')?.classList.add('hidden');
}

// Show the sidebar entry as soon as the app loads, not only after Settings has
// been opened. refreshEntertainment() is otherwise only reached from the
// settings-open path and the extensions-changed event, which left the launcher
// invisible on a fresh load.
if (typeof document !== 'undefined') {
  const bootEntertainment = () => { refreshEntertainment().catch(() => {}); };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bootEntertainment, { once: true });
  } else {
    bootEntertainment();
  }
}
