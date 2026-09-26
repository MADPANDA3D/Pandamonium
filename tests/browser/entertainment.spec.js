import { expect, test } from '@playwright/test';

test('Entertainment settings tab holds defaults and the sidebar opens the player', async ({ page }) => {
  let installed = [];
  await page.addInitScript(() => {
    HTMLMediaElement.prototype.play = async function () {};
    HTMLMediaElement.prototype.pause = function () {};
    HTMLMediaElement.prototype.load = function () {};
  });
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/entertainment') return route.fulfill({ json: { providers: installed } });
    if (path === '/api/prefs/entertainment') return route.fulfill({ json: { key: 'entertainment', value: null } });
    if (path === '/api/entertainment/pandaflix/search') return route.fulfill({ json: { items: [
      { selection: '[movie] Arrival (2016)', kind: 'movie', title: 'Arrival (2016)' },
    ] } });
    if (path === '/api/entertainment/pandaflix/resolve') return route.fulfill({ json: {
      title: 'Arrival', url: 'https://media.example/arrival.mp4', format: 'file', proxied: false, subtitles: [],
    } });
    if (path === '/api/auth/status') return route.fulfill({ json: { username: 'tester', is_admin: true, privileges: {} } });
    if (['/api/models', '/api/model-endpoints', '/api/sessions'].includes(path)) return route.fulfill({ json: [] });
    return route.fulfill({ json: {} });
  });
  await page.goto('/static/index.html');
  await page.evaluate(async () => (await import('/static/js/settings.js')).open());
  await expect(page.locator('[data-settings-tab="entertainment"]')).toBeHidden();
  await expect(page.locator('#entertainment-section')).toBeHidden();

  installed = [{ id: 'ani-cli', label: 'Anime', enabled: true }];
  await page.evaluate(() => window.dispatchEvent(new Event('pandamonium:extensions-changed')));
  await expect(page.locator('[data-settings-tab="entertainment"]')).toBeVisible();
  await expect(page.locator('#entertainment-section')).toBeVisible();

  // Settings tab now shows modifiable defaults instead of launching the player.
  await page.locator('[data-settings-tab="entertainment"]').click();
  await expect(page.locator('[data-settings-panel="entertainment"]')).toBeVisible();
  await expect(page.locator('#entertainment-modal')).toBeHidden();

  // The sidebar entry opens the player; one provider opens directly.
  await page.locator('#entertainment-section').click();
  await expect(page.locator('#entertainment-modal')).toBeVisible();
  await expect(page.locator('#entertainment-heading')).toHaveText('Anime');
  await expect(page.locator('#entertainment-landing')).toBeHidden();
  await page.locator('#entertainment-close').click();

  installed = [
    { id: 'ani-cli', label: 'Anime', enabled: true },
    { id: 'pandaflix', label: 'Movies & Shows', enabled: true },
  ];
  await page.evaluate(async () => { window.dispatchEvent(new Event('pandamonium:extensions-changed')); (await import('/static/js/settings.js')).open(); });
  await page.locator('#entertainment-section').click();
  await expect(page.locator('#entertainment-landing')).toBeVisible();
  await expect(page.locator('.entertainment-choice')).toHaveCount(2);
  await page.locator('.entertainment-choice[data-provider="pandaflix"]').click();
  await page.locator('#entertainment-query').fill('Arrival');
  await page.locator('#entertainment-search button[type="submit"]').click();
  await page.locator('.ent-card-play').click();
  await expect(page.locator('#entertainment-player')).toBeVisible();
  await expect(page.locator('#entertainment-now-playing')).toHaveText('Arrival');
  await expect(page.locator('#entertainment-video')).toHaveAttribute('src', 'https://media.example/arrival.mp4');
});

test('Entertainment defaults from Settings apply when the player opens', async ({ page }) => {
  await page.addInitScript(() => {
    HTMLMediaElement.prototype.play = async function () {};
    HTMLMediaElement.prototype.pause = function () {};
    HTMLMediaElement.prototype.load = function () {};
  });
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/entertainment') return route.fulfill({ json: { providers: [
      { id: 'ani-cli', label: 'Anime', enabled: true },
      { id: 'pandaflix', label: 'Movies & Shows', enabled: true },
    ] } });
    if (path === '/api/prefs/entertainment') return route.fulfill({ json: { key: 'entertainment', value: null } });
    if (path === '/api/auth/status') return route.fulfill({ json: { username: 'tester', is_admin: true, privileges: {} } });
    if (['/api/models', '/api/model-endpoints', '/api/sessions'].includes(path)) return route.fulfill({ json: [] });
    return route.fulfill({ json: {} });
  });
  await page.goto('/static/index.html');
  await page.evaluate(async () => (await import('/static/js/settings.js')).open());
  await page.locator('[data-settings-tab="entertainment"]').click();
  await page.locator('#entertainment-default-provider').selectOption('pandaflix');
  await page.locator('#entertainment-default-language').selectOption('dub');
  await page.locator('#entertainment-default-quality').selectOption('720p');

  await page.locator('#entertainment-section').click();
  await expect(page.locator('#entertainment-modal')).toBeVisible();
  await expect(page.locator('#entertainment-heading')).toHaveText('Movies & Shows');
  await expect(page.locator('#entertainment-landing')).toBeHidden();
  await page.locator('#entertainment-home').click();
  await expect(page.locator('#entertainment-landing')).toBeVisible();
  await page.locator('.entertainment-choice[data-provider="ani-cli"]').click();
  await expect(page.locator('#entertainment-mode')).toHaveValue('dub');
  await expect(page.locator('#entertainment-quality')).toHaveValue('720p');
});

