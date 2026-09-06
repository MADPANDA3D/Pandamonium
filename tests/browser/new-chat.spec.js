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

async function waitForChatSubmitReady(page) {
  await expect.poll(() => page.evaluate(() => (
    typeof document.querySelector('#chat-form')?.onsubmit === 'function'
  ))).toBe(true);
}

async function waitForSession(page, sessionId) {
  await expect.poll(
    () => page.evaluate(() => window.sessionModule?.getCurrentSessionId()),
    { timeout: 15_000 },
  ).toBe(sessionId);
}

test('sidebar New Chat preserves the active configuration and sends immediately', async ({ page }) => {
  let stallDefaultChat = false;
  let releaseDefaultChat;
  const defaultChatGate = new Promise(resolve => { releaseDefaultChat = resolve; });
  let createdSession = false;
  let streamedSession = '';

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
    if (url.pathname === '/api/session' && route.request().method() === 'POST') {
      createdSession = true;
      return route.fulfill({ json: { id: 'session-new' } });
    }
    if (url.pathname === '/api/chat_stream') {
      const body = await route.request().postDataBuffer();
      streamedSession = body?.toString().match(/name="session"\r\n\r\n([^\r]+)/)?.[1] || '';
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: 'data: {"type":"model_info","model":"test/model","requested_model":"test/model"}\n\n'
          + 'data: {"delta":"New conversation ready."}\n\n'
          + 'data: [DONE]\n\n',
      });
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
  await waitForSession(page, 'session-one');
  await waitForChatSubmitReady(page);
  await expect(page.locator('#current-meta')).toHaveText('Existing chat');

  stallDefaultChat = true;
  await page.locator('#sidebar-new-chat-btn').click();

  await expect(page.locator('#current-meta')).toHaveText('New Chat', { timeout: 750 });
  await expect.poll(() => page.evaluate(() => window.sessionModule?.getCurrentSessionId())).toBe(null);
  await expect.poll(() => page.evaluate(() => window.sessionModule?.getPendingChat())).toMatchObject({
    source: 'new_chat',
    modelId: 'test/model',
    url: 'http://model.test/v1/chat/completions',
  });

  await page.locator('#message:visible').fill('Start another conversation');
  await page.locator('.send-btn:visible').click();
  await expect.poll(() => createdSession).toBe(true);
  await expect.poll(() => streamedSession).toBe('session-new');
  await expect.poll(() => page.evaluate(() => window.sessionModule?.getCurrentSessionId())).toBe('session-new');

  await page.evaluate(() => window.sessionModule.selectSession('session-one'));
  releaseDefaultChat();
  await waitForSession(page, 'session-one');
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
  await waitForSession(page, 'session-one');
  await expect(page.locator('#chat-history .msg')).toHaveCount(2);
  await page.locator('#message:visible').fill('unsent draft');

  await page.evaluate(() => {
    window.compareModule.isActive = () => true;
    window.compareModule.deactivate = () => new Promise(() => {});
  });
  await page.locator('#sidebar-new-chat-btn').click();

  await expect(page.locator('#current-meta')).toHaveText('New Chat', { timeout: 750 });
  await expect.poll(() => page.evaluate(() => window.sessionModule?.getCurrentSessionId())).toBe(null);
  await expect.poll(() => page.evaluate(() => window.sessionModule?.getPendingChat()?.source)).toBe('new_chat');
  await expect(page.locator('#chat-history .msg')).toHaveCount(0);
  await expect(page.locator('#welcome-screen')).not.toHaveClass(/hidden/);
  await expect(page.locator('#message:visible')).toHaveValue('');

  await page.evaluate(() => window.sessionModule.loadSessions());
  await expect.poll(() => page.evaluate(() => window.sessionModule?.getCurrentSessionId())).toBe(null);
  await expect(page.locator('#current-meta')).toHaveText('New Chat');
});

