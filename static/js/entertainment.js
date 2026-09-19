import uiModule from './ui.js';

const el = id => document.getElementById(id);
const esc = value => uiModule.esc(String(value));
let providers = [];
let active = null;
let query = '';
let selection = null;
let hls = null;
let initialized = false;

async function api(path = '', body) {
  const response = await fetch(`/api/entertainment${path}`, {
    credentials: 'same-origin',
    ...(body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Entertainment unavailable');
  return data;
}

function stopPlayer() {
  if (hls) { hls.destroy(); hls = null; }
  const video = el('entertainment-video');
  if (video) { video.pause(); video.removeAttribute('src'); video.replaceChildren(); video.load(); }
}

function status(text = '') { el('entertainment-status').textContent = text; }

function buttons(items, action, label) {
  el('entertainment-results').innerHTML = items.length ? items.map((item, index) =>
    `<button type="button" class="entertainment-result" data-ent-action="${action}" data-ent-index="${index}"><span>${esc(label(item))}</span><b>›</b></button>`
  ).join('') : '<p class="entertainment-empty">Nothing found. Try a different search.</p>';
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
  el('entertainment-query').placeholder = anime ? 'Search anime' : 'Search movies and shows';
  el('entertainment-results').replaceChildren();
  status('');
  el('entertainment-tabs').querySelectorAll('button').forEach(button => button.classList.toggle('active', button.dataset.provider === id));
  el('entertainment-query').focus();
}

function showLanding() {
  if (providers.length === 1) { showProvider(providers[0].id); return; }
  stopPlayer(); active = null;
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
    episodeButtons(result, 'anime-episode', '/ani-cli/episodes', body); status(`Choose an episode · ${result.total} available.`);
  } else if (item.kind === 'movie') {
    await resolvePanda(0, 0);
  } else {
    const result = await api('/pandaflix/seasons', { query, selection: item.selection });
    buttons(result.items, 'season', value => value.label); status('Choose a season.');
  }
}

async function resolvePanda(season, episode) {
  status('Resolving stream…');
  await play(await api('/pandaflix/resolve', { query, selection: selection.selection, kind: selection.kind, season, episode }));
}

async function choose(action, item) {
  if (action === 'title') return chooseTitle(item);
  if (action === 'anime-episode') {
    status('Resolving stream…');
    return play(await api('/ani-cli/resolve', { query, selection_index: selection.id, dub: el('entertainment-mode').value === 'dub', episode: item.number, quality: el('entertainment-quality').value }));
  }
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

function init() {
  if (initialized) return;
  initialized = true;
  el('entertainment-close')?.addEventListener('click', closeEntertainment);
  el('entertainment-home')?.addEventListener('click', showLanding);
  el('entertainment-back')?.addEventListener('click', () => { stopPlayer(); showProvider(active); });
  el('entertainment-fullscreen')?.addEventListener('click', () => el('entertainment-video').requestFullscreen?.());
  el('entertainment-search')?.addEventListener('submit', event => { event.preventDefault(); search().catch(error => status(error.message)); });
  el('entertainment-history')?.addEventListener('click', async () => {
    try { status('Loading history…'); const result = await api('/ani-cli/history', {}); buttons(result.items, 'history', item => `${item.title} · Episode ${item.episode}`); status('Continue watching.'); }
    catch (error) { status(error.message); }
  });
  document.addEventListener('click', event => {
    const provider = event.target.closest?.('[data-provider]');
    if (provider) { showProvider(provider.dataset.provider); return; }
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
  try { providers = (await api()).providers; } catch (_) { providers = []; }
  document.querySelector('[data-settings-tab="entertainment"]')?.classList.toggle('hidden', providers.length === 0);
  if (!providers.length) closeEntertainment();
}

export async function openEntertainment() {
  await refreshEntertainment();
  if (!providers.length) return;
  const tabs = el('entertainment-tabs');
  tabs.innerHTML = providers.length > 1 ? providers.map(provider => `<button type="button" data-provider="${provider.id}">${esc(provider.label)}</button>`).join('') : '';
  el('entertainment-modal').classList.remove('hidden');
  showLanding();
}

export function closeEntertainment() {
  stopPlayer();
  el('entertainment-modal')?.classList.add('hidden');
}