test('Entertainment player supports jump, next, autoplay, favorites, and continue watching', async ({ page }) => {
  await page.addInitScript(() => {
    HTMLMediaElement.prototype.play = async function () {};
    HTMLMediaElement.prototype.pause = function () {};
    HTMLMediaElement.prototype.load = function () {};
  });
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/entertainment') return route.fulfill({ json: { providers: [
      { id: 'ani-cli', label: 'Anime', enabled: true },
      { id: 'pandaflix', label: 'Movies & Shows', enabled: true },
    ] } });
    if (path === '/api/prefs/entertainment') return route.fulfill({ json: { key: 'entertainment', value: null } });
    if (path === '/api/entertainment/ani-cli/search') return route.fulfill({ json: { items: [{ id: 1, title: 'Naruto' }] } });
    if (path === '/api/entertainment/ani-cli/episodes') return route.fulfill({ json: { items: [
      { number: '1', label: 'Episode 1' }, { number: '2', label: 'Episode 2' }, { number: '3', label: 'Episode 3' },
    ], total: 3, next_offset: -1 } });
    if (path === '/api/entertainment/ani-cli/resolve') {
      const body = JSON.parse(route.request().postData() || '{}');
      return route.fulfill({ json: { title: `Naruto Episode ${body.episode}`, url: 'https://media.example/ep.mp4', format: 'file', proxied: false, subtitles: [] } });
    }
    if (path === '/api/auth/status') return route.fulfill({ json: { username: 'tester', is_admin: true, privileges: {} } });
    if (['/api/models', '/api/model-endpoints', '/api/sessions'].includes(path)) return route.fulfill({ json: [] });
    return route.fulfill({ json: {} });
  });
  await page.goto('/static/index.html');
  await page.evaluate(async () => (await import('/static/js/settings.js')).open());
  await page.locator('#entertainment-section').click();
  await page.locator('.entertainment-choice[data-provider="ani-cli"]').click();
  await page.locator('#entertainment-query').fill('Naruto');
  await page.locator('#entertainment-search button[type="submit"]').click();
  await page.locator('.ent-card-play').first().click();

  // Jump straight to an episode instead of paging.
  await expect(page.locator('#entertainment-jump')).toBeVisible();
  await page.locator('#entertainment-jump-input').fill('3');
  await page.locator('#entertainment-jump button[type="submit"]').click();
  await expect(page.locator('#entertainment-player')).toBeVisible();
  await expect(page.locator('#entertainment-now-playing')).toHaveText('Naruto Episode 3');

  // Next advances to the following episode, and autoplay can be enabled.
  await page.locator('#entertainment-next').click();
  await expect(page.locator('#entertainment-now-playing')).toHaveText('Naruto Episode 4');
  // The advanced-to (preloaded) element must still expose native controls.
  await expect(page.locator('.ent-video.is-active')).toHaveAttribute('controls', '');
  await page.locator('#entertainment-autoplay').check();
  await expect(page.locator('#entertainment-autoplay')).toBeChecked();

  // Previous steps back if you skip too far ahead.
  await page.locator('#entertainment-prev').click();
  await expect(page.locator('#entertainment-now-playing')).toHaveText('Naruto Episode 3');

  // Refresh re-resolves the current stream in place, keeping the title.
  await page.locator('#entertainment-refresh').click();
  await expect(page.locator('#entertainment-player')).toBeVisible();
  await expect(page.locator('#entertainment-now-playing')).toHaveText('Naruto Episode 3');

  // Favoriting still works from the result card.
  await page.locator('#entertainment-back').click();
  await page.locator('#entertainment-query').fill('Naruto');
  await page.locator('#entertainment-search button[type="submit"]').click();
  await page.locator('.ent-card-fav').first().click();
  await expect(page.locator('.ent-card-fav').first()).toHaveAttribute('aria-pressed', 'true');
});

