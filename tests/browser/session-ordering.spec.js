import { expect, test } from '@playwright/test';

function sessionFixture(id, name, createdAt, lastMessageAt, extra = {}) {
  return {
    id,
    name,
    model: 'test/model',
    endpoint_url: 'http://model.test/v1/chat/completions',
    message_count: 2,
    archived: false,
    created_at: createdAt,
    updated_at: lastMessageAt,
    last_message_at: lastMessageAt,
    ...extra,
  };
}

test('chat dates and latest-message order refresh live without restoring archived state', async ({ page }) => {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const todayAt = minutes => new Date(today.getTime() + minutes * 60_000).toISOString();
  const current = sessionFixture(
    'session-current',
    'Current chat',
    new Date(today.getTime() - 10 * 86400000).toISOString(),
    todayAt(1),
    { folder: 'Work' },
  );
  const recent = sessionFixture(
    'session-recent',
    'Recent chat',
    todayAt(1),
    todayAt(2),
    { folder: 'Work' },
  );
  const favoriteYesterday = sessionFixture(
    'session-favorite',
    'Favorite yesterday',
    new Date(today.getTime() - 60_000).toISOString(),
    new Date(today.getTime() - 60_000).toISOString(),
    { is_important: true },
  );
  const archived = sessionFixture(
    'session-archived',
    'Archived latest',
    todayAt(3),
    todayAt(3),
    { archived: true },
  );
  let responseSessions = [archived, favoriteYesterday, current, recent];

  await page.addInitScript(() => {
    localStorage.setItem('odysseus-session-sort', 'newest');
    localStorage.setItem('lastSessionId', 'session-current');
  });
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/sessions') {
      return route.fulfill({ json: responseSessions });
    }
    if (url.pathname === '/api/history/session-current') {
      return route.fulfill({
        json: {
          history: [
            { role: 'user', content: 'Keep me selected' },
            { role: 'assistant', content: 'Current response' },
          ],
          model: 'test/model',
          name: 'Current chat',
          endpoint_url: 'http://model.test/v1/chat/completions',
          offset: 0,
          limit: 100,
          total: 2,
          has_more_before: false,
        },
      });
    }
    if (url.pathname === '/api/history/session-archived') {
      return route.fulfill({
        json: {
          history: [{ role: 'assistant', content: 'Archived response' }],
          model: 'test/model',
          name: 'Archived latest',
          offset: 0,
          limit: 100,
          total: 1,
          has_more_before: false,
        },
      });
    }
    if (url.pathname === '/api/auth/status') {
      return route.fulfill({ json: { username: 'tester', is_admin: false, privileges: {} } });
    }
    if (url.pathname === '/api/model-endpoints' || url.pathname === '/api/models') {
      return route.fulfill({ json: [] });
    }
    if (url.pathname === '/api/default-chat') {
      return route.fulfill({ json: {} });
    }
    return route.fulfill({ json: {} });
  });

  await page.goto('/static/index.html#session-archived');
  await expect.poll(() => page.evaluate(() => window.sessionModule?.getCurrentSessionId())).toBe('session-current');
  await expect(page.locator('#current-meta')).toHaveText('Current chat');
  await expect(page.locator('[data-session-id="session-archived"]')).toHaveCount(0);

  const sessionIds = () => page.locator('#session-list .list-item[data-session-id]').evaluateAll(
    items => items.map(item => item.dataset.sessionId),
  );
  await expect.poll(sessionIds).toEqual(['session-recent', 'session-current', 'session-favorite']);
  const dateHeaders = page.locator('#session-list .date-section-header');
  await expect(dateHeaders).toHaveText(['Today', 'Yesterday']);
  await expect(dateHeaders.first()).toBeVisible();
  await expect(dateHeaders.last()).toBeVisible();

  for (let click = 0; click < 2; click += 1) {
    await page.locator('#session-sort-btn').click();
    await page.locator('#session-sort-dropdown [data-sort="active"]').click();
  }
  await expect.poll(() => page.evaluate(() => window.sessionModule?.getSortMode())).toBe('active');
  await expect(dateHeaders.first()).toBeVisible();

  await page.evaluate(() => { window.__sessionOrderingPageMarker = true; });
  current.last_message_at = todayAt(4);
  current.updated_at = current.last_message_at;
  responseSessions = [favoriteYesterday, archived, recent, current];
  await page.evaluate(() => window.sessionModule.loadSessions());

  await expect.poll(sessionIds).toEqual(['session-current', 'session-recent', 'session-favorite']);
  await expect(dateHeaders).toHaveText(['Today', 'Yesterday']);
  await expect.poll(() => page.evaluate(() => window.__sessionOrderingPageMarker)).toBe(true);
  await expect.poll(() => page.evaluate(() => window.sessionModule?.getCurrentSessionId())).toBe('session-current');

  await page.evaluate(() => window.sessionModule.setSortMode('group'));
  await expect(page.locator('#session-list .session-folder-content .date-section-header')).toHaveText(['Today', 'Yesterday']);
  await expect(page.locator('#session-list .session-folder-content').first()).toContainText('Current chat');
});
