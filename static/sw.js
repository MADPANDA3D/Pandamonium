// static/sw.js — Pandamonium PWA Service Worker
// Strategy:
//   - HTML (navigation): stale-while-revalidate. Instant open from cache,
//     background refresh so the next open has latest HTML.
//   - JS/CSS (/static/*.js|.css): network-first, cache fallback for offline.
//     (So code/style edits show up on a normal reload, no manual cache clear.)
//   - Other static assets (images/fonts/libs): cache-first with bg refresh.
//   - API / non-GET: never cached.
// Bump CACHE_NAME whenever the precache list or SW logic changes.
const CACHE_NAME = 'pandamonium-v405';
const UPDATE_RECONCILE_QUERY = 'pandamonium-update-reconcile';
const UPDATE_WORKER_PENDING = 'pending-worker-update';
const UPDATE_RECONCILE_ATTEMPTS = 8;
const UPDATE_RECONCILE_DELAY_MS = 650;
const UPDATE_STATUS_TIMEOUT_MS = 5000;
const UPDATE_CLIENT_NAVIGATION_GRACE_MS = 750;
const UPDATE_ACTIVE_STATES = new Set(['queued', 'running']);
const UPDATE_TERMINAL_STATES = new Set(['succeeded', 'release_active', 'recovered', 'rolled_back', 'failed']);
const UPDATE_RETRYABLE_STATUS_CODES = new Set([408, 425, 429]);

// Core shell precached on install so repeat opens are instant without any
// network wait. Keep this list in sync with the <script type="module"> tags
// and <link rel="stylesheet"> in index.html.
const PRECACHE = [
  '/',
  '/static/style.css',
  '/static/app.js',
  '/static/js/brand.js',
  '/static/js/storage.js',
  '/static/js/clientState.js',
  '/static/js/ui.js',
  '/static/js/markdown.js',
  '/static/js/dragSort.js',
  '/static/js/sessions.js',
  '/static/js/modelPicker.js',
  '/static/js/codexWorkspace.js',
  '/static/js/conversationContext.js',
  '/static/js/memory.js',
  '/static/js/skills.js',
  '/static/js/tourHints.js',
  '/static/js/fileHandler.js',
  '/static/js/voiceLifecycle.js',
  '/static/js/voiceRecorder.js',
  '/static/js/models.js',
  '/static/js/rag.js',
  '/static/js/presets.js',
  '/static/js/search.js',
  '/static/js/spinner.js',
  '/static/js/tts-ai.js',
  '/static/js/document.js',
  '/static/js/gallery.js',
  '/static/js/chatRenderer.js',
  '/static/js/codeRunner.js',
  '/static/js/chatStream.js',
  '/static/js/foregroundActions.js',
  '/static/js/jarvisVoice.js',
  '/static/js/voiceOrbMedia.js',
  '/static/voice-orb-media.json',
  '/static/js/chat.js',
  '/static/js/cookbook.js',
  '/static/js/search-chat.js',
  '/static/js/compare/index.js',
  '/static/js/theme.js',
  '/static/js/madMcp.js',
  '/static/icons/pandamonium.png',
  '/static/js/censor.js',
  '/static/js/settings.js',
  '/static/js/admin.js',
  '/static/js/init.js',
  '/static/js/slashCommands.js',
  '/static/js/emailInbox.js',
  '/static/js/emailLibrary/utils.js',
  '/static/js/emailLibrary/signatureFold.js',
  '/static/js/emailLibrary/state.js',
  '/static/js/notes.js',
  '/static/js/tasks.js',
  '/static/js/calendar.js',
  '/static/js/calendar/utils.js',
  '/static/js/calendar/reminders.js',
  '/static/js/group.js',
  '/static/js/keyboard-shortcuts.js',
  '/static/js/sidebar-layout.js',
  '/static/js/section-management.js',
  '/static/lib/highlight.min.js',
  '/static/lib/mermaid.min.js?v=11.17.2',
  '/static/icons/brands/github.svg',
  '/static/icons/brands/instagram.svg',
  '/static/icons/brands/facebook.svg',
];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE_NAME).then(cache =>
      // addAll is atomic — if any item fails, none are cached. Use individual
      // puts so a single 404 can't block the whole install.
      Promise.all(
        PRECACHE.map(url =>
          fetch(url, { cache: 'reload' })
            .then(res => res.ok ? cache.put(url, res) : null)
            .catch(() => null)
        )
      )
    )
  );
  self.skipWaiting();
});