test('Entertainment warms the next episode into the idle player', async ({ page }) => {
  await page.addInitScript(() => {
    HTMLMediaElement.prototype.play = async function () {};
    HTMLMediaElement.prototype.pause = function () {};
    HTMLMediaElement.prototype.load = function () {};
  });
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/entertainment') return route.fulfill({ json: { providers: [
      { id: 'ani-cli', label: 'Anime', enabled: true },
    ] } });
    if (path === '/api/prefs/entertainment') return route.fulfill({ json: { key: 'entertainment', value: null } });
    if (path === '/api/entertainment/ani-cli/search') return route.fulfill({ json: { items: [{ id: 1, title: 'Naruto' }] } });
    if (path === '/api/entertainment/ani-cli/episodes') return route.fulfill({ json: { items: [
      { number: '1', label: 'Episode 1' }, { number: '2', label: 'Episode 2' },
    ], total: 2, next_offset: -1 } });
    if (path === '/api/entertainment/ani-cli/resolve') {
      const body = JSON.parse(route.request().postData() || '{}');
      return route.fulfill({ json: { title: `Naruto Episode ${body.episode}`, url: `https://media.example/ep-${body.episode}.mp4`, format: 'file', proxied: false, subtitles: [] } });
    }
    if (path === '/api/auth/status') return route.fulfill({ json: { username: 'tester', is_admin: true, privileges: {} } });
    if (['/api/models', '/api/model-endpoints', '/api/sessions'].includes(path)) return route.fulfill({ json: [] });
    return route.fulfill({ json: {} });
  });
  await page.goto('/static/index.html');
  await page.evaluate(async () => (await import('/static/js/settings.js')).open());
  await page.locator('#entertainment-section').click();
  await page.locator('#entertainment-query').fill('Naruto');
  await page.locator('#entertainment-search button[type="submit"]').click();
  await page.locator('.ent-card-play').first().click();
  await page.locator('.entertainment-result', { hasText: 'Episode 1' }).click();

  // Episode 1 is playing on the primary element...
  await expect(page.locator('#entertainment-video')).toHaveAttribute('src', 'https://media.example/ep-1.mp4');
  // ...while episode 2 is warmed into the idle element before it is needed.
  await expect(page.locator('#entertainment-video-next')).toHaveAttribute('src', 'https://media.example/ep-2.mp4');
});

test('Entertainment pill plays the fanfare and the top nav is stripped down', async ({ page }) => {
  await page.addInitScript(() => {
    window.__entPlayed = [];
    HTMLMediaElement.prototype.play = function () { window.__entPlayed.push(this.id || 'media'); return Promise.resolve(); };
    HTMLMediaElement.prototype.pause = function () {};
    HTMLMediaElement.prototype.load = function () {};
  });
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/entertainment') return route.fulfill({ json: { providers: [
      { id: 'ani-cli', label: 'Anime', enabled: true },
      { id: 'pandaflix', label: 'Movies & Shows', enabled: true },
    ] } });
    if (path === '/api/prefs/entertainment') return route.fulfill({ json: { key: 'entertainment', value: null } });
    if (path === '/api/auth/status') return route.fulfill({ json: { username: 'tester', is_admin: true, privileges: {} } });
    if (['/api/models', '/api/model-endpoints', '/api/sessions'].includes(path)) return route.fulfill({ json: [] });
    return route.fulfill({ json: {} });
  });
  await page.goto('/static/index.html');
  await page.evaluate(async () => (await import('/static/js/settings.js')).open());
  await page.locator('#entertainment-section').click();

  // The landing nav is stripped: only the ENTERTAINMENT pill and close remain.
  await expect(page.locator('.ent-provider-tabs').first()).toBeHidden();
  await expect(page.locator('.ent-nav-link').first()).toBeHidden();
  await expect(page.locator('.ent-search-field').first()).toBeHidden();
  await expect(page.locator('.ent-avatar').first()).toBeHidden();

  // The ENTERTAINMENT pill plays the fanfare.
  await expect(page.locator('#entertainment-fanfare')).toHaveCount(1);
  await page.locator('#entertainment-fanfare-btn').click();
  await expect.poll(() => page.evaluate(() => window.__entPlayed.includes('entertainment-fanfare'))).toBe(true);

  // Open AniCLI plays its own sting; Open Pandaflix plays the Hollywood sting.
  await page.evaluate(() => { window.__entPlayed.length = 0; });
  await page.locator('.entertainment-choice[data-provider="ani-cli"]').click();
  await expect.poll(() => page.evaluate(() => window.__entPlayed.includes('entertainment-anime-wow'))).toBe(true);
  await page.locator('#entertainment-home').click();
  await page.evaluate(() => { window.__entPlayed.length = 0; });
  await page.locator('.entertainment-choice[data-provider="pandaflix"]').click();
  await expect.poll(() => page.evaluate(() => window.__entPlayed.includes('entertainment-welcome-hollywood'))).toBe(true);
});

