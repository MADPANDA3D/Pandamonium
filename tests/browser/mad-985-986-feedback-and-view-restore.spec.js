import { expect, test } from '@playwright/test';

/**
 * MAD-985 / MAD-986 — discoverable bug report entry + view restore across reload.
 *
 * The static test server has no backend, so /api/** is mocked. Covers the
 * sidebar "Report" button (MAD-985) and restoring the focused full-screen
 * view (Settings) plus the bounded fallback (MAD-986).
 */

function sessionFixture() {
  return {
    id: 'session-one',
    name: 'Existing chat',
    model: 'test/model',
    endpoint_url: 'http://model.test/v1/chat/completions',
    message_count: 0,
    archived: false,
    created_at: '2026-09-04T20:00:00Z',
    updated_at: '2026-09-04T20:01:00Z',
    last_message_at: '2026-09-04T20:01:00Z',
    workspace: '',
  };
}

async function mockApi(page) {
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path === '/api/sessions') return route.fulfill({ json: [sessionFixture()] });
    if (path === '/api/auth/status') return route.fulfill({ json: { username: 'tester', is_admin: true, privileges: {} } });
    if (path === '/api/default-chat') {
      return route.fulfill({ json: { endpoint_id: 'endpoint-one', endpoint_url: 'http://model.test/v1/chat/completions', model: 'test/model' } });
    }
    if (path === '/api/history/session-one') {
      return route.fulfill({ json: { history: [], model: 'test/model', name: 'Existing chat', offset: 0, limit: 100, total: 0, has_more_before: false } });
    }
    if (path === '/api/model-endpoints' || path === '/api/models') return route.fulfill({ json: [] });
    return route.fulfill({ json: {} });
  });
}

async function boot(page) {
  await page.goto('/static/index.html#session-one');
  await expect.poll(() => page.evaluate(async () => {
    const directImport = await import('/static/js/sessions.js');
    return window.sessionModule === directImport.default;
  }), { timeout: 15_000 }).toBe(true);
}

test('the sidebar exposes a Report a bug button that opens the panel', async ({ page }) => {
  await mockApi(page);
  await boot(page);

  const button = page.locator('#user-bar-report');
  await expect(button).toBeVisible();
  await expect(button).toContainText('Report');

  await button.click();
  await expect(page.locator('#bug-report-modal')).toBeVisible();
});

test('Settings is restored after a reload', async ({ page }) => {
  await mockApi(page);
  await boot(page);

  await page.evaluate(async () => { (await import('/static/js/settings.js')).open(); });
  await expect(page.locator('#settings-modal')).toBeVisible();

  await page.reload();
  await expect(page.locator('#settings-modal')).toBeVisible();
});

test('closing Settings clears the saved view so reload returns to chat', async ({ page }) => {
  await mockApi(page);
  await boot(page);

  await page.evaluate(async () => { (await import('/static/js/settings.js')).open(); });
  await expect(page.locator('#settings-modal')).toBeVisible();

  await page.evaluate(async () => { (await import('/static/js/settings.js')).close(); });
  await expect(page.locator('#settings-modal')).toBeHidden();

  await page.reload();
  await expect(page.locator('#settings-modal')).toBeHidden();
});

test('an unknown persisted view falls back to chat without an error loop', async ({ page }) => {
  await mockApi(page);
  await page.addInitScript(() => {
    localStorage.setItem('odysseus-active-view', JSON.stringify({ kind: 'nonsense', id: 'x' }));
  });
  await boot(page);

  await expect(page.locator('#settings-modal')).toBeHidden();
  await expect(page.locator('#bug-report-modal')).toBeHidden();
});