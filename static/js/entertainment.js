import uiModule from './ui.js';

const el = id => document.getElementById(id);
const esc = value => uiModule.esc(String(value));
let providers = [];
let active = null;
let query = '';
let selection = null;
let slots = null;
let activeSlotKey = null;
let preloaded = null;
let preloadToken = 0;
let pendingResume = 0;
let resumeSaveAt = 0;
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
  const same = prefs.resume
    && prefs.resume.provider === record.provider
    && String(prefs.resume.episode) === String(record.episode);
  prefs.resume = { ...record, position: same ? (prefs.resume.position || 0) : 0, at: Date.now() };
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

function initSlots() {
  if (slots) return;
  slots = {
    a: { video: el('entertainment-video'), hls: null },
    b: { video: el('entertainment-video-next'), hls: null },
  };
  for (const key of ['a', 'b']) {
    slots[key].video?.addEventListener('ended', () => { if (key === activeSlotKey) handleEnded(); });
    slots[key].video?.addEventListener('timeupdate', event => {
      if (!activeSlotKey || slots[activeSlotKey].video !== event.target || !playback) return;
      const now = Date.now();
      if (now - resumeSaveAt < 5000) return;
      resumeSaveAt = now;
      if (prefs.resume) { prefs.resume.position = Math.floor(event.target.currentTime || 0); prefs.resume.at = now; savePrefs(); }
    });
  }
}

function idleSlotKey() {
  return activeSlotKey === 'a' ? 'b' : 'a';
}

function teardownSlot(slot) {
  if (!slot) return;
  if (slot.hls) { slot.hls.destroy(); slot.hls = null; }
  const video = slot.video;
  if (video) { video.pause(); video.removeAttribute('src'); video.replaceChildren(); video.load(); }
}

function stopPlayer() {
  initSlots();
  preloaded = null;
  preloadToken += 1;
  for (const key of ['a', 'b']) teardownSlot(slots[key]);
  activeSlotKey = null;
}

function status(text = '') { el('entertainment-status').textContent = text; }

function playSound(id) {
  const audio = el(id);
  if (!audio) return;
  try { audio.currentTime = 0; audio.play().catch(() => {}); } catch (_) {}
}

function buttons(items, action, label) {
  const host = el('entertainment-results');
  const cards = action === 'title';
  host.classList.toggle('is-cards', cards);
  if (!items.length) {
    host.innerHTML = '<p class="entertainment-empty">Nothing found. Try a different search.</p>';
    host._items = items;
    return;
  }
  host.innerHTML = items.map((item, index) => {
    if (!cards) {
      return `<button type="button" class="entertainment-result" data-ent-action="${action}" data-ent-index="${index}"><span>${esc(label(item))}</span><b>›</b></button>`;
    }
    const saved = isFavorite(active, item);
    const kind = item.kind === 'series' ? 'TV Series' : (item.kind === 'movie' ? 'Movie' : '');
    const cta = active === 'ani-cli' ? 'View Episodes' : (item.kind === 'movie' ? 'Watch Now' : 'View Seasons');
    return `<article class="ent-card" data-kind="${esc(active)}">
      <span class="ent-card-art" aria-hidden="true"><span class="ent-card-initials">${esc((item.title || '?').trim().slice(0, 2).toUpperCase())}</span></span>
      <div class="ent-card-body">
        <h3>${esc(item.title)}</h3>
        ${kind ? `<div class="ent-card-badges"><span>${kind}</span></div>` : ''}
        <div class="ent-card-actions">
          <button type="button" class="ent-card-play" data-ent-action="${action}" data-ent-index="${index}">▶ ${cta}</button>
          <button type="button" class="ent-card-fav" data-ent-fav="${index}" aria-pressed="${saved}" aria-label="${saved ? 'Remove favorite' : 'Save favorite'}: ${esc(item.title)}">${saved ? '★' : '☆'}</button>
        </div>
      </div>
    </article>`;
  }).join('');
  host._items = items;
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
  el('entertainment-modal').classList.remove('is-landing');
  el('entertainment-landing').classList.add('hidden');
  el('entertainment-player').classList.add('hidden');
  el('entertainment-browser').classList.remove('hidden');
  const anime = id === 'ani-cli';
  el('entertainment-browser').dataset.kind = id;
  el('entertainment-eyebrow').textContent = anime ? 'STREAM ANYTHING · NO LIMITS' : 'PRIVATE RUNTIME · UPSTREAM CLI';
  el('entertainment-chips').innerHTML = providerChips(id);
  el('entertainment-results-title').textContent = 'Results';
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
  el('entertainment-tabs')?.querySelectorAll('button').forEach(button => button.classList.toggle('active', button.dataset.provider === id));
  document.querySelectorAll('[data-ent-browse]').forEach(button => button.classList.toggle('active', button.dataset.entBrowse === id));
  el('entertainment-query').focus();
}