test('Entertainment sidebar destinations open their pages', async ({ page }) => {
  await page.addInitScript(() => { HTMLMediaElement.prototype.play = async function () {}; });
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/entertainment') return route.fulfill({ json: { providers: [{ id: 'ani-cli', label: 'Anime', enabled: true }] } });
    if (path === '/api/prefs/entertainment') return route.fulfill({ json: { key: 'entertainment', value: {
      history: [{ provider: 'ani-cli', query: 'Naruto', item: { id: 1, title: 'Naruto' }, episode: '429', title: 'Naruto: Shippuden', position: 132, duration: 600 }],
      favorites: [{ key: 'ani-cli:1', provider: 'ani-cli', query: 'Naruto', item: { id: 1, title: 'Naruto' }, title: 'Naruto: Shippuden' }],
    } } });
    if (path === '/api/entertainment/ani-cli/resolve') return route.fulfill({ json: {
      title: 'Naruto: Shippuden', url: 'https://media.example/naruto.mp4', format: 'file', proxied: false, subtitles: [],
    } });
    if (path === '/api/auth/status') return route.fulfill({ json: { username: 'tester', is_admin: true, privileges: {} } });
    if (['/api/models', '/api/model-endpoints', '/api/sessions'].includes(path)) return route.fulfill({ json: [] });
    return route.fulfill({ json: {} });
  });
  await page.goto('/static/index.html');
  await page.evaluate(async () => (await import('/static/js/settings.js')).open());
  await page.locator('#entertainment-section').click();

  await page.locator('.ent-sidebar [data-ent-dest="recent"]').click();
  await expect(page.locator('#ent-collection-title')).toHaveText('Recently Watched');
  await expect(page.locator('#ent-collection-results .ent-collection-item')).toContainText('Naruto: Shippuden');
  await expect(page.locator('#ent-collection-results .ent-collection-item')).toContainText('7:48 left');

  // Clicking a recently watched title resumes it without a new search.
  await page.locator('#ent-collection-results .ent-collection-item').click();
  await expect(page.locator('#entertainment-player')).toBeVisible();
  await expect(page.locator('#entertainment-now-playing')).toHaveText('Naruto: Shippuden');
  await page.locator('#entertainment-back').click();

  await page.locator('.ent-sidebar [data-ent-dest="favorites"]').click();
  await expect(page.locator('#ent-collection-title')).toHaveText('My Favorites');
  await expect(page.locator('#ent-collection-results .ent-collection-item')).toContainText('Naruto: Shippuden');

  await page.locator('.ent-sidebar [data-ent-dest="watchlist"]').click();
  await expect(page.locator('#ent-collection-title')).toHaveText('Watchlist');

  await page.locator('.ent-sidebar [data-ent-dest="downloads"]').click();
  await expect(page.locator('#ent-collection-title')).toHaveText('Downloads');

  await page.locator('.ent-sidebar [data-ent-dest="popular"]').click();
  await expect(page.locator('#ent-collection-title')).toHaveText('Popular');
});

