// MAD-942: opening a tool window while another tool is edge-docked must not
// freeze the renderer. Regression for the ui.js modal z-stack observer that
// promoted two visible modals on every observer batch (infinite microtask
// loop, unresponsive tab).
import { expect, test } from '@playwright/test';

async function installMockRoutes(page) {
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/auth/status') {
      return route.fulfill({ json: { username: 'leo', is_admin: true, privileges: {} } });
    }
    if (['/api/sessions', '/api/models', '/api/model-endpoints', '/api/plugins'].includes(path)) {
      return route.fulfill({ json: [] });
    }
    if (path === '/api/notes') return route.fulfill({ json: { notes: [] } });
    return route.fulfill({ json: {} });
  });
}

async function openDockedUpdater(page) {
  await installMockRoutes(page);
  await page.goto('/static/index.html');
  await expect(page.locator('#sidebar-version-btn')).toBeVisible();
  await page.locator('#sidebar-version-btn').click();
  await expect(page.locator('#updater-modal')).toBeVisible();
  await expect(page.locator('#updater-modal')).toHaveClass(/modal-right-docked/);
}

test('Settings opened from the user bar while the Updater is docked does not freeze', async ({ page }) => {
  await openDockedUpdater(page);

  // The freeze trigger. On the broken build this click never resolves because
  // the renderer is stuck promoting the Updater and Settings forever.
  await page.locator('#user-bar-settings').click({ timeout: 5000 });
  await expect(page.locator('#settings-modal')).toBeVisible();
  await expect(page.locator('#settings-modal')).toHaveClass(/modal-right-docked/);

  // The window must still respond to input and layout after both are open.
  await page.locator('#settings-modal [data-settings-tab="appearance"]').click({ timeout: 5000 });
  await expect(page.locator('#set-text-size')).toBeVisible();
  expect(await page.evaluate(() => 1 + 1)).toBe(2);

  const z = await page.evaluate(() => ({
    settings: parseInt(getComputedStyle(document.getElementById('settings-modal')).zIndex, 10) || 0,
    updater: parseInt(getComputedStyle(document.getElementById('updater-modal')).zIndex, 10) || 0,
  }));
  expect(z.settings).toBeGreaterThan(z.updater);
});

test('the profile button opens Settings while the Updater is docked', async ({ page }) => {
  await openDockedUpdater(page);

  await page.locator('#user-bar-profile').click({ timeout: 5000 });
  await expect(page.locator('#settings-modal')).toBeVisible();
  await expect(page.locator('#settings-modal')).toHaveClass(/modal-right-docked/);
  await expect(page.locator('#settings-modal [data-settings-tab="account"]')).toHaveClass(/active/);
  expect(await page.evaluate(() => 1 + 1)).toBe(2);
});

test('a sidebar tool opened while a dock is active does not freeze', async ({ page }) => {
  await openDockedUpdater(page);

  await page.locator('#add-plugins-btn').click({ timeout: 5000 });
  await expect(page.locator('#marketplace-modal')).toBeVisible();
  await expect(page.locator('#marketplace-modal')).toHaveClass(/modal-right-docked/);
  expect(await page.evaluate(() => 1 + 1)).toBe(2);
});
