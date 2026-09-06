import { expect, test } from '@playwright/test';

const marketplace = {
  schema_version: 'pandamonium.marketplace-view.v1',
  status: 'ready',
  failure: null,
  catalog: {
    id: 'pandamonium-community', generated_at: '2026-09-06T07:00:00Z',
    expires_at: '2099-01-01T00:00:00Z', signature_state: 'verified',
  },
  runtime: { pandamonium_version: '1.0.10', platform: 'linux', architecture: 'amd64' },
  plugins: [
    {
      id: 'atlas', name: 'Atlas', version: '2.0.0', summary: 'Geometry workspace plugin.',
      categories: ['design', 'developer-tools'], license: 'Apache-2.0', availability: 'available',
      publisher: { id: 'example-labs', name: 'Example Labs', url: 'https://example.com/atlas', key_id: 'publisher-example-2026' },
      provenance: { source_url: 'https://github.com/example/atlas-lab.git', source_revision: '1'.repeat(40), sha256: 'a'.repeat(64), digest_state: 'verified', signature_state: 'verified' },
      compatibility: { state: 'compatible', pandamonium_min: '1.0.0', pandamonium_max: '1.9.99', platforms: ['linux', 'macos'], architectures: ['amd64', 'arm64'] },
      permissions: { default: 'read_only', capabilities: { create_mesh: 'bounded_write' }, data_boundaries: { read: ['assets'], write: ['outputs'], network: ['https://example.org'] } },
      dependencies: [{ dependency_type: 'plugin', id: 'base-tools', minimum_version: '1.2.0', maximum_version: '1.9.9', optional: true }],
      configuration: [{ key: 'ATLAS_API_TOKEN', description: 'Owner-supplied API token reference', required: false, secret: true }],
      restart_required: 'none', review: { status: 'active', reviewed_at: '2026-09-06T07:00:00Z', reviewer: 'pandamonium-security', security_advisories: [] },
      removal: { remove_paths: ['cache'], preserve_paths: ['data'] }, rollback: { strategy: 'pinned_revision', retain_revisions: 3, available_revisions: ['0'.repeat(40)] },
      installation: { state: 'update_available', current_version: '1.0.0', target_version: '2.0.0', enabled: true, update_available: true },
    },
    {
      id: 'robin', name: 'Robin', version: '1.0.0', summary: 'Reviewed OSINT plugin.',
      categories: ['research'], license: 'MIT', availability: 'revoked',
      publisher: { id: 'example-labs', name: 'Example Labs', url: 'https://example.com/robin', key_id: 'publisher-example-2026' },
      provenance: { source_url: 'https://github.com/example/robin.git', source_revision: '2'.repeat(40), sha256: 'b'.repeat(64), digest_state: 'verified', signature_state: 'verified' },
      compatibility: { state: 'incompatible', pandamonium_min: '2.0.0', pandamonium_max: '2.9.99', platforms: ['linux'], architectures: ['amd64'] },
      permissions: { default: 'read_only', capabilities: {}, data_boundaries: { read: [], write: [], network: [] } },
      dependencies: [], configuration: [], restart_required: 'pandamonium',
      removal: { remove_paths: [], preserve_paths: ['data'] }, rollback: { strategy: 'pinned_revision', retain_revisions: 2, available_revisions: [] },
      review: { status: 'revoked', reviewed_at: '2026-09-06T07:00:00Z', reviewer: 'pandamonium-security', security_advisories: [{ id: 'ADV-1', url: 'https://example.com/advisory', severity: 'high', summary: 'Fixture revocation.' }] },
      installation: { state: 'disabled', current_version: '1.0.0', target_version: '1.0.0', enabled: false, update_available: false },
    },
  ],
};

async function mockApp(page, response = marketplace) {
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/extensions/marketplace') return route.fulfill({ json: response });
    if (path === '/api/extensions/marketplace/plans') {
      const request = route.request().postDataJSON();
      return route.fulfill({ json: {
        plan_id: `plan-${request.operation}`, operation: request.operation, extension_id: request.extension_id,
        authority_decision: { decision: 'approval_required', decision_id: `decision-${request.operation}` },
        marketplace: { artifact: { sha256: 'a'.repeat(64), size_bytes: 27, digest_state: 'verified', signature_state: 'verified' }, current_version: '1.0.0', target_version: '2.0.0', dependencies: response.plugins[0].dependencies, configuration: response.plugins[0].configuration, restart_required: 'none' },
        removal: { ...response.plugins[0].removal, deleted_paths: [], retained_paths: ['cache', 'data'], package_recoverable: true },
      } });
    }
    if (path.startsWith('/api/authority/decisions/')) return route.fulfill({ json: { decision: 'allow' } });
    if (/\/api\/extensions\/plans\/[^/]+\/execute$/.test(path)) return route.fulfill({ json: { result: { status: 'succeeded' } } });
    if (path === '/api/extensions/catalog') return route.fulfill({ json: { plugins: [] } });
    if (path === '/api/auth/status') return route.fulfill({ json: { username: 'tester', is_admin: true, privileges: {} } });
    if (path === '/api/models' || path === '/api/model-endpoints' || path === '/api/sessions') return route.fulfill({ json: [] });
    return route.fulfill({ json: {} });
  });
}