test('Entertainment favorites open from the library and watchlist stays separate', async ({ page }) => {
  await page.addInitScript(() => {
    HTMLMediaElement.prototype.play = async function () {};
    HTMLMediaElement.prototype.pause = function () {};
    HTMLMediaElement.prototype.load = function () {};
  });
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/entertainment') return route.fulfill({ json: { providers: [{ id: 'ani-cli', label: 'Anime', enabled: true }] } });
    if (path === '/api/prefs/entertainment') return route.fulfill({ json: { key: 'entertainment', value: null } });
    if (path === '/api/entertainment/ani-cli/search') return route.fulfill({ json: { items: [{ id: 1, title: 'Naruto' }, { id: 2, title: 'Bleach' }] } });
    if (path === '/api/entertainment/ani-cli/episodes') return route.fulfill({ json: { items: [{ number: '1', label: 'Episode 1' }], total: 1, next_offset: -1 } });
    if (path === '/api/entertainment/metadata') return route.fulfill({ json: { covers: { Naruto: '/api/entertainment/image/naruto' } } });
    if (path === '/api/auth/status') return route.fulfill({ json: { username: 'tester', is_admin: true, privileges: {} } });
    if (['/api/models', '/api/model-endpoints', '/api/sessions'].includes(path)) return route.fulfill({ json: [] });
    return route.fulfill({ json: {} });
  });
  await page.goto('/static/index.html');
  await page.evaluate(async () => (await import('/static/js/settings.js')).open());
  await page.locator('#entertainment-section').click();
  await page.locator('#entertainment-query').fill('Naruto');
  await page.locator('#entertainment-search button[type="submit"]').click();

  await page.locator('.ent-card').nth(0).locator('.ent-card-fav').click();
  await expect(page.locator('.ent-card').nth(0).locator('.ent-card-fav')).toHaveAttribute('aria-pressed', 'true');
  await page.locator('.ent-card').nth(1).locator('.ent-card-watch').click();
  await expect(page.locator('.ent-card').nth(1).locator('.ent-card-watch')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.ent-card').nth(0).locator('.ent-card-cover')).toHaveCount(1);

  await page.locator('.ent-sidebar [data-ent-dest="favorites"]').click();
  await expect(page.locator('#ent-collection-results .ent-collection-item')).toHaveCount(1);
  await expect(page.locator('#ent-collection-results .ent-collection-item')).toContainText('Naruto');
  await page.locator('.ent-sidebar [data-ent-dest="watchlist"]').click();
  await expect(page.locator('#ent-collection-results .ent-collection-item')).toHaveCount(1);
  await expect(page.locator('#ent-collection-results .ent-collection-item')).toContainText('Bleach');

  await page.locator('.ent-sidebar [data-ent-dest="favorites"]').click();
  await page.locator('#ent-collection-results .ent-collection-item').click();
  await expect(page.locator('#entertainment-collection')).toBeHidden();
  await expect(page.locator('#entertainment-browser')).toBeVisible();
  await expect(page.locator('.entertainment-result')).toContainText('Episode 1');
});

test('Entertainment refresh shows visible progress while re-resolving', async ({ page }) => {
  await page.addInitScript(() => {
    HTMLMediaElement.prototype.play = async function () {};
    HTMLMediaElement.prototype.pause = function () {};
    HTMLMediaElement.prototype.load = function () {};
  });
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/entertainment') return route.fulfill({ json: { providers: [{ id: 'ani-cli', label: 'Anime', enabled: true }] } });
    if (path === '/api/prefs/entertainment') return route.fulfill({ json: { key: 'entertainment', value: null } });
    if (path === '/api/entertainment/ani-cli/search') return route.fulfill({ json: { items: [{ id: 1, title: 'Naruto' }] } });
    if (path === '/api/entertainment/ani-cli/episodes') return route.fulfill({ json: { items: [{ number: '1', label: 'Episode 1' }], total: 1, next_offset: -1 } });
    if (path === '/api/entertainment/ani-cli/resolve') {
      await new Promise(resolve => setTimeout(resolve, 400));
      return route.fulfill({ json: { title: 'Naruto Episode 1', url: 'https://media.example/ep1.mp4', format: 'file', proxied: false, subtitles: [] } });
    }
    if (path === '/api/auth/status') return route.fulfill({ json: { username: 'tester', is_admin: true, privileges: {} } });
    if (['/api/models', '/api/model-endpoints', '/api/sessions'].includes(path)) return route.fulfill({ json: [] });
    return route.fulfill({ json: {} });
  });
  await page.goto('/static/index.html');
  await page.evaluate(async () => (await import('/static/js/settings.js')).open());
  await page.locator('#entertainment-section').click();
  await page.locator('#entertainment-query').fill('Naruto');
  await page.locator('#entertainment-search button[type="submit"]').click();
  await page.locator('.ent-card-play').first().click();
  await page.locator('.entertainment-result', { hasText: 'Episode 1' }).click();
  await expect(page.locator('#entertainment-player')).toBeVisible();

  await page.locator('#entertainment-refresh').click();
  await expect(page.locator('#entertainment-player-status')).toHaveText('Refreshing stream…');
  await expect(page.locator('#entertainment-refresh')).toBeDisabled();
  await expect(page.locator('#entertainment-refresh')).toHaveText('⟳ Refresh');
  await expect(page.locator('#entertainment-player-status')).toBeEmpty();
});

