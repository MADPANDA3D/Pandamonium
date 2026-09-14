import { expect, test } from '@playwright/test';

const SCREENSHOT_DIR = '/tmp/opencode';

async function stubEmptySetup(page, { isAdmin = true } = {}) {
  await page.route('**/api/**', route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === '/api/auth/status') {
      return route.fulfill({
        json: {
          username: 'tester',
          is_admin: isAdmin,
          privileges: {},
          agent_identity: { source: 'configured', display_name: 'Friday', status: 'healthy' },
        },
      });
    }
    if (path === '/api/setup/status') {
      return route.fulfill({
        json: {
          is_admin: isAdmin,
          identity: { configured: true, display_name: 'Friday', status: 'healthy' },
          model: { usable: false, endpoints: 0, models: 0 },
          voice: { ready: false, enabled: true, provider: 'disabled' },
          integrations: { configured: 0, portal_connected: false },
          extensions: { installed: 0, enabled: 0 },
          update: null,
        },
      });
    }
    if (path === '/api/models') return route.fulfill({ json: { items: [] } });
    if (path === '/api/selector-catalog') {
      return route.fulfill({
        json: {
          discovery: {
            schema_version: 'pandamonium.discovery.v1',
            generated_at: '2026-09-05T12:00:00Z',
            entities: [],
          },
          selections: [],
        },
      });
    }
    if (path === '/api/sessions' || path === '/api/model-endpoints') return route.fulfill({ json: [] });
    if (path === '/api/gallery/discovery') return route.fulfill({ json: { connected: 0, sources: [] } });
    return route.fulfill({ json: {} });
  });
}

async function dismissWizard(page) {
  await page.addInitScript(() => {
    try {
      localStorage.setItem('pandamonium-setup-wizard-dismissed', '1');
      // The Models sidebar section is visibility-default-off; this spec needs
      // the models list empty state on screen.
      localStorage.setItem('odysseus-ui-visibility', JSON.stringify({ 'models-section': true }));
    } catch (_) { /* private mode */ }
  });
}

test('admin no-model surfaces share one wizard entry that opens the model step', async ({ page }) => {
  await dismissWizard(page);
  await stubEmptySetup(page, { isAdmin: true });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/static/index.html');

  const welcomeEntry = page.locator('#welcome-sub .setup-wizard-entry');
  await expect(welcomeEntry).toHaveText('Connect a model engine');
  await expect(welcomeEntry).toBeVisible();

  // The sidebar models list is the second no-model surface.
  await page.evaluate(() => window.modelsModule.refreshModels(true));
  const modelsEntry = page.locator('#models .setup-wizard-entry');
  await expect(modelsEntry).toHaveText('Connect a model engine');
  await page.screenshot({ path: `${SCREENSHOT_DIR}/mad-925-welcome-models-empty.png` });

  // The composer picker is the third.
  await page.locator('#model-picker-btn').click();
  const pickerList = page.locator('#model-picker-list');
  const pickerEntry = pickerList.locator('.setup-wizard-entry');
  await expect(pickerList).toContainText('No configured identities are available.');
  await expect(pickerEntry).toHaveText('Connect a model engine');
  await page.screenshot({ path: `${SCREENSHOT_DIR}/mad-925-picker-empty.png` });

  const modal = page.locator('#guide-modal');
  await pickerEntry.click();
  await expect(modal).not.toHaveClass(/hidden/);
  await expect(modal).toContainText('Give it a brain');
  await page.screenshot({ path: `${SCREENSHOT_DIR}/mad-925-wizard-model-step.png` });

  // Welcome and models entries open the same model step. Wait for each close
  // to finish before reopening: the shared modal hides on animationend, and a
  // fast reopen would otherwise be hidden by the pending close animation.
  await modal.locator('#close-guide-modal').click();
  await expect(modal).toHaveClass(/hidden/);
  await welcomeEntry.click();
  await expect(modal).not.toHaveClass(/hidden/);
  await expect(modal).toContainText('Give it a brain');
  await modal.locator('#close-guide-modal').click();
  await expect(modal).toHaveClass(/hidden/);
  await modelsEntry.click();
  await expect(modal).not.toHaveClass(/hidden/);
  await expect(modal).toContainText('Give it a brain');
});

test('non-admin no-model surfaces show managed copy and no setup command', async ({ page }) => {
  await dismissWizard(page);
  await stubEmptySetup(page, { isAdmin: false });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/static/index.html');
  await page.waitForFunction(() => window._isAdmin === false);

  const welcomeSub = page.locator('#welcome-sub');
  await expect(welcomeSub).toHaveText('Managed by your administrator');

  await page.evaluate(() => window.modelsModule.refreshModels(true));
  const modelsList = page.locator('#models');
  await expect(modelsList).toContainText('Managed by your administrator');
  await expect(modelsList.locator('.setup-wizard-entry')).toHaveCount(0);

  await page.locator('#model-picker-btn').click();
  const pickerList = page.locator('#model-picker-list');
  await expect(pickerList).toContainText('Managed by your administrator');
  await expect(pickerList.locator('.setup-wizard-entry')).toHaveCount(0);

  const body = await page.locator('body').innerText();
  expect(body).not.toContain('/setup');
  await page.screenshot({ path: `${SCREENSHOT_DIR}/mad-925-non-admin-empty.png` });
});

test('a wizard-linked plugin scan failure never shows the raw backend code', async ({ page }) => {
  await dismissWizard(page);
  await stubEmptySetup(page, { isAdmin: true });
  await page.route('**/api/extensions/scans', route => {
    if (route.request().method() !== 'POST') return route.fallback();
    return route.fulfill({ status: 400, json: { detail: 'extension_scan_source_invalid' } });
  });
  await page.route('**/api/extensions/marketplace', route => route.fulfill({
    json: { status: 'empty', plugins: [] },
  }));
  await page.route('**/api/extensions/installed', route => route.fulfill({ json: { plugins: [] } }));
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/static/index.html');

  await page.locator('#user-bar-guide').click();
  const modal = page.locator('#guide-modal');
  await expect(modal).not.toHaveClass(/hidden/);
  await modal.locator('.setup-lane').filter({ hasText: 'Plugins' })
    .getByRole('button', { name: 'Browse' }).click();
  // MAD-924: the lane opens the wizard's plugins step, which links onward.
  await expect(modal).toContainText('Add plugins');
  await modal.getByRole('button', { name: 'Add Plugins' }).click();

  const marketplace = page.locator('#marketplace-modal');
  await expect(marketplace).not.toHaveClass(/hidden/);
  await marketplace.getByRole('tab', { name: 'Add a new plugin' }).click();
  await marketplace.locator('#marketplace-source-url').fill('https://github.com/example/plugin');
  await marketplace.locator('#marketplace-source-scan').click();

  const detail = marketplace.locator('#marketplace-scan-detail');
  await expect(detail).toContainText('https://');
  await expect(marketplace).not.toContainText('extension_scan_source_invalid');
  await page.screenshot({ path: `${SCREENSHOT_DIR}/mad-925-plugin-error-human.png` });
});
