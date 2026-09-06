import { expect, test } from '@playwright/test';


test('Gallery reports, changes, disables, and protects a connected Pictures folder', async ({ page }) => {
  let source = {
    id: 'source-1',
    path: '/home/tester/Pictures',
    label: 'Pictures',
    kind: 'native',
    enabled: true,
    auto_connected: true,
    indexed: 1,
    last_scan_at: '2026-09-06T13:30:00',
    error: null,
  };
  const state = () => ({
    environment: 'native',
    message: "Pandamonium uses the operating system's conventional Pictures folder.",
    candidates: [],
    sources: [source],
    results: {},
  });

  await page.route('**/api/**', route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === '/api/auth/status') {
      return route.fulfill({ json: { username: 'tester', is_admin: true, privileges: {} } });
    }
    if (path === '/api/gallery/sources' && request.method() === 'GET') {
      return route.fulfill({ json: state() });
    }
    if (path === '/api/gallery/sources/sync') {
      return route.fulfill({ json: state() });
    }
    if (path === '/api/gallery/sources/source-1' && request.method() === 'PATCH') {
      const change = request.postDataJSON();
      source = { ...source, ...change, label: change.path ? 'Photos' : source.label };
      return route.fulfill({ json: state() });
    }
    if (path === '/api/gallery/library') {
      return route.fulfill({ json: {
        items: [{
          id: 'img-1', filename: 'source-fixture.jpg', url: '/api/gallery/source/img-1/source-fixture.jpg',
          prompt: 'fixture', caption: '', model: 'local-folder', read_only: true,
          favorite: false, tags: '', ai_tags: '', created_at: '2026-09-06T13:30:00',
        }],
        total: 1, total_tagged: 0, tags: [], models: ['local-folder'],
      } });
    }
    if (path === '/api/gallery/albums') return route.fulfill({ json: { albums: [] } });
    if (path === '/api/gallery/stats') return route.fulfill({ json: {} });
    if (path === '/api/models' || path === '/api/model-endpoints' || path === '/api/sessions') {
      return route.fulfill({ json: [] });
    }
    return route.fulfill({ json: {} });
  });

  await page.goto('/static/index.html');
  await page.evaluate(async () => (await import('/static/js/gallery.js')).openGallery());
  const gallery = page.locator('#gallery-modal');
  await expect(gallery).toBeVisible();
  await gallery.locator('.gallery-tab[data-tab="settings"]').click();
  await expect(gallery.locator('#gallery-source-message')).toContainText('conventional Pictures');
  await expect(gallery.locator('#gallery-source-list')).toContainText('/home/tester/Pictures');
  await expect(gallery.getByRole('button', { name: 'Change' })).toBeVisible();
  await expect(gallery.getByRole('button', { name: 'Disable' })).toBeVisible();

  await gallery.getByRole('button', { name: 'Change' }).click();
  await page.locator('#styled-prompt-input').fill('/home/tester/Photos');
  await page.locator('#styled-prompt-ok').click();
  await expect(gallery.locator('#gallery-source-list')).toContainText('/home/tester/Photos');

  await gallery.getByRole('button', { name: 'Disable' }).click();
  await expect(gallery.locator('#gallery-source-list')).toContainText('disabled');
  await expect(gallery.getByRole('button', { name: 'Enable' })).toBeVisible();

  await gallery.locator('.gallery-tab[data-tab="images"]').click();
  await gallery.locator('.gallery-card[data-id="img-1"]').click();
  await expect(gallery.locator('#gallery-edit-direct-btn')).toBeDisabled();
  await expect(gallery.locator('#gallery-rotate-btn')).toBeDisabled();
  await gallery.locator('#gallery-detail-menu-btn').click();
  await expect(gallery.locator('#gallery-delete-btn')).toBeDisabled();
});