test('Entertainment plays the anime sting when an episode starts', async ({ page }) => {
  await page.addInitScript(() => {
    window.__entPlayed = [];
    HTMLMediaElement.prototype.play = function () { window.__entPlayed.push(this.id || 'media'); return Promise.resolve(); };
    HTMLMediaElement.prototype.pause = function () {};
    HTMLMediaElement.prototype.load = function () {};
  });
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/entertainment') return route.fulfill({ json: { providers: [{ id: 'ani-cli', label: 'Anime', enabled: true }] } });
    if (path === '/api/prefs/entertainment') return route.fulfill({ json: { key: 'entertainment', value: null } });
    if (path === '/api/entertainment/ani-cli/search') return route.fulfill({ json: { items: [{ id: 1, title: 'Naruto' }] } });
    if (path === '/api/entertainment/ani-cli/episodes') return route.fulfill({ json: { items: [{ number: '1', label: 'Episode 1' }], total: 1, next_offset: -1 } });
    if (path === '/api/entertainment/ani-cli/resolve') return route.fulfill({ json: { title: 'Naruto Episode 1', url: 'https://media.example/ep1.mp4', format: 'file', proxied: false, subtitles: [] } });
    if (path === '/api/auth/status') return route.fulfill({ json: { username: 'tester', is_admin: true, privileges: {} } });
    if (['/api/models', '/api/model-endpoints', '/api/sessions'].includes(path)) return route.fulfill({ json: [] });
    return route.fulfill({ json: {} });
  });
  await page.goto('/static/index.html');
  await page.evaluate(async () => (await import('/static/js/settings.js')).open());
  await page.locator('#entertainment-section').click();
  await page.locator('#entertainment-query').fill('Naruto');
  await page.locator('#entertainment-search button[type="submit"]').click();
  await page.locator('.ent-card-play').first().click();
  await page.locator('.entertainment-result', { hasText: 'Episode 1' }).click();
  await expect.poll(() => page.evaluate(() => window.__entPlayed.includes('entertainment-anime-play'))).toBe(true);
});

test('Entertainment back button walks results -> seasons -> episodes', async ({ page }) => {
  await page.addInitScript(() => {
    HTMLMediaElement.prototype.play = async function () {};
    HTMLMediaElement.prototype.pause = function () {};
    HTMLMediaElement.prototype.load = function () {};
  });
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/entertainment') return route.fulfill({ json: { providers: [{ id: 'pandaflix', label: 'Movies & Shows', enabled: true }] } });
    if (path === '/api/prefs/entertainment') return route.fulfill({ json: { key: 'entertainment', value: null } });
    if (path === '/api/entertainment/pandaflix/search') return route.fulfill({ json: { items: [
      { selection: '[series] The Blacklist (2013)', kind: 'series', title: 'The Blacklist (2013)' },
    ] } });
    if (path === '/api/entertainment/pandaflix/seasons') return route.fulfill({ json: { items: [
      { number: 1, label: 'Season 1' }, { number: 2, label: 'Season 2' },
    ] } });
    if (path === '/api/entertainment/pandaflix/episodes') return route.fulfill({ json: {
      items: [{ number: 1, label: 'E01 - Pilot' }, { number: 2, label: 'E02 - The Freelancer' }], total: 2, next_offset: -1,
    } });
    if (path === '/api/auth/status') return route.fulfill({ json: { username: 'tester', is_admin: true, privileges: {} } });
    if (['/api/models', '/api/model-endpoints', '/api/sessions'].includes(path)) return route.fulfill({ json: [] });
    return route.fulfill({ json: {} });
  });
  await page.goto('/static/index.html');
  await page.evaluate(async () => (await import('/static/js/settings.js')).open());
  await page.locator('#entertainment-section').click();
  await page.locator('#entertainment-query').fill('The Blacklist');
  await page.locator('#entertainment-search button[type="submit"]').click();

  // Root results view: nothing to go back to.
  await expect(page.locator('#entertainment-results-title')).toContainText('Results for');
  await expect(page.locator('#entertainment-results-back')).toBeHidden();

  // Open the series -> seasons, and the back control appears.
  await page.locator('.ent-card-play').click();
  await expect(page.locator('.entertainment-result', { hasText: 'Season 1' })).toBeVisible();
  await expect(page.locator('#entertainment-results-back')).toBeVisible();

  // Open a season -> episodes.
  await page.locator('.entertainment-result', { hasText: 'Season 1' }).click();
  await expect(page.locator('.entertainment-result', { hasText: 'E01 - Pilot' })).toBeVisible();

  // Back -> seasons.
  await page.locator('#entertainment-results-back').click();
  await expect(page.locator('.entertainment-result', { hasText: 'Season 1' })).toBeVisible();
  await expect(page.locator('.entertainment-result', { hasText: 'E01 - Pilot' })).toHaveCount(0);
  await expect(page.locator('#entertainment-results-back')).toBeVisible();

  // Back -> results, and the control hides again.
  await page.locator('#entertainment-results-back').click();
  await expect(page.locator('#entertainment-results-title')).toContainText('Results for');
  await expect(page.locator('#entertainment-results-back')).toBeHidden();
});