test('Plugins → Add Plugins previews and executes the approved lifecycle', async ({ page }) => {
  await mockApp(page);
  await page.goto('/static/index.html');

  await page.locator('#add-plugins-btn').focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('#marketplace-modal')).toBeVisible();
  await expect(page.locator('#marketplace-search')).toBeFocused();
  await expect(page.getByRole('dialog', { name: 'Add Plugins' })).toContainText('Signed marketplace');
  await expect(page.getByRole('button', { name: /Atlas/ })).toContainText('Update available');
  await expect(page.getByRole('button', { name: /Robin/ })).toContainText('Revoked');

  const dialog = await page.locator('.marketplace-modal-content').boundingBox();
  expect(dialog.width).toBeGreaterThan(900);

  await page.getByRole('button', { name: /Atlas/ }).click();
  const detail = page.locator('#marketplace-detail');
  await expect(detail).toContainText('Example Labs');
  await expect(detail).toContainText('Apache-2.0');
  await expect(detail).toContainText('Catalog + artifact verified');
  await expect(detail).toContainText('read_only');
  await expect(detail).toContainText('base-tools');
  await expect(detail).toContainText('No restart');
  await expect(detail.getByRole('button', { name: 'Update' })).toBeVisible();
  await expect(detail.getByRole('button', { name: 'Disable' })).toBeVisible();
  await expect(detail.getByRole('button', { name: 'Rollback' })).toBeVisible();
  await expect(detail.getByRole('button', { name: 'Remove' })).toBeVisible();
  await detail.getByRole('button', { name: 'Update' }).click();
  await expect(detail).toContainText('Approval required: Update Atlas');
  await expect(detail).toContainText(`Verified sha256:${'a'.repeat(64)}`);
  await expect(detail).toContainText('1.0.0 → 2.0.0');
  await expect(detail).toContainText('1 declared configuration keys');
  await detail.getByRole('button', { name: 'Approve once' }).click();
  await expect(page.locator('#marketplace-summary')).toContainText('Atlas: Update completed.');
  const cards = await page.locator('.marketplace-card').evaluateAll(nodes => nodes.map(node => node.getBoundingClientRect().toJSON()));
  expect(cards[0].height).toBeGreaterThan(90);
  expect(cards[1].top).toBeGreaterThan(cards[0].bottom);

  await page.locator('#marketplace-search').fill('research');
  await expect(page.getByRole('button', { name: /Atlas/ })).toBeHidden();
  await expect(page.getByRole('button', { name: /Robin/ })).toBeVisible();

  await page.locator('#marketplace-search').fill('');
  await page.locator('#marketplace-category').selectOption('design');
  await expect(page.getByRole('button', { name: /Atlas/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /Robin/ })).toBeHidden();

  await page.keyboard.press('Escape');
  await expect(page.locator('#marketplace-modal')).toBeHidden();
  await expect(page.locator('#add-plugins-btn')).toBeFocused();
});

test('marketplace renders loading, offline, empty, and mobile detail navigation', async ({ page }) => {
  let resolveCatalog;
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/extensions/marketplace') {
      return new Promise(resolve => { resolveCatalog = () => resolve(route.fulfill({ json: marketplace })); });
    }
    if (path === '/api/extensions/catalog') return route.fulfill({ json: { plugins: [] } });
    if (path === '/api/auth/status') return route.fulfill({ json: { username: 'tester', is_admin: true, privileges: {} } });
    if (path === '/api/models' || path === '/api/model-endpoints' || path === '/api/sessions') return route.fulfill({ json: [] });
    return route.fulfill({ json: {} });
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/static/index.html');
  await page.getByRole('button', { name: 'Toggle sidebar' }).click();
  await page.locator('#add-plugins-btn').click();
  await expect(page.locator('#marketplace-results')).toContainText('Loading plugins');
  resolveCatalog();
  await expect(page.getByRole('button', { name: /Atlas/ })).toBeVisible();
  await page.getByRole('button', { name: /Atlas/ }).click();
  await expect(page.locator('#marketplace-detail')).toBeVisible();
  await expect(page.locator('#marketplace-results')).toBeHidden();
  await page.locator('#marketplace-back').click();
  await expect(page.locator('#marketplace-results')).toBeVisible();

  await page.unroute('**/api/**');
  await mockApp(page, { schema_version: 'pandamonium.marketplace-view.v1', status: 'offline', failure: 'marketplace_catalog_offline', plugins: [] });
  await page.locator('#marketplace-retry').click();
  await expect(page.locator('#marketplace-results')).toContainText('Marketplace offline');

  await page.unroute('**/api/**');
  await mockApp(page, { schema_version: 'pandamonium.marketplace-view.v1', status: 'error', failure: 'marketplace_catalog_unsigned', plugins: [] });
  await page.locator('#marketplace-retry').click();
  await expect(page.locator('#marketplace-results')).toContainText('Catalog verification failed');
  await expect(page.locator('#marketplace-results')).toContainText('marketplace_catalog_unsigned');

  await page.unroute('**/api/**');
  await mockApp(page, { schema_version: 'pandamonium.marketplace-view.v1', status: 'empty', failure: null, plugins: [] });
  await page.locator('#marketplace-retry').click();
  await expect(page.locator('#marketplace-results')).toContainText('No plugins published');
});
