import { expect, test } from '@playwright/test';


test('sidebar reports version status and ordinary chat can select configured Friday', async ({ page }) => {
  let fridayConfigured = true;
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
      if (!fridayConfigured) return route.fulfill({ json: {} });
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
  await expect(page.locator('#model-picker-menu')).toHaveClass(/hidden/);
  await expect(page.locator('#model-picker-label')).toHaveText('Friday');
  await expect.poll(() => page.evaluate(async () => (
    await import('/static/js/modelPicker.js')
  ).getSelectedAgentTarget())).toBe('pc-codex');

  await page.evaluate(async () => {
    const sessions = await import('/static/js/sessions.js');
    sessions.createBlankChat();
  });
  await expect.poll(() => page.evaluate(async () => (
    await import('/static/js/modelPicker.js')
  ).getSelectedAgentTarget())).toBe('');

  await page.locator('#model-picker-btn').click();
  await page.locator('.model-switch-item').filter({ hasText: 'Friday' }).click();
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

  await expect.poll(() => page.evaluate(() => JSON.parse(
    localStorage.getItem('odysseus-agent-selections') || '{}',
  )['session-friday']?.target)).toBe('pc-codex');
  await page.reload();
  await page.evaluate(async () => {
    const sessions = await import('/static/js/sessions.js');
    sessions.setCurrentSessionId('session-friday');
    sessions.updateModelPicker();
  });
  await expect(page.locator('#model-picker-label')).toHaveText('Friday');
  await expect.poll(() => page.evaluate(async () => (
    await import('/static/js/modelPicker.js')
  ).getSelectedAgentTarget())).toBe('pc-codex');

  fridayConfigured = false;
  await page.locator('#model-picker-btn').click();
  await expect.poll(() => page.evaluate(async () => (
    await import('/static/js/modelPicker.js')
  ).getSelectedAgentTarget())).toBe('');
  await expect.poll(() => page.evaluate(() => JSON.parse(
    localStorage.getItem('odysseus-agent-selections') || '{}',
  )['session-friday'])).toBeUndefined();

  await page.evaluate(async () => {
    const sessions = await import('/static/js/sessions.js');
    sessions.setCurrentSessionId(null);
    sessions.updateModelPicker();
  });
  await expect.poll(() => page.evaluate(async () => (
    await import('/static/js/modelPicker.js')
  ).getSelectedAgentTarget())).toBe('');
});


test('login submission does not wait for the remote release status', async ({ page }) => {
  let releaseVersion;
  const releaseGate = new Promise(resolve => { releaseVersion = resolve; });
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/version') {
      await releaseGate;
      return route.fulfill({ json: { version: '1.0.10' } });
    }
    if (path === '/api/brand') {
      return route.fulfill({ json: { name: 'Pandamonium', logo: '', accent: '#e06c75' } });
    }
    if (path === '/api/auth/status') {
      return route.fulfill({ json: { authenticated: false, configured: true, signup_enabled: false } });
    }
    if (path === '/api/auth/policy') {
      return route.fulfill({ json: { password_min_length: 8, reserved_usernames: [] } });
    }
    if (path === '/api/auth/login') {
      return route.fulfill({ status: 401, json: { detail: 'fixture rejection' } });
    }
    return route.fulfill({ json: {} });
  });

  await page.goto('/static/login.html');
  await page.locator('#username').fill('leo');
  await page.locator('#password').fill('not-a-real-password');
  const loginRequest = page.waitForRequest(request => (
    new URL(request.url()).pathname === '/api/auth/login' && request.method() === 'POST'
  ));
  await page.locator('#submitBtn').click();
  const request = await loginRequest;
  expect(request.postDataJSON()).toMatchObject({ username: 'leo' });
  expect(page.url()).not.toContain('password=');
  releaseVersion();
});