test('Continue Watching holds in-progress titles, queues the next episode, and supports removal', async ({ page }) => {
  await page.addInitScript(() => {
    HTMLMediaElement.prototype.play = async function () {};
    HTMLMediaElement.prototype.pause = function () {};
    HTMLMediaElement.prototype.load = function () {};
  });
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/entertainment') return route.fulfill({ json: { providers: [{ id: 'ani-cli', label: 'Anime', enabled: true }] } });
    if (path === '/api/prefs/entertainment') return route.fulfill({ json: { key: 'entertainment', value: null } });
    if (path === '/api/entertainment/ani-cli/search') return route.fulfill({ json: { items: [{ id: 1, title: 'Naruto' }] } });
    if (path === '/api/entertainment/ani-cli/episodes') return route.fulfill({ json: { items: [
      { number: '1', label: 'Episode 1' }, { number: '2', label: 'Episode 2' }, { number: '3', label: 'Episode 3' },
    ], total: 3, next_offset: -1 } });
    if (path === '/api/entertainment/ani-cli/resolve') {
      const body = JSON.parse(route.request().postData() || '{}');
      return route.fulfill({ json: { title: `Naruto Episode ${body.episode}`, url: 'https://media.example/ep.mp4', format: 'file', proxied: false, subtitles: [] } });
    }
    if (path === '/api/auth/status') return route.fulfill({ json: { username: 'tester', is_admin: true, privileges: {} } });
    if (['/api/models', '/api/model-endpoints', '/api/sessions'].includes(path)) return route.fulfill({ json: [] });
    return route.fulfill({ json: {} });
  });
  await page.goto('/static/index.html');
  await page.evaluate(async () => (await import('/static/js/settings.js')).open());
  await page.locator('#entertainment-section').click();
  await page.locator('#entertainment-query').fill('Naruto');
  await page.locator('#entertainment-search button[type="submit"]').click();
  await page.locator('.ent-card-play').first().click();
  await page.locator('.entertainment-result', { hasText: 'Episode 1' }).click();
  await expect(page.locator('#entertainment-player')).toBeVisible();

  // Report partial progress, then open the library: the title is in progress.
  await page.evaluate(() => {
    const video = document.getElementById('entertainment-video');
    Object.defineProperty(video, 'duration', { configurable: true, get: () => 1000 });
    Object.defineProperty(video, 'currentTime', { configurable: true, get: () => 200, set: () => {} });
    video.dispatchEvent(new Event('timeupdate'));
  });
  await page.locator('#entertainment-back').click();
  await page.locator('.ent-sidebar [data-ent-dest="continue"]').click();
  await expect(page.locator('.ent-continue-main')).toContainText('Naruto Episode 1');

  // Finish the episode with autoplay off: the finished one drops out and the
  // next episode is queued as "Up next".
  await page.locator('.ent-continue-main').click();
  await expect(page.locator('#entertainment-player')).toBeVisible();
  await page.evaluate(() => document.querySelector('.ent-video.is-active').dispatchEvent(new Event('ended')));
  await page.locator('#entertainment-back').click();
  await page.locator('.ent-sidebar [data-ent-dest="continue"]').click();
  await expect(page.locator('.ent-continue-item')).toHaveCount(1);
  await expect(page.locator('.ent-continue-main')).toContainText('Episode 2');
  await expect(page.locator('.ent-continue-main')).toContainText('Up next');

  // The user can remove an entry from Continue Watching.
  await page.locator('.ent-continue-remove').click();
  await expect(page.locator('.ent-continue-item')).toHaveCount(0);
  await expect(page.locator('#ent-collection-results .entertainment-empty')).toBeVisible();
});