const ENT_ICON = {
  lock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="10" width="16" height="10" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/></svg>',
  infinity: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M7 12c0-2 1.5-3.5 3.5-3.5S14 12 14 12s1.5 3.5 3.5 3.5S21 14 21 12s-1.5-3.5-3.5-3.5S14 12 14 12s-1.5 3.5-3.5 3.5S7 14 7 12z"/></svg>',
  bolt: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2 4 14h6l-1 8 9-12h-6l1-8z"/></svg>',
  panda: '<svg viewBox="0 0 100 100"><circle cx="24" cy="26" r="15" fill="currentColor"/><circle cx="76" cy="26" r="15" fill="currentColor"/><circle cx="50" cy="54" r="40" fill="none" stroke="currentColor" stroke-width="7"/><ellipse cx="34" cy="50" rx="12" ry="16" fill="currentColor"/><ellipse cx="66" cy="50" rx="12" ry="16" fill="currentColor"/><ellipse cx="50" cy="74" rx="7" ry="5" fill="currentColor"/></svg>',
  film: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M8 4v5M16 4v5"/></svg>',
  search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>',
};

function providerBlurb(id) {
  return id === 'ani-cli'
    ? 'Dive into a massive library of anime, from timeless classics to the latest releases.'
    : 'Blockbusters, binge-worthy series, and hidden gems. All in one place.';
}

function providerChips(id) {
  const chips = id === 'ani-cli'
    ? [[ENT_ICON.panda, 'AnimeCLI Powered'], [ENT_ICON.film, 'Dub / Sub'], ['<b>HD</b>', 'Up to 1080p/4K'], [ENT_ICON.bolt, 'Fast & Private']]
    : [[ENT_ICON.film, 'PandaFlix Powered'], ['<b>HD</b>', 'Movie / TV / Special'], ['<b>HD</b>', 'Up to 4K'], [ENT_ICON.bolt, 'Fast & Private']];
  return chips.map(([icon, label]) => `<span class="ent-chip">${icon}${label}</span>`).join('');
}

function showLanding() {
  if (providers.length === 1) { showProvider(providers[0].id); return; }
  stopPlayer(); active = null; playback = null;
  el('entertainment-modal').classList.add('is-landing');
  el('entertainment-browser').classList.add('hidden');
  el('entertainment-player').classList.add('hidden');
  el('entertainment-landing').classList.remove('hidden');
  el('entertainment-landing').innerHTML =
    `<div class="ent-landing-inner">
       <div class="ent-landing-copy">
         <p class="ent-eyebrow"><i></i>PRIVATE STREAMING. NO LIMITS.<i></i></p>
         <h1>What are we<br><em>watching tonight?</em></h1>
         <p class="ent-landing-sub">Your private gateway to anime, movies, and shows. Stream what you love. On your terms.</p>
         <div class="ent-feature-chips">
           <div class="ent-feature-chip"><span class="ent-feature-icon">${ENT_ICON.lock}</span><div><strong>Private</strong><small>Your media, your space.</small></div></div>
           <div class="ent-feature-chip"><span class="ent-feature-icon">${ENT_ICON.infinity}</span><div><strong>Unlimited</strong><small>Anime, movies, and more.</small></div></div>
           <div class="ent-feature-chip"><span class="ent-feature-icon">${ENT_ICON.bolt}</span><div><strong>Always On</strong><small>Entertainment, no limits.</small></div></div>
         </div>
       </div>
       <div class="ent-choice-grid">` + providers.map(provider =>
        `<button type="button" class="entertainment-choice" data-provider="${provider.id}" ${provider.enabled ? '' : 'disabled'}>
           <span class="ent-choice-art" data-kind="${esc(provider.id)}"></span>
           <span class="ent-choice-body">
             <span class="ent-choice-badge" aria-hidden="true">${provider.id === 'ani-cli' ? ENT_ICON.panda : ENT_ICON.film}</span>
             <strong>${esc(provider.label)}</strong>
             <small>${providerBlurb(provider.id)}</small>
             <span class="ent-choice-cta">${provider.enabled ? (provider.id === 'ani-cli' ? 'Open AniCLI' : 'Open Pandaflix') : 'Enable this plugin first'} <b>→</b></span>
           </span>
         </button>`
      ).join('') + `</div>
     </div>
     <div class="ent-corner ent-corner-left"><i></i><span>GOOD STORIES<br>GO FURTHER.</span></div>
     <div class="ent-corner ent-corner-right"><span>SAME PASSION.<br>BIGGER WORLDS.</span><i></i></div>`;
}

