import { expect, test } from '@playwright/test';

function sessionFixture() {
  return {
    id: 'session-one',
    name: 'Existing chat',
    model: 'test/model',
    endpoint_url: 'http://model.test/v1/chat/completions',
    message_count: 2,
    archived: false,
    created_at: '2026-09-04T20:00:00Z',
    updated_at: '2026-09-04T20:01:00Z',
    last_message_at: '2026-09-04T20:01:00Z',
  };
}

test('sidebar New Chat leaves the active session before discovery finishes', async ({ page }) => {
  let stallDefaultChat = false;
  let releaseDefaultChat;
  const defaultChatGate = new Promise(resolve => { releaseDefaultChat = resolve; });

  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/default-chat') {
      if (stallDefaultChat) await defaultChatGate;
      return route.fulfill({
        json: {
          endpoint_id: 'endpoint-one',
          endpoint_url: 'http://model.test/v1/chat/completions',
          model: 'test/model',
        },
      });
    }
    if (url.pathname === '/api/sessions') {
      return route.fulfill({ json: [sessionFixture()] });
    }
    if (url.pathname === '/api/history/session-one') {
      return route.fulfill({
        json: {
          history: [
            { role: 'user', content: 'Hello' },
            { role: 'assistant', content: 'Hi there.' },
          ],
          model: 'test/model',
          name: 'Existing chat',
          endpoint_url: 'http://model.test/v1/chat/completions',
          offset: 0,
          limit: 100,
          total: 2,
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
    return route.fulfill({ json: {} });
  });

  await page.goto('/static/index.html#session-one');
  await expect.poll(() => page.evaluate(async () => {
    const directImport = await import('/static/js/sessions.js');
    return window.sessionModule === directImport.default;
  })).toBe(true);
  await expect.poll(() => page.evaluate(() => window.sessionModule?.getCurrentSessionId())).toBe('session-one');
  await expect(page.locator('#current-meta')).toHaveText('Existing chat');

  stallDefaultChat = true;
  await page.locator('#sidebar-new-chat-btn').click();

  await expect(page.locator('#current-meta')).toHaveText('New Chat', { timeout: 750 });
  await expect.poll(() => page.evaluate(() => window.sessionModule?.getCurrentSessionId())).toBe(null);
  await expect.poll(() => page.evaluate(() => window.sessionModule?.getPendingChat()?.source)).toBe('discovering');

  await page.evaluate(() => window.sessionModule.selectSession('session-one'));
  releaseDefaultChat();
  await expect.poll(() => page.evaluate(() => window.sessionModule?.getCurrentSessionId())).toBe('session-one');
  await expect.poll(() => page.evaluate(() => window.sessionModule?.getPendingChat())).toBe(null);
});

test('New Chat clears the visible session immediately while Compare teardown is pending', async ({ page }) => {
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/sessions') {
      return route.fulfill({ json: [sessionFixture()] });
    }
    if (url.pathname === '/api/history/session-one') {
      return route.fulfill({
        json: {
          history: [
            { role: 'user', content: 'Old conversation' },
            { role: 'assistant', content: 'Still visible' },
          ],
          model: 'test/model',
          name: 'Existing chat',
          endpoint_url: 'http://model.test/v1/chat/completions',
          offset: 0,
          limit: 100,
          total: 2,
          has_more_before: false,
        },
      });
    }
    if (url.pathname === '/api/auth/status') {
      return route.fulfill({ json: { username: 'tester', is_admin: false, privileges: {} } });
    }
    if (url.pathname === '/api/default-chat') {
      return route.fulfill({ json: {} });
    }
    if (url.pathname === '/api/model-endpoints' || url.pathname === '/api/models') {
      return route.fulfill({ json: [] });
    }
    return route.fulfill({ json: {} });
  });

  await page.goto('/static/index.html#session-one');
  await expect.poll(() => page.evaluate(() => window.sessionModule?.getCurrentSessionId())).toBe('session-one');
  await expect(page.locator('#chat-history .msg')).toHaveCount(2);
  await page.locator('#message:visible').fill('unsent draft');

  await page.evaluate(() => {
    window.compareModule.isActive = () => true;
    window.compareModule.deactivate = () => new Promise(() => {});
  });
  await page.locator('#sidebar-new-chat-btn').click();

  await expect(page.locator('#current-meta')).toHaveText('New Chat', { timeout: 750 });
  await expect.poll(() => page.evaluate(() => window.sessionModule?.getCurrentSessionId())).toBe(null);
  await expect.poll(() => page.evaluate(() => window.sessionModule?.getPendingChat()?.source)).toBe('discovering');
  await expect(page.locator('#chat-history .msg')).toHaveCount(0);
  await expect(page.locator('#welcome-screen')).not.toHaveClass(/hidden/);
  await expect(page.locator('#message:visible')).toHaveValue('');

  await page.evaluate(() => window.sessionModule.loadSessions());
  await expect.poll(() => page.evaluate(() => window.sessionModule?.getCurrentSessionId())).toBe(null);
  await expect(page.locator('#current-meta')).toHaveText('New Chat');
});