function wait(delayMs) {
  return new Promise(resolve => setTimeout(resolve, delayMs));
}

async function waitForTerminalUpdate() {
  for (let attempt = 0; attempt < UPDATE_RECONCILE_ATTEMPTS; attempt += 1) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), UPDATE_STATUS_TIMEOUT_MS);
    try {
      const response = await fetch('/api/update/status', {
        cache: 'no-store',
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
        signal: controller.signal,
      });
      if (response.status === 401 || response.status === 403 || response.redirected) return false;
      if (!response.ok) {
        if (response.status >= 500 || UPDATE_RETRYABLE_STATUS_CODES.has(response.status)) {
          throw new Error('Update status unavailable');
        }
        return false;
      }
      const operation = await response.json();
      if (!operation || typeof operation !== 'object') {
        throw new Error('Invalid update status response');
      }
      if (UPDATE_TERMINAL_STATES.has(operation?.status)) return true;
      if (!UPDATE_ACTIVE_STATES.has(operation?.status)) return false;
    } catch (_) {
    } finally {
      clearTimeout(timeout);
    }
    if (attempt + 1 < UPDATE_RECONCILE_ATTEMPTS) await wait(UPDATE_RECONCILE_DELAY_MS);
  }
  return false;
}

async function reconcileUpdateClients() {
  if (!await waitForTerminalUpdate()) return;
  const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
  const navigations = Promise.allSettled(windows.map(client => {
    const url = new URL(client.url);
    const marker = url.searchParams.get(UPDATE_RECONCILE_QUERY);
    if (marker === UPDATE_WORKER_PENDING) {
      client.postMessage?.({ type: 'pandamonium-update-reconciled' });
      return;
    }
    if (marker === CACHE_NAME) return;
    url.searchParams.set(UPDATE_RECONCILE_QUERY, CACHE_NAME);
    return client.navigate(url.href);
  }));
  // Chromium can keep an in-scope navigation pending until this replacement
  // worker's activate event completes. Keep the worker alive long enough to
  // submit and normally settle every navigation, but break that lifecycle
  // dependency after a bounded grace period so activation cannot deadlock.
  await Promise.race([navigations, wait(UPDATE_CLIENT_NAVIGATION_GRACE_MS)]);
}

async function activateWorker() {
  const keys = await caches.keys();
  const replacesPandamoniumWorker = keys.some(
    key => key.startsWith('pandamonium-') && key !== CACHE_NAME,
  );
  await Promise.all(keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key)));
  await self.clients.claim();
  if (replacesPandamoniumWorker) await reconcileUpdateClients();
}

self.addEventListener('activate', (e) => {
  e.waitUntil(activateWorker());
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);

  // Never touch API calls or non-GET.
  if (url.pathname.startsWith('/api/') || e.request.method !== 'GET') return;

  // HTML navigation: stale-while-revalidate the app shell — but ONLY for the
  // SPA root. Other navigations (e.g. a deep-linked /static/*.html page) must
  // go to the network/static handlers below; otherwise every navigation was
  // served the app index, replacing the page the user actually asked for.
  if (e.request.mode === 'navigate' && url.pathname === '/') {
    e.respondWith(
      caches.open(CACHE_NAME).then(async cache => {
        const cached = await cache.match('/');
        const network = fetch(e.request).then(res => {
          if (res && res.ok) cache.put('/', res.clone());
          return res;
        }).catch(() => cached);
        return cached || network;
      })
    );
    return;
  }

  // JS/CSS: network-first — always try the network so code/style edits show up
  // on a normal reload; fall back to cache only when offline.
  if (url.pathname.startsWith('/static/') && /\.(js|css)(\?|$)/.test(url.pathname + url.search)) {
    e.respondWith(
      fetch(e.request).then(res => {
        if (res && res.ok) {
          const copy = res.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(e.request, copy));
        }
        return res;
      }).catch(() => caches.match(e.request))
    );
    return;
  }

  // Other static assets (images, fonts, libs): cache-first with background refresh.
  if (url.pathname.startsWith('/static/')) {
    e.respondWith(
      caches.open(CACHE_NAME).then(async cache => {
        const cached = await cache.match(e.request);
        const fetching = fetch(e.request).then(res => {
          if (res && res.ok) cache.put(e.request, res.clone());
          return res;
        }).catch(() => cached);
        return cached || fetching;
      })
    );
    return;
  }
});