test('New Chat clears a completed tool conversation without a browser refresh', async ({ page }) => {
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/sessions') {
      return route.fulfill({ json: [sessionFixture()] });
    }
    if (url.pathname === '/api/history/session-one') {
      return route.fulfill({
        json: {
          history: [],
          model: 'test/model',
          name: 'Existing chat',
          endpoint_url: 'http://model.test/v1/chat/completions',
          offset: 0,
          limit: 100,
          total: 0,
          has_more_before: false,
        },
      });
    }
    if (url.pathname === '/api/chat_stream') {
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: 'data: {"type":"model_info","model":"test/model","requested_model":"test/model"}\n\n'
          + 'data: {"type":"tool_start","tool":"bash","command":"whoami"}\n\n'
          + 'data: {"type":"tool_output","tool":"bash","command":"whoami","output":"odysseus","exit_code":0}\n\n'
          + 'data: {"type":"agent_step","round":2}\n\n'
          + 'data: {"delta":"odysseus"}\n\n'
          + 'data: [DONE]\n\n',
      });
    }
    if (url.pathname === '/api/auth/status') {
      return route.fulfill({ json: { username: 'tester', is_admin: false, privileges: {} } });
    }
    if (url.pathname === '/api/default-chat') {
      return route.fulfill({
        json: {
          endpoint_id: 'endpoint-one',
          endpoint_url: 'http://model.test/v1/chat/completions',
          model: 'test/model',
        },
      });
    }
    if (url.pathname === '/api/model-endpoints' || url.pathname === '/api/models') {
      return route.fulfill({ json: [] });
    }
    return route.fulfill({ json: {} });
  });

  await page.goto('/static/index.html#session-one');
  await waitForSession(page, 'session-one');
  await waitForChatSubmitReady(page);

  await page.locator('#message:visible').fill('Run whoami and tell me the result');
  await page.locator('.send-btn:visible').click();
  await expect(page.locator('.agent-thread-node')).toContainText('bashdone');
  await expect(page.locator('#chat-history')).toContainText('odysseus');
  await expect(page.locator('.send-btn:visible')).not.toHaveAttribute('data-mode', 'streaming');

  await page.locator('#sidebar-new-chat-btn').click();

  await expect(page.locator('#current-meta')).toHaveText('New Chat');
  await expect.poll(() => page.evaluate(() => window.sessionModule?.getCurrentSessionId())).toBe(null);
  await expect(page.locator('#chat-history')).not.toContainText('odysseus');
  await expect(page.locator('#chat-history .msg, #chat-history .agent-thread')).toHaveCount(0);
  await expect(page.locator('#welcome-screen')).not.toHaveClass(/hidden/);
  await expect(page.locator('#message:visible')).toBeEditable();
});

