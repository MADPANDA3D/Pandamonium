// Access-mode control (MAD-885): shield button left of the Jarvis sphere,
// three-option menu, server persistence through /api/prefs/access_mode.
import { expect, test } from '@playwright/test';

async function installMockRoutes(page, captured) {
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/prefs/access_mode') {
      if (route.request().method() === 'PUT') {
        captured.push(JSON.parse(route.request().postData() || '{}'));
        const body = JSON.parse(route.request().postData() || '{}');
        return route.fulfill({ json: { key: 'access_mode', value: body.value } });
      }
      return route.fulfill({ json: { key: 'access_mode', value: 'ask_for_approval' } });
    }
    if (url.pathname === '/api/auth/status') {
      return route.fulfill({ json: { username: 'leo', is_admin: true, privileges: {} } });
    }
    if (url.pathname === '/api/sessions' || url.pathname === '/api/model-endpoints' || url.pathname === '/api/models') {
      return route.fulfill({ json: [] });
    }
    return route.fulfill({ json: {} });
  });
}

test('access-mode button sits left of Jarvis sphere and persists selection', async ({ page }) => {
  const captured = [];
  await installMockRoutes(page, captured);
  await page.goto('/static/index.html');
  await expect(page.locator('#access-mode-btn')).toBeVisible();

  const sphereBox = await page.locator('#jarvis-input-sphere').boundingBox();
  const btnBox = await page.locator('#access-mode-btn').boundingBox();
  expect(btnBox.x + btnBox.width <= sphereBox.x + 1).toBe(true);

  // Loaded state: default mode applied (GET /api/prefs/access_mode).
  await expect(page.locator('#access-mode-btn')).toHaveAttribute('data-access-mode', 'ask_for_approval');
  await expect(page.locator('#access-mode-btn')).toHaveAttribute('title', 'Agent access: Ask for approval');

  // Menu shows Leo's exact three options.
  await page.locator('#access-mode-btn').click();
  await expect(page.locator('#access-mode-menu')).toBeVisible();
  await expect(page.locator('#access-mode-menu')).toContainText('Ask for approval');
  await expect(page.locator('#access-mode-menu')).toContainText('Always ask to edit external files and use the internet');
  await expect(page.locator('#access-mode-menu')).toContainText('Approve for me');
  await expect(page.locator('#access-mode-menu')).toContainText('Only ask for actions detected as potentially unsafe');
  await expect(page.locator('#access-mode-menu')).toContainText('Full access');
  await expect(page.locator('#access-mode-menu')).toContainText('Unrestricted access to the internet and any file on your computer');
  expect(await page.locator('#access-mode-menu .access-mode-option').count()).toBe(3);

  // Selecting persists server-side and reflects immediately.
  await page.locator('#access-mode-menu .access-mode-option[data-access-value="approve_for_me"]').click();
  await expect(page.locator('#access-mode-btn')).toHaveAttribute('data-access-mode', 'approve_for_me');
  await expect(page.locator('#access-mode-btn')).toHaveAttribute('title', 'Agent access: Approve for me');
  expect(captured).toEqual([{ value: 'approve_for_me' }]);

  // Menu closes and Escape/outside click dismisses without persisting.
  await page.locator('#access-mode-btn').click();
  await expect(page.locator('#access-mode-menu')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('#access-mode-menu')).toBeHidden();
  expect(captured).toEqual([{ value: 'approve_for_me' }]);
});