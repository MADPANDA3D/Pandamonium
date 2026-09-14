import { expect, test } from '@playwright/test';

const installed = {
  plugins: [
    { id: 'oracle', name: 'ORACLE', version: '', state: 'configured', runtime: 'web', descriptor: 'live_catalog', origin: 'configured', capability_count: 0 },
    { id: 'atlas', name: 'Atlas Lab', version: '2.0.0', state: 'enabled', runtime: 'openapi', descriptor: 'openapi', origin: 'registry', capability_count: 1 },
  ],
};

const oracleDetail = {
  id: 'oracle', name: 'ORACLE', version: '', state: 'configured', runtime: 'web',
  descriptor: 'live_catalog', origin: 'configured', source_revision: '',
  permissions: { default: 'read_only', capabilities: {} },
  data_boundaries: { read: [], write: [], network: [] },
  capabilities: [], configuration: [],
  notes: ['Configured surface, not installed through the plugin registry. Its live capability catalog is resolved when the session engages it.'],
};

const atlasDetail = {
  id: 'atlas', name: 'Atlas Lab', version: '2.0.0', state: 'enabled', runtime: 'openapi',
  descriptor: 'openapi', origin: 'registry', source_revision: '1'.repeat(40),
  permissions: { default: 'read_only', capabilities: { create_mesh: 'bounded_write' } },
  data_boundaries: { read: ['assets'], write: ['outputs'], network: [] },
  capabilities: [
    { name: 'create_mesh', kind: 'tool', permission_mode: 'bounded_write', descriptor: 'openapi', description: 'Create a mesh' },
  ],
  configuration: [
    { key: 'ATLAS_API_TOKEN', description: 'Owner-supplied API token', required: true, secret: true },
  ],
  notes: [],
};

async function mockApp(page) {
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/extensions/installed') return route.fulfill({ json: installed });
    if (path === '/api/extensions/installed/oracle') return route.fulfill({ json: oracleDetail });
    if (path === '/api/extensions/installed/atlas') return route.fulfill({ json: atlasDetail });
    if (path === '/api/extensions/marketplace') {
      return route.fulfill({ json: { schema_version: 'pandamonium.marketplace-view.v1', status: 'offline', failure: 'marketplace_catalog_offline', plugins: [] } });
    }
    if (path === '/api/extensions/catalog') return route.fulfill({ json: { plugins: [] } });
    if (path === '/api/auth/status') return route.fulfill({ json: { username: 'tester', is_admin: true, privileges: {} } });
    if (path === '/api/models' || path === '/api/model-endpoints' || path === '/api/sessions') return route.fulfill({ json: [] });
    return route.fulfill({ json: {} });
  });
}

test('installed plugins stay visible and detailed when the marketplace is offline', async ({ page }) => {
  await mockApp(page);
  await page.goto('/static/index.html');
  await page.getByRole('button', { name: 'Browse plugins' }).click();

  await expect(page.locator('#marketplace-panel-installed')).toBeVisible();
  await expect(page.locator('#marketplace-installed-list')).toContainText('ORACLE');
  await expect(page.locator('#marketplace-installed-list')).toContainText('Atlas Lab');

  await page.getByRole('tab', { name: 'Add a new plugin' }).click();
  await expect(page.locator('#marketplace-results')).toContainText('Marketplace offline');
  await page.getByRole('tab', { name: 'Installed plugins' }).click();

  await page.locator('#marketplace-installed-list button', { hasText: 'ORACLE' }).click();
  await expect(page.locator('#marketplace-installed-view')).toBeHidden();
  await expect(page.locator('#marketplace-installed-detail')).toBeVisible();
  await expect(page.locator('#marketplace-installed-detail-content')).toContainText('Configured surface');
  await expect(page.locator('#marketplace-installed-detail-content')).toContainText('live_catalog');

  await page.getByRole('button', { name: '← Back to installed' }).click();
  await expect(page.locator('#marketplace-installed-view')).toBeVisible();
  await expect(page.locator('#marketplace-installed-detail')).toBeHidden();

  await page.locator('#marketplace-installed-list button', { hasText: 'Atlas Lab' }).click();
  await expect(page.locator('#marketplace-installed-detail-content')).toContainText('create_mesh');
  await expect(page.locator('#marketplace-installed-detail-content')).toContainText('Create a mesh');
  await expect(page.locator('#marketplace-installed-detail-content')).toContainText('ATLAS_API_TOKEN');
  await expect(page.locator('#marketplace-installed-detail-content')).toContainText('secret');
});