function loadIntoSlot(key, result) {
  const slot = slots[key];
  teardownSlot(slot);
  const video = slot.video;
  for (const subtitle of result.subtitles || []) {
    const track = document.createElement('track');
    track.kind = 'subtitles'; track.src = subtitle.url; track.label = subtitle.label; track.srclang = subtitle.language;
    video.appendChild(track);
  }
  if (result.format === 'hls' && window.Hls?.isSupported()) {
    const instance = new window.Hls({
      enableWorker: true,
      lowLatencyMode: false,
      maxBufferLength: 60,
      maxMaxBufferLength: 600,
      maxBufferSize: 120 * 1000 * 1000,
      backBufferLength: 30,
    });
    instance.loadSource(result.url); instance.attachMedia(video);
    slot.hls = instance;
  } else {
    video.src = result.url;
  }
  return slot;
}

async function activateSlot(key) {
  activeSlotKey = key;
  for (const name of ['a', 'b']) {
    const video = slots[name].video;
    if (!video) continue;
    const isActive = name === key;
    video.classList.toggle('is-active', isActive);
    video.muted = !isActive;
  }
  const video = slots[key].video;
  try { await video.play(); } catch (_) { status('Press play to start.'); }
}

function showPlayer(title) {
  el('entertainment-browser').classList.add('hidden');
  el('entertainment-player').classList.remove('hidden');
  el('entertainment-now-playing').textContent = title;
}

async function play(result) {
  initSlots();
  stopPlayer();
  showPlayer(result.title);
  const key = idleSlotKey();
  loadIntoSlot(key, result);
  if (pendingResume > 0) {
    const video = slots[key].video;
    const seek = () => { try { video.currentTime = pendingResume; } catch (_) {} };
    if (video.readyState >= 1) seek(); else video.addEventListener('loadedmetadata', seek, { once: true });
    pendingResume = 0;
  }
  await activateSlot(key);
  schedulePreload();
}

