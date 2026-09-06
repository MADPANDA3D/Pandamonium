import { expect, test } from '@playwright/test';


test('Gallery manages Immich safely and keeps remote assets read-only', async ({ page }) => {
  const connectionId = '0123456789abcdef0123456789abcdef';
  const assetId = `immich:${connectionId}:asset-1`;
  const albumId = `immich:${connectionId}:album:album-1`;
  let connection = {
    configured: false,
    enabled: false,
    status: 'unconfigured',
    api_key_configured: false,
    cached_files: 0,
  };
  let savedKey = '';
  let importCalls = 0;
  const responseBodies = [];

  await page.route('**/api/**', route => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const fulfill = (json, status = 200) => {
      responseBodies.push(json);
      return route.fulfill({ status, json });
    };
    if (path === '/api/auth/status') {
      return fulfill({ username: 'tester', is_admin: true, privileges: {} });
    }
    if (path === '/api/gallery/immich/connection' && request.method() === 'GET') {
      return fulfill(connection);
    }
    if (path === '/api/gallery/discovery') {
      return fulfill({
        connected: connection.configured && connection.enabled ? 2 : 1,
        available: connection.configured ? 0 : 1,
        sources: [
          {
            id: 'folder:source-1', source_id: 'source-1', kind: 'device_folder',
            provider: 'Device folder', label: 'Pictures', device: 'pc-codex',
            location: '/home/tester/Pictures', state: 'connected', connected: true,
            connectable: true, enabled: true, indexed: 3,
          },
          {
            id: connection.configured ? 'immich:primary' : 'immich:found',
            kind: 'immich', provider: 'Immich', label: 'Immich', device: 'photo-server',
            location: connection.server_url || 'https://immich.test',
            server_url: connection.server_url || 'https://immich.test',
            state: connection.configured ? (connection.enabled ? 'connected' : 'disabled') : 'available',
            connected: connection.configured, connectable: true, enabled: connection.enabled,
            status: connection.status,
          },
        ],
        local: { environment: 'native', message: 'Pictures found on this device.' },
        tailnet: { available: true, devices_checked: 2, message: 'Found 1 Immich service.' },
      });
    }
    if (path === '/api/gallery/immich/connection' && request.method() === 'PUT') {
      const body = request.postDataJSON();
      if (body.api_key) savedKey = body.api_key;
      connection = {
        configured: true,
        enabled: body.enabled,
        server_url: body.server_url || connection.server_url,
        status: body.enabled ? 'untested' : 'disabled',
        api_key_configured: true,
        cached_files: 0,
      };
      return fulfill(connection);
    }
    if (path === '/api/gallery/immich/connection' && request.method() === 'DELETE') {
      connection = {
        configured: false, enabled: false, status: 'unconfigured',
        api_key_configured: false, cached_files: 0,
      };
      return fulfill({ ok: true, removed_cached_files: 2 });
    }
    if (path === '/api/gallery/immich/test') {
      connection = { ...connection, status: 'healthy' };
      return fulfill({ ok: true, status: 'healthy' });
    }
    if (path === '/api/gallery/immich/sync') {
      connection = { ...connection, status: 'healthy', cached_files: 2 };
      return fulfill({ ok: true, assets_cached: 1, albums_cached: 1 });
    }
    if (path === '/api/gallery/immich/cache') {
      connection = { ...connection, cached_files: 0 };
      return fulfill({ ok: true, removed_cached_files: 2 });
    }
    if (path.endsWith('/import')) {
      importCalls += 1;
      return fulfill({ ok: true, id: 'local-copy', source_type: 'immich' });
    }
    if (path.includes('/api/gallery/immich/assets/') && path.endsWith('/thumbnail')) {
      return route.fulfill({
        contentType: 'image/png',
        body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Z2S8AAAAASUVORK5CYII=', 'base64'),
      });
    }
    if (path === '/api/gallery/library') {
      if (url.searchParams.get('model') === 'Immich' || url.searchParams.get('album') === albumId) {
        return fulfill({
          items: [{
            id: assetId,
            filename: 'panda.jpg',
            url: `/api/gallery/immich/assets/${assetId}/thumbnail?size=preview`,
            download_url: `/api/gallery/immich/assets/${assetId}/download`,
            prompt: 'panda', caption: '', model: 'Immich', source_type: 'immich',
            read_only: true, remote: true, favorite: false, tags: '', ai_tags: '',
            created_at: '2026-09-06T13:30:00Z', thumbnail_ready: true,
          }],
          total: 1, total_tagged: 0, tags: [], models: ['Immich'],
          source_state: { status: 'healthy', stale: false },
        });
      }
      return fulfill({
        items: [], total: 0, total_tagged: 0, tags: [], models: ['Immich'],
      });
    }
    if (path === '/api/gallery/albums') {
      return fulfill({ albums: [{
        id: albumId, name: 'Pandas', count: 1, read_only: true,
        source_type: 'immich', cover_url: null,
      }] });
    }
    if (path === '/api/gallery/sources') {
      return fulfill({ message: 'No local source', sources: [], candidates: [] });
    }
    if (path === '/api/gallery/stats') return fulfill({});
    if (path === '/api/models' || path === '/api/model-endpoints' || path === '/api/sessions') {
      return fulfill([]);
    }
    return fulfill({});
  });

  await page.goto('/static/index.html');
  await page.evaluate(async () => (await import('/static/js/gallery.js')).openGallery());
  const gallery = page.locator('#gallery-modal');
  await expect(gallery).toBeVisible();
  await expect(gallery).toHaveClass(/modal-right-docked/);
  await expect(page.locator('body')).toHaveClass(/right-dock-active/);

  await gallery.locator('.gallery-tab[data-tab="settings"]').click();
  await expect(gallery.locator('[data-gallery-source-kind="device_folder"]')).toContainText('Pictures · connected');
  const discoveredImmich = gallery.locator('[data-gallery-source-kind="immich"]');
  await expect(discoveredImmich).toContainText('Immich · found');
  await discoveredImmich.getByRole('button', { name: 'Connect' }).click();
  const card = gallery.locator('#gallery-immich-card');
  await expect(card).toBeVisible();
  await expect(card).toContainText('Account Settings → API Keys');
  await expect(card.locator('.gallery-permission-list code')).toHaveText([
    'album.read', 'asset.read', 'asset.view', 'asset.download', 'asset.upload',
  ]);
  await expect(card).toContainText('Leave every other permission disabled');
  await expect(card.locator('#gallery-immich-url')).toHaveValue('https://immich.test');
  await card.locator('#gallery-immich-key').fill('browser-secret');
  await card.getByRole('button', { name: 'Save / rotate key' }).click();
  await expect(card.locator('#gallery-immich-key')).toHaveValue('');
  await expect(card.locator('#gallery-immich-key')).toHaveAttribute('placeholder', /API key saved/);
  expect(savedKey).toBe('browser-secret');
  expect(JSON.stringify(responseBodies)).not.toContain('browser-secret');

  await card.getByRole('button', { name: 'Test' }).click();
  await expect(card.locator('#gallery-immich-status')).toContainText('Connected');
  await card.getByRole('button', { name: 'Refresh cache' }).click();
  await expect(card.getByRole('button', { name: 'Disable' })).toBeVisible();

  await gallery.locator('.gallery-tab[data-tab="images"]').click();
  await gallery.locator('#gallery-model-filter').selectOption('Immich');
  const remoteCard = gallery.locator(`.gallery-card[data-id="${assetId}"]`);
  await expect(remoteCard).toBeVisible();
  await expect(remoteCard.locator('.gallery-fav-btn')).toHaveCount(0);
  await expect(remoteCard.locator('.gallery-select-dot')).toHaveCount(0);
  await remoteCard.click();
  await expect(gallery.locator('#gallery-edit-direct-btn')).toBeDisabled();
  await expect(gallery.locator('#gallery-detail-name-input')).toBeDisabled();
  await expect(gallery.locator('#gallery-detail-album')).toBeDisabled();
  await gallery.locator('#gallery-detail-menu-btn').click();
  await gallery.getByRole('button', { name: 'Import a local copy' }).click();
  await expect.poll(() => importCalls).toBe(1);

  await gallery.locator('.gallery-tab[data-tab="albums"]').click();
  const remoteAlbum = gallery.locator(`.gallery-album-card[data-album="${albumId}"]`);
  await expect(remoteAlbum).toBeVisible();
  await expect(remoteAlbum.locator('.gallery-album-menu-btn')).toHaveCount(0);

  await gallery.locator('.gallery-tab[data-tab="settings"]').click();
  await card.getByRole('button', { name: 'Disable' }).click();
  await expect(card.locator('#gallery-immich-status')).toContainText('Disabled');
  await card.getByRole('button', { name: 'Enable' }).click();
  await card.getByRole('button', { name: 'Remove' }).click();
  await page.locator('#styled-confirm-ok').click();
  await expect(card.locator('#gallery-immich-status')).toContainText('Not connected');
});
