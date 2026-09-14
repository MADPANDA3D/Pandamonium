import { expect, test } from '@playwright/test';

const SOURCE_URL = 'https://github.com/example/demo-tools.git';
const REVISION = 'a'.repeat(40);

const artifact = {
  scan_version: 'jos-extension-scan.v1',
  source_url: SOURCE_URL,
  source_revision: REVISION,
  stage: 'report',
  repo_class: 'python_cli',
  capabilities: [
    { name: 'demo', kind: 'tool', descriptor: 'inline', evidence_path: 'pyproject.toml' },
  ],
  dependencies: [{ ecosystem: 'pypi', name: 'httpx', version: '>=0.27' }],
  licenses: ['MIT'],
  findings: [
    { id: 'license-missing', severity: 'low', category: 'license', title: 'No license file detected' },
  ],
  draft_manifest: {
    extension_id: 'demo-tools',
    name: 'Demo Tools',
    version: '0.0.0-draft',
    runtime: { type: 'service', entrypoint: 'pyproject.toml' },
    permissions: { default: 'read_only', capabilities: {} },
  },
  bounds: { files_scanned: 4, bytes_scanned: 512, duration_ms: 30 },
  executed_repo_commands: [],
  artifact_digest: `sha256:${'c'.repeat(64)}`,
};

async function mockApp(page) {
  let scanPolls = 0;
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/extensions/marketplace') {
      return route.fulfill({ json: { schema_version: 'pandamonium.marketplace-view.v1', status: 'empty', failure: null, plugins: [] } });
    }
    if (path === '/api/extensions/scans' && route.request().method() === 'POST') {
      return route.fulfill({ json: { scan_id: 'scan-1', status: 'queued', stage: 'fetch', progress: 0, message: 'Queued' } });
    }
    if (path === '/api/extensions/scans/scan-1') {
      scanPolls += 1;
      if (scanPolls === 1) {
        return route.fulfill({ json: { scan_id: 'scan-1', status: 'running', stage: 'extract', progress: 55, message: 'Extracting entrypoints' } });
      }
      return route.fulfill({ json: { scan_id: 'scan-1', status: 'succeeded', stage: 'report', progress: 100, message: 'Scan complete', artifact } });
    }
    if (path === '/api/extensions/plans/source') {
      return route.fulfill({ json: {
        plan_id: 'plan-1', operation: 'install', extension_id: 'demo-tools',
        source_revision: REVISION,
        manifest_origin: 'scan_draft',
        authority_decision: { decision: 'approval_required', decision_id: 'decision-1' },
        manifest: { name: 'Demo Tools' },
        requested_permissions: { default: 'read_only', capabilities: {} },
        lifecycle_commands: { install: [], start: [], stop: [], remove: [] },
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

test('Add from GitHub scans, reviews, and installs through approval', async ({ page }) => {
  await mockApp(page);
  await page.goto('/static/index.html');
  await page.getByRole('button', { name: 'Browse plugins' }).click();
  await page.getByRole('tab', { name: 'Add a new plugin' }).click();

  await expect(page.locator('#marketplace-source-ref')).toHaveCount(0);
  await page.locator('#marketplace-source-url').fill(SOURCE_URL);
  await page.getByRole('button', { name: 'Scan repository' }).click();

  await expect(page.locator('#marketplace-scan-progress')).toHaveAttribute('data-state', 'working');
  await expect(page.locator('#marketplace-scan-progress')).toHaveAttribute('data-state', 'complete', { timeout: 15000 });
  await expect(page.locator('#marketplace-scan-phases li[data-phase="report"]')).toHaveAttribute('data-state', 'complete');
  await expect(page.locator('#marketplace-scan-results')).toContainText('demo · tool · inline');
  await expect(page.locator('#marketplace-scan-results')).toContainText('Static scan only');
  await expect(page.locator('#marketplace-scan-results')).toContainText('MIT');

  const planRequest = page.waitForRequest(request => request.url().includes('/api/extensions/plans/source'));
  await page.getByRole('button', { name: 'Install plugin…' }).click();
  const sent = await planRequest;
  expect(sent.postDataJSON().scan_id).toBe('scan-1');
  await expect(page.getByText('Approval required: Install Demo Tools')).toBeVisible();
  await expect(page.locator('#marketplace-scan-results')).toContainText('generated draft manifest');
  await expect(page.getByRole('button', { name: 'Approve once' })).toBeInViewport();
  await expect(page.getByRole('button', { name: '← Back to scan' })).toBeVisible();
  await page.getByRole('button', { name: 'Approve once' }).click();
  await expect(page.locator('#marketplace-summary')).toContainText('Demo Tools: Install completed.');
});

test('Add from GitHub explains a missing manifest instead of a raw code', async ({ page }) => {
  await mockApp(page);
  await page.route('**/api/extensions/plans/source', route => route.fulfill({
    status: 409,
    json: { detail: 'extension_manifest_missing' },
  }));
  await page.goto('/static/index.html');
  await page.getByRole('button', { name: 'Browse plugins' }).click();
  await page.getByRole('tab', { name: 'Add a new plugin' }).click();

  await page.locator('#marketplace-source-url').fill(SOURCE_URL);
  await page.getByRole('button', { name: 'Scan repository' }).click();
  await expect(page.locator('#marketplace-scan-progress')).toHaveAttribute('data-state', 'complete', { timeout: 15000 });
  await page.getByRole('button', { name: 'Install plugin…' }).click();

  await expect(page.locator('#marketplace-scan-status')).toContainText('no jarvis-extension.json');
  await expect(page.locator('#marketplace-scan-status')).not.toContainText('extension_manifest_missing');
});

test('Add from GitHub rejects non-https sources without scanning', async ({ page }) => {
  await mockApp(page);
  await page.goto('/static/index.html');
  await page.getByRole('button', { name: 'Browse plugins' }).click();
  await page.getByRole('tab', { name: 'Add a new plugin' }).click();

  await page.locator('#marketplace-source-url').fill('http://example.com/repo.git');
  await page.getByRole('button', { name: 'Scan repository' }).click();

  await expect(page.locator('#marketplace-scan-progress')).toHaveAttribute('data-state', 'error');
  await expect(page.locator('#marketplace-scan-progress')).toContainText('Invalid repository URL');
});

test.describe('mobile', () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test('intake stays usable on mobile', async ({ page }) => {
    await mockApp(page);
    await page.goto('/static/index.html');
    await page.getByRole('button', { name: 'Toggle sidebar' }).click();
    await page.getByRole('button', { name: 'Browse plugins' }).click();
    await page.getByRole('tab', { name: 'Add a new plugin' }).click();

    await expect(page.locator('#marketplace-intake')).toBeVisible();
    const scanButton = page.getByRole('button', { name: 'Scan repository' });
    const box = await scanButton.boundingBox();
    expect(box.width).toBeGreaterThan(120);
    await scanButton.scrollIntoViewIfNeeded();
    await page.locator('#marketplace-source-url').fill(SOURCE_URL);
    await scanButton.click();
    await expect(page.locator('#marketplace-scan-progress')).toHaveAttribute('data-state', 'complete', { timeout: 15000 });
  });
});
