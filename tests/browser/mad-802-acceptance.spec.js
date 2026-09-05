import { expect, test } from '@playwright/test';


test('sidebar reports version status and ordinary chat can select configured Friday', async ({ page }) => {
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/version') {
      return route.fulfill({ json: {
        version: '1.0.9',
        latest_version: '1.0.10',
        update_available: true,
        update_url: 'https://github.com/MADPANDA3D/Pandamonium/releases/tag/v1.0.10',
        update_status: 'available',
      } });
    }
    if (path === '/api/agent-workers') {
      return route.fulfill({ json: {
        'pc-codex': {
          id: 'pc-codex', label: 'Friday', configured: true, ready: true,
          machine: 'Local workstation', connection: { state: 'connected' },
        },
      } });
    }
    if (path === '/api/auth/status') {
      return route.fulfill({ json: { username: 'leo', is_admin: true, privileges: {} } });
    }
    if (path === '/api/sessions' || path === '/api/model-endpoints' || path === '/api/models') {
      return route.fulfill({ json: [] });
    }
    return route.fulfill({ json: {} });
  });

  await page.goto('/static/index.html');

  await expect(page.locator('#sidebar-update-version')).toHaveText('Version v1.0.9');
  await expect(page.locator('#sidebar-update-state')).toHaveText('v1.0.10 available');
  await expect(page.locator('#sidebar-update-action')).toBeVisible();
  await expect(page.locator('#sidebar-update-action')).toHaveAttribute(
    'href',
    'https://github.com/MADPANDA3D/Pandamonium/releases/tag/v1.0.10',
  );

  await page.locator('#model-picker-btn').click();
  const friday = page.locator('.model-switch-item').filter({ hasText: 'Friday' });
  await expect(friday).toBeVisible();
  await friday.click();
  await expect(page.locator('#model-picker-label')).toHaveText('Friday');
  await expect.poll(() => page.evaluate(async () => (
    await import('/static/js/modelPicker.js')
  ).getSelectedAgentTarget())).toBe('pc-codex');

  await page.evaluate(async () => {
    const picker = await import('/static/js/modelPicker.js');
    picker.movePendingAgentTarget('session-friday');
  });
  await expect.poll(() => page.evaluate(async () => (
    await import('/static/js/modelPicker.js')
  ).getSelectedAgentTarget())).toBe('');

  await page.evaluate(async () => {
    const sessions = await import('/static/js/sessions.js');
    sessions.setCurrentSessionId('session-friday');
    sessions.updateModelPicker();
  });
  await expect(page.locator('#model-picker-label')).toHaveText('Friday');
  await expect.poll(() => page.evaluate(async () => (
    await import('/static/js/modelPicker.js')
  ).getSelectedAgentTarget())).toBe('pc-codex');

  await page.evaluate(async () => {
    const sessions = await import('/static/js/sessions.js');
    sessions.setCurrentSessionId(null);
    sessions.updateModelPicker();
  });
  await expect.poll(() => page.evaluate(async () => (
    await import('/static/js/modelPicker.js')
  ).getSelectedAgentTarget())).toBe('');
});