async function search() {
  query = el('entertainment-query').value.trim();
  if (!query) return;
  status('Searching upstream…'); el('entertainment-results').replaceChildren();
  const body = active === 'ani-cli' ? { query, dub: el('entertainment-mode').value === 'dub' } : { query };
  const result = await api(`/${active}/search`, body);
  buttons(result.items, 'title', item => active === 'ani-cli' ? item.title : `${item.title} · ${item.kind === 'series' ? 'Series' : 'Movie'}`);
  el('entertainment-results-title').textContent = `${result.items.length} Results for “${query}”`;
  status('');
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

function previousNumber(current, items) {
  if (Array.isArray(items)) {
    const index = items.findIndex(value => String(value.number) === String(current));
    if (index > 0) return items[index - 1].number;
  }
  const numeric = Number(current);
  return Number.isFinite(numeric) && numeric > 1 ? String(numeric - 1) : null;
}

function episodeTarget(step) {
  if (!playback) return null;
  const context = playback;
  const pick = (current, items) => (step > 0 ? nextNumber(current, items) : previousNumber(current, items));
  if (context.provider === 'ani-cli') {
    const episode = pick(context.episode, context.items);
    if (episode == null) return null;
    return {
      playback: { ...context, episode },
      resolve: () => api('/ani-cli/resolve', { query: context.query, selection_index: context.item.id, dub: context.dub, episode, quality: context.quality }),
      resume: (title) => ({ provider: 'ani-cli', query: context.query, item: context.item, episode, dub: context.dub, quality: context.quality, title }),
    };
  }
  if (context.provider === 'pandaflix' && context.item?.kind !== 'movie') {
    const episode = pick(context.episode, context.items);
    if (episode == null) return null;
    return {
      playback: { ...context, episode },
      resolve: () => api('/pandaflix/resolve', { query: context.query, selection: context.item.selection, kind: context.item.kind, season: context.season, episode }),
      resume: (title) => ({ provider: 'pandaflix', query: context.query, item: context.item, season: context.season, episode, title }),
    };
  }
  return null;
}

function nextPlaybackTarget() { return episodeTarget(1); }

// Warm the next episode into the idle video while the current one plays, so an
// episode change does not have to resolve and buffer from scratch. This is why
// the active slot alternates between the two stacked <video> elements.
async function schedulePreload() {
  const target = nextPlaybackTarget();
  if (!target || !slots) return;
  const key = idleSlotKey();
  const token = ++preloadToken;
  try {
    const result = await target.resolve();
    if (token !== preloadToken || !slots) return;
    loadIntoSlot(key, result);
    preloaded = { key, target, result };
  } catch (_) { /* fall back to resolving on demand */ }
}

async function advanceToPreloaded() {
  if (!preloaded) return false;
  const { key, target, result } = preloaded;
  preloaded = null;
  playback = { ...target.playback, title: result.title };
  rememberResume(target.resume(result.title));
  showPlayer(result.title);
  await activateSlot(key);
  schedulePreload();
  return true;
}

async function nextEpisode() {
  if (!playback) { status('No episode is playing.'); return; }
  if (await advanceToPreloaded()) return;
  const target = nextPlaybackTarget();
  if (!target) { status('No next episode.'); return; }
  status('Loading next episode…');
  const result = await target.resolve();
  playback = { ...target.playback, title: result.title };
  rememberResume(target.resume(result.title));
  await play(result);
}

async function handleEnded() {
  if (!prefs.autoplay) return;
  try { await nextEpisode(); } catch (error) { status(error.message); }
}

async function previousEpisode() {
  if (!playback) { status('No episode is playing.'); return; }
  const target = episodeTarget(-1);
  if (!target) { status('No previous episode.'); return; }
  status('Loading previous episode…');
  const result = await target.resolve();
  playback = { ...target.playback, title: result.title };
  rememberResume(target.resume(result.title));
  await play(result);
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
  pendingResume = Number(resume.position) || 0;
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
  el('entertainment-fanfare-btn')?.addEventListener('click', () => playSound('entertainment-fanfare'));
  el('ent-global-search')?.addEventListener('click', () => el('entertainment-query')?.focus());
  el('entertainment-back')?.addEventListener('click', () => { stopPlayer(); showProvider(active); });
  initSlots();
  el('entertainment-fullscreen')?.addEventListener('click', () => (slots?.[activeSlotKey]?.video || el('entertainment-video'))?.requestFullscreen?.());
  el('entertainment-next')?.addEventListener('click', () => nextEpisode().catch(error => status(error.message)));
  el('entertainment-prev')?.addEventListener('click', () => previousEpisode().catch(error => status(error.message)));
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
    if (provider) {
      playSound(provider.dataset.provider === 'ani-cli' ? 'entertainment-anime-wow' : 'entertainment-fanfare');
      showProvider(provider.dataset.provider);
      return;
    }
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
  document.querySelectorAll('[data-ent-browse]').forEach(button => button.addEventListener('click', () => {
    const id = button.dataset.entBrowse;
    if (providers.some(provider => provider.id === id)) showProvider(id);
  }));
  document.querySelectorAll('[data-ent-nav]').forEach(button => button.addEventListener('click', () => {
    const id = button.dataset.entNav;
    if (id === 'home') { showLanding(); return; }
    if (providers.some(provider => provider.id === id)) showProvider(id);
  }));
}

export async function refreshEntertainment() {
  init();
  if (!prefsLoaded) await loadPrefs();
  try { providers = (await api()).providers; } catch (_) { providers = []; }
  const installed = providers.length > 0;
  document.querySelector('[data-settings-tab="entertainment"]')?.classList.toggle('hidden', !installed);
  el('entertainment-section')?.classList.toggle('hidden', !installed);
  const footerVersion = el('entertainment-footer-version');
  if (footerVersion && window._appVersion) footerVersion.textContent = `v${window._appVersion}`;
  renderSettingsPanel();
  if (!installed) closeEntertainment();
}

export async function openEntertainment() {
  await refreshEntertainment();
  if (!providers.length) return;
  applyPrefs();
  const tabs = el('entertainment-tabs');
  if (tabs) tabs.innerHTML = providers.length > 1 ? providers.map(provider => `<button type="button" data-provider="${provider.id}">${esc(provider.label)}</button>`).join('') : '';
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