test('every New Chat launcher uses the same blank pending lifecycle', async ({ page }) => {
  let sessionCreates = 0;
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/sessions') return route.fulfill({ json: [sessionFixture()] });
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
    if (url.pathname === '/api/session' && route.request().method() === 'POST') {
      sessionCreates += 1;
      return route.fulfill({ json: { id: 'unexpected-session' } });
    }
    if (url.pathname === '/api/default-chat') {
      return route.fulfill({
        json: {
          endpoint_id: 'endpoint-one',
          endpoint_url: 'http://model.test/v1/chat/completions',
          model: 'test/model',
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
  const launchers = [
    ['sidebar', () => page.locator('#sidebar-new-chat-btn').click()],
    ['rail', () => page.locator('#rail-new-session').evaluate(button => button.click())],
    ['brand', () => page.locator('#sidebar-brand-btn').click()],
    ['composer', async () => {
      await expect(page.locator('.send-btn:visible')).toHaveAttribute('data-mode', 'newchat');
      await page.locator('.send-btn:visible').click();
    }],
    ['keyboard', () => page.keyboard.press('Control+Alt+n')],
  ];

  for (const [name, launch] of launchers) {
    await waitForSession(page, 'session-one');
    await expect(page.locator('#chat-history .msg'), name).toHaveCount(2);
    await launch();
    await expect.poll(() => page.evaluate(() => window.sessionModule?.getCurrentSessionId()), name).toBe(null);
    await expect(page.locator('#current-meta'), name).toHaveText('New Chat');
    await expect(page.locator('#chat-history .msg, #chat-history .agent-thread'), name).toHaveCount(0);
    await expect.poll(() => page.evaluate(() => window.sessionModule?.getPendingChat()?.source), name).toBe('new_chat');
    expect(sessionCreates, `${name} must not eagerly create a session`).toBe(0);
    await page.evaluate(() => window.sessionModule.selectSession('session-one'));
  }
});

test('New Chat stays blank while an active tool stream finishes and sessions refresh', async ({ page }) => {
  let releaseStream;
  let sessionLoads = 0;
  const streamGate = new Promise(resolve => { releaseStream = resolve; });
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/sessions') {
      sessionLoads += 1;
      return route.fulfill({ json: [sessionFixture()] });
    }
    if (url.pathname === '/api/history/session-one') {
      return route.fulfill({
        json: {
          history: [],
          model: 'test/model',
          name: 'Existing chat',
          endpoint_url: 'http://model.test/v1/chat/completions',
          offset: 0,
          limit: 100,
          total: 0,
          has_more_before: false,
        },
      });
    }
    if (url.pathname === '/api/chat_stream') {
      await streamGate;
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: 'data: {"type":"tool_start","tool":"bash","command":"whoami"}\n\n'
          + 'data: {"type":"tool_output","tool":"bash","command":"whoami","output":"odysseus","exit_code":0}\n\n'
          + 'data: {"delta":"odysseus"}\n\n'
          + 'data: [DONE]\n\n',
      });
    }
    if (url.pathname === '/api/default-chat') {
      return route.fulfill({
        json: {
          endpoint_id: 'endpoint-one',
          endpoint_url: 'http://model.test/v1/chat/completions',
          model: 'test/model',
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
  await waitForSession(page, 'session-one');
  await waitForChatSubmitReady(page);
  await page.locator('#message:visible').fill('Run whoami');
  await page.locator('.send-btn:visible').click();
  await expect(page.locator('.send-btn:visible')).toHaveAttribute('data-mode', 'streaming');

  await page.locator('#sidebar-new-chat-btn').click();
  await expect(page.locator('#current-meta')).toHaveText('New Chat');
  await expect(page.locator('#chat-history .msg, #chat-history .agent-thread')).toHaveCount(0);

  const loadsBeforeCompletion = sessionLoads;
  const streamResponse = page.waitForResponse(response => new URL(response.url()).pathname === '/api/chat_stream');
  releaseStream();
  await streamResponse;
  await expect.poll(() => sessionLoads, { timeout: 5000 }).toBeGreaterThan(loadsBeforeCompletion);
  await expect.poll(() => page.evaluate(() => window.sessionModule?.getCurrentSessionId())).toBe(null);
  await expect(page.locator('#current-meta')).toHaveText('New Chat');
  await expect(page.locator('#chat-history')).not.toContainText('odysseus');
});

test('mobile sidebar New Chat clears the chat immediately', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/sessions') return route.fulfill({ json: [sessionFixture()] });
    if (url.pathname === '/api/history/session-one') {
      return route.fulfill({
        json: {
          history: [{ role: 'assistant', content: 'Mobile old chat' }],
          model: 'test/model',
          name: 'Existing chat',
          endpoint_url: 'http://model.test/v1/chat/completions',
          offset: 0,
          limit: 100,
          total: 1,
          has_more_before: false,
        },
      });
    }
    if (url.pathname === '/api/default-chat') return route.fulfill({ json: {} });
    if (url.pathname === '/api/auth/status') {
      return route.fulfill({ json: { username: 'tester', is_admin: false, privileges: {} } });
    }
    if (url.pathname === '/api/model-endpoints' || url.pathname === '/api/models') {
      return route.fulfill({ json: [] });
    }
    return route.fulfill({ json: {} });
  });

  await page.goto('/static/index.html#session-one');
  await waitForSession(page, 'session-one');
  if (await page.locator('#sidebar').evaluate(sidebar => sidebar.classList.contains('hidden'))) {
    await page.locator('#hamburger-btn').click();
  }
  await page.locator('#sidebar-new-chat-btn').click();

  await expect.poll(() => page.evaluate(() => window.sessionModule?.getCurrentSessionId())).toBe(null);
  await expect(page.locator('#current-meta')).toHaveText('New Chat');
  await expect(page.locator('#chat-history')).not.toContainText('Mobile old chat');
  await expect(page.locator('#message:visible')).toBeEditable();
});
