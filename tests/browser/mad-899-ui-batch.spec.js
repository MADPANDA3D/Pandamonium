// MAD-899 quick visual batch: orb symmetry, right-docked Settings/Updater,
// display size controls, and sidebar bucket reorder.
import { expect, test } from '@playwright/test';

async function installMockRoutes(page) {
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/auth/status') {
      return route.fulfill({ json: { username: 'leo', is_admin: true, privileges: {} } });
    }
    if (['/api/sessions', '/api/model-endpoints', '/api/models', '/api/plugins'].includes(url.pathname)) {
      return route.fulfill({ json: [] });
    }
    return route.fulfill({ json: {} });
  });
}

test('quick visual batch — orb, docks, display size, bucket reorder', async ({ page }) => {
  await installMockRoutes(page);
  await page.goto('/static/index.html');
  await expect(page.locator('#access-mode-btn')).toBeVisible();

  // 1. Shield/access orb matches the voice orb for symmetry (MAD-899).
  const shield = await page.locator('#access-mode-btn').boundingBox();
  const voice = await page.locator('#jarvis-input-sphere').boundingBox();
  expect(Math.abs(shield.width - voice.width)).toBeLessThanOrEqual(1);
  expect(Math.abs(shield.height - voice.height)).toBeLessThanOrEqual(1);

  // 2. Settings opens docked to the right.
  const iconBefore = await page.locator('#email-section .section-icon').evaluate(el => el.getBoundingClientRect().width);
  expect(iconBefore).toBeGreaterThan(0);
  await page.evaluate(async () => (await import('/static/js/settings.js')).open());
  await expect(page.locator('#settings-modal')).toBeVisible();
  await expect(page.locator('#settings-modal')).toHaveClass(/modal-right-docked/);

  // 3. Display size controls apply and persist.
  await page.locator('#settings-modal [data-settings-tab="appearance"]').click();
  await page.locator('#set-text-size').selectOption('125');
  await expect.poll(() => page.evaluate(() => document.documentElement.classList.contains('ui-scale-125'))).toBe(true);
  await page.locator('#set-icon-size').selectOption('150');
  await expect.poll(() => page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--ui-icon-scale').trim())).toBe('1.5');
  expect(await page.evaluate(() => localStorage.getItem('odysseus-ui-scale'))).toBe('125');
  expect(await page.evaluate(() => localStorage.getItem('odysseus-ui-icon-scale'))).toBe('150');
  await page.locator('#settings-modal .close-btn').click();
  await expect(page.locator('#settings-modal')).toBeHidden();

  // Persisted sizes re-apply on load and visibly grow the sidebar icons.
  await page.reload();
  const iconAfter = await page.locator('#email-section .section-icon').evaluate(el => el.getBoundingClientRect().width);
  expect(iconAfter).toBeGreaterThan(iconBefore);

  // Reset to defaults for the reorder/updater checks.
  await page.evaluate(() => {
    localStorage.removeItem('odysseus-ui-scale');
    localStorage.removeItem('odysseus-ui-icon-scale');
  });
  await page.reload();
  await expect(page.locator('#access-mode-btn')).toBeVisible();

  // 4. Bucket reorder — handle drag moves Plugins above Tools and persists.
  const handle = page.locator('#plugins-section .section-drag-handle');
  await page.locator('#plugins-section').hover();
  const handleBox = await handle.boundingBox();
  const toolsBox = await page.locator('#tools-section').boundingBox();
  expect(toolsBox).not.toBeNull();
  await page.mouse.move(handleBox.x + handleBox.width / 2, handleBox.y + handleBox.height / 2);
  await page.mouse.down();
  await page.mouse.move(toolsBox.x + 8, Math.max(1, toolsBox.y + 4), { steps: 8 });
  await page.mouse.up();
  await expect.poll(() => page.evaluate(() => {
    const ids = [...document.querySelectorAll('.sidebar-inner .section')].map(s => s.id);
    return ids.indexOf('plugins-section') < ids.indexOf('tools-section');
  })).toBe(true);
  const saved = await page.evaluate(() => localStorage.getItem('sidebar-section-order'));
  const order = JSON.parse(saved);
  expect(order.indexOf('plugins-section')).toBeLessThan(order.indexOf('tools-section'));

  // 5. Updater modal also opens right-docked (sidebar button path).
  await page.locator('#sidebar-update-check').click();
  await expect(page.locator('#updater-modal')).toBeVisible();
  await expect(page.locator('#updater-modal')).toHaveClass(/modal-right-docked/);
});