test('Continue Watching collapses a show to its latest episode and recovers a broken cover', async ({ page }) => {
  const seeded = {
    provider: 'ani-cli', language: 'sub', quality: 'best', autoplay: false,
    favorites: [], watchlist: [], resume: null,
    history: [
      { provider: 'ani-cli', query: 'naruto', item: { id: 1, title: 'Naruto: Shippuden', cover: '/api/entertainment/image/stale10' }, episode: '10', title: 'Naruto: Shippuden Episode 10', position: 100, duration: 1400, at: 1000, key: 'ani-cli:1:10' },
      { provider: 'ani-cli', query: 'naruto', item: { id: 1, title: 'Naruto: Shippuden', cover: '/api/entertainment/image/stale11' }, episode: '11', title: 'Naruto: Shippuden Episode 11', position: 120, duration: 1400, at: 2000, key: 'ani-cli:1:11' },
      { provider: 'ani-cli', query: 'naruto', item: { id: 1, title: 'Naruto: Shippuden', cover: '/api/entertainment/image/stale12' }, episode: '12', title: 'Naruto: Shippuden Episode 12', position: 130, duration: 1400, at: 3000, key: 'ani-cli:1:12' },
      { provider: 'ani-cli', query: 'the last', item: { id: 3, title: 'The Last: Naruto the Movie', cover: '/api/entertainment/image/stale-movie' }, episode: '1', title: 'The Last: Naruto the Movie Episode 1', position: 50, duration: 5000, at: 4000, key: 'ani-cli:3:1' },
    ],
  };
  await page.addInitScript(() => { HTMLMediaElement.prototype.play = async function () {}; });
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/entertainment') return route.fulfill({ json: { providers: [{ id: 'ani-cli', label: 'Anime', enabled: true }] } });
    if (path === '/api/prefs/entertainment') return route.fulfill({ json: { key: 'entertainment', value: seeded } });
    if (path === '/api/entertainment/metadata') return route.fulfill({ json: { covers: {} } });
    if (path === '/api/auth/status') return route.fulfill({ json: { username: 'tester', is_admin: true, privileges: {} } });
    if (['/api/models', '/api/model-endpoints', '/api/sessions'].includes(path)) return route.fulfill({ json: [] });
    return route.fulfill({ json: {} });
  });
  await page.goto('/static/index.html');
  await page.evaluate(async () => (await import('/static/js/settings.js')).open());
  await page.locator('#entertainment-section').click();
  await page.locator('.ent-sidebar [data-ent-dest="continue"]').click();

  // One row per show, not one per nexted episode.
  await expect(page.locator('.ent-continue-item')).toHaveCount(2);
  await expect(page.locator('.ent-continue-item', { hasText: 'Episode 12' })).toHaveCount(1);
  await expect(page.locator('.ent-continue-item', { hasText: 'Episode 11' })).toHaveCount(0);

  // A cover token that no longer resolves degrades to the initials tile.
  await expect(page.locator('.ent-collection-thumb--empty').first()).toBeVisible();
});

test('Continue Watching uses the furthest episode reached, not recency', async ({ page }) => {
  // Finishing the last episode must drop the show even when earlier episodes
  // still have unfinished progress; a queued next episode keeps it.
  const seeded = {
    provider: 'ani-cli', language: 'sub', quality: 'best', autoplay: false,
    favorites: [], watchlist: [], resume: null,
    history: [
      { provider: 'ani-cli', query: 'naruto', item: { id: 1, title: 'Naruto: Shippuden' }, episode: '10', title: 'Naruto: Shippuden Episode 10', position: 100, duration: 1400, at: 1000, key: 'ani-cli:1:10' },
      { provider: 'ani-cli', query: 'naruto', item: { id: 1, title: 'Naruto: Shippuden' }, episode: '12', title: 'Naruto: Shippuden Episode 12', position: 1390, duration: 1400, at: 2000, key: 'ani-cli:1:12' },
      { provider: 'ani-cli', query: 'naruto', item: { id: 1, title: 'Naruto: Shippuden' }, episode: '13', title: 'Naruto: Shippuden Episode 13', position: 0, duration: 0, upNext: true, at: 3000, key: 'ani-cli:1:13' },
      { provider: 'ani-cli', query: 'bleach', item: { id: 2, title: 'Bleach' }, episode: '5', title: 'Bleach Episode 5', position: 100, duration: 1400, at: 1500, key: 'ani-cli:2:5' },
      { provider: 'ani-cli', query: 'bleach', item: { id: 2, title: 'Bleach' }, episode: '6', title: 'Bleach Episode 6', position: 1400, duration: 1400, at: 1600, key: 'ani-cli:2:6' },
    ],
  };
  await page.addInitScript(() => { HTMLMediaElement.prototype.play = async function () {}; });
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/entertainment') return route.fulfill({ json: { providers: [{ id: 'ani-cli', label: 'Anime', enabled: true }] } });
    if (path === '/api/prefs/entertainment') return route.fulfill({ json: { key: 'entertainment', value: seeded } });
    if (path === '/api/entertainment/metadata') return route.fulfill({ json: { covers: {} } });
    if (path === '/api/auth/status') return route.fulfill({ json: { username: 'tester', is_admin: true, privileges: {} } });
    if (['/api/models', '/api/model-endpoints', '/api/sessions'].includes(path)) return route.fulfill({ json: [] });
    return route.fulfill({ json: {} });
  });
  await page.goto('/static/index.html');
  await page.evaluate(async () => (await import('/static/js/settings.js')).open());
  await page.locator('#entertainment-section').click();
  await page.locator('.ent-sidebar [data-ent-dest="continue"]').click();

  // Bleach is done (furthest episode finished) so it drops out entirely;
  // Naruto keeps its queued Episode 13.
  await expect(page.locator('.ent-continue-item')).toHaveCount(1);
  await expect(page.locator('.ent-continue-main')).toContainText('Episode 13');
  await expect(page.locator('.ent-continue-main')).toContainText('Up next');
  await expect(page.locator('.ent-continue-item', { hasText: 'Bleach' })).toHaveCount(0);
});
