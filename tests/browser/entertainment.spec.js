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
  await expect(page.locator('#entertainment-tabs button')).toHaveCount(0);
  await page.locator('#entertainment-close').click();

  installed = [
    { id: 'ani-cli', label: 'Anime', enabled: true },
    { id: 'pandaflix', label: 'Movies & Shows', enabled: true },
  ];
  await page.evaluate(async () => { window.dispatchEvent(new Event('pandamonium:extensions-changed')); (await import('/static/js/settings.js')).open(); });
  await page.locator('#entertainment-section').click();
  await expect(page.locator('#entertainment-landing')).toBeVisible();
  await expect(page.locator('.entertainment-choice')).toHaveCount(2);
  await expect(page.locator('#entertainment-tabs button')).toHaveCount(2);
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
  await page.locator('#entertainment-autoplay').check();
  await expect(page.locator('#entertainment-autoplay')).toBeChecked();

  // Continue watching and favorites appear back on the provider page.
  await page.locator('#entertainment-back').click();
  await expect(page.locator('#entertainment-resume')).toBeVisible();
  await expect(page.locator('#entertainment-resume')).toContainText('Naruto Episode 4');
  await page.locator('#entertainment-query').fill('Naruto');
  await page.locator('#entertainment-search button[type="submit"]').click();
  await page.locator('.ent-card-fav').first().click();
  await expect(page.locator('#entertainment-favorites .entertainment-favorite')).toHaveCount(1);
  await expect(page.locator('#entertainment-favorites .entertainment-favorite')).toContainText('Naruto');
});
