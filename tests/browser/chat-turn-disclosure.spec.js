import { expect, test } from '@playwright/test';

const VIEWPORTS = [
  ['desktop', { width: 1440, height: 900 }],
  ['mobile', { width: 390, height: 844 }],
];

function session(id, name) {
  return {
    id,
    name,
    model: 'test/model',
    endpoint_url: 'http://model.test/v1/chat/completions',
    message_count: id === 'session-one' ? 2 : 0,
    archived: false,
    created_at: '2026-09-07T20:00:00Z',
    updated_at: '2026-09-07T20:01:00Z',
    last_message_at: '2026-09-07T20:01:00Z',
  };
}

const toolEvents = [
  { round: 1, tool: 'search_memory', command: '{"query":"identity"}', output: 'one match', exit_code: 0 },
  {
    round: 2,
    tool: 'manage_mcp',
    command: '{"action":"call"}',
    output: 'approval required',
    exit_code: 1,
    action_result: { status: 'denied' },
    authority_decision: { decision: 'approval_required' },
  },
];

const roundTexts = [
  '<think>Line one\nLine two\nLine three\nLine four\nLine five\nLine six\nLine seven</think>\nI will inspect the saved identity.',
  '<think>The first result requires a second check.</think>\nI found the connection boundary.',
  'The response is consolidated.',
];

async function installRoutes(page, { stream = false } = {}) {
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/sessions') {
      return route.fulfill({ json: [
        session('session-one', 'Fragmented session'),
        session('session-two', 'Empty session'),
        session('session-three', 'Interrupted session'),
      ] });
    }
    if (url.pathname === '/api/history/session-one') {
      return route.fulfill({
        json: {
          history: stream ? [] : [
            {
              role: 'user',
              content: 'I should have clarified this long request.\n\nKeep this second paragraph as ordinary user text.',
              metadata: {},
            },
            {
              role: 'assistant',
              content: 'I will inspect the saved identity.\n\nI found the connection boundary.\n\nThe response is consolidated.',
              metadata: {
                model: 'test/model',
                round_texts: roundTexts,
                tool_events: toolEvents,
                rounds_exhausted: 20,
                tool_budget_exceeded: { used: 2, limit: 2 },
              },
            },
          ],
          model: 'test/model',
          name: 'Fragmented session',
          endpoint_url: 'http://model.test/v1/chat/completions',
          offset: 0,
          limit: 100,
          total: stream ? 0 : 2,
          has_more_before: false,
        },
      });
    }
    if (url.pathname === '/api/history/session-two') {
      return route.fulfill({
        json: {
          history: [], model: 'test/model', name: 'Empty session',
          endpoint_url: 'http://model.test/v1/chat/completions', offset: 0,
          limit: 100, total: 0, has_more_before: false,
        },
      });
    }
    if (url.pathname === '/api/history/session-three') {
      return route.fulfill({
        json: {
          history: [{
            role: 'assistant',
            content: 'The tool call completed, but the model returned no final answer. Please retry the request.',
            metadata: {
              model: 'test/model',
              round_texts: ['<think>Line one\nLine two\nLine three\nLine four\nLine five\nLine six\nLine seven</think>'],
              tool_events: [{ round: 1, tool: 'manage_mcp', output: 'failed', exit_code: 1 }],
              stopped: true,
            },
          }],
          model: 'test/model', name: 'Interrupted session',
          endpoint_url: 'http://model.test/v1/chat/completions', offset: 0,
          limit: 100, total: 1, has_more_before: false,
        },
      });
    }
    if (url.pathname === '/api/chat_stream' && stream) {
      const metrics = {
        model: 'test/model',
        requested_model: 'test/model',
        response_time: 1,
        input_tokens: 10,
        output_tokens: 20,
        total_tokens: 30,
        context_length: 4096,
        context_percent: 1,
        tokens_per_second: 20,
        round_texts: roundTexts,
        tool_events: toolEvents,
        rounds_exhausted: 20,
      };
      const approval = {
        decision_id: 'approval-one',
        decision: 'approval_required',
        capability: { name: 'manage_mcp', target: 'portal' },
        preview: { action: 'call' },
      };
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: 'data: {"type":"model_info","model":"test/model","requested_model":"test/model"}\n\n'
          + 'data: {"delta":"Line one\\nLine two\\nLine three\\nLine four\\nLine five\\nLine six\\nLine seven","thinking":true}\n\n'
          + 'data: {"delta":"I will inspect the saved identity."}\n\n'
          + 'data: {"type":"tool_start","tool":"search_memory","command":"{\\"query\\":\\"identity\\"}"}\n\n'
          + 'data: {"type":"tool_output","tool":"search_memory","output":"one match","exit_code":0}\n\n'
          + 'data: {"type":"agent_step","round":2}\n\n'
          + 'data: {"delta":"The first result requires a second check.","thinking":true}\n\n'
          + 'data: {"delta":"I found the connection boundary."}\n\n'
          + 'data: {"type":"tool_start","tool":"manage_mcp","command":"{\\"action\\":\\"call\\"}"}\n\n'
          + 'data: {"type":"tool_output","tool":"manage_mcp","output":"approval required","exit_code":1}\n\n'
          + `data: ${JSON.stringify({ type: 'authority_approval_required', data: approval })}\n\n`
          + 'data: {"type":"agent_step","round":3}\n\n'
          + 'data: {"delta":"The response is consolidated."}\n\n'
          + 'data: {"type":"rounds_exhausted","rounds":20}\n\n'
          + `data: ${JSON.stringify({ type: 'metrics', data: metrics })}\n\n`
          + 'data: {"type":"message_saved","id":"assistant-one"}\n\n'
          + 'data: [DONE]\n\n',
      });
    }
    if (url.pathname === '/api/auth/status') {
      return route.fulfill({ json: { username: 'tester', is_admin: false, privileges: {} } });
    }
    if (url.pathname === '/api/default-chat') {
      return route.fulfill({
        json: { endpoint_id: 'endpoint-one', endpoint_url: 'http://model.test/v1/chat/completions', model: 'test/model' },
      });
    }
    if (url.pathname === '/api/model-endpoints' || url.pathname === '/api/models') return route.fulfill({ json: [] });
    if (url.pathname === '/api/authority') return route.fulfill({ json: { decisions: [] } });
    return route.fulfill({ json: {} });
  });
}

async function waitForSession(page, sessionId = 'session-one') {
  await expect.poll(() => page.evaluate(() => window.sessionModule?.getCurrentSessionId())).toBe(sessionId);
  await expect.poll(() => page.evaluate(() => typeof document.querySelector('#chat-form')?.onsubmit === 'function')).toBe(true);
}

for (const [viewportName, viewport] of VIEWPORTS) {
  test(`${viewportName}: persisted turn hydrates into one reopenable assistant response`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await installRoutes(page);
    await page.goto('/static/index.html#session-one');
    await waitForSession(page);

    await expect(page.locator('#chat-history > .msg-user')).toHaveCount(1);
    await expect(page.locator('#chat-history > .msg-user .thinking-section')).toHaveCount(0);
    await expect(page.locator('#chat-history > .msg-ai')).toHaveCount(1);
    await expect(page.locator('#chat-history > .agent-thread, #chat-history > .msg-continuation')).toHaveCount(0);

    const assistant = page.locator('#chat-history > .msg-ai');
    const disclosure = assistant.locator('.assistant-turn-disclosure');
    const button = disclosure.locator('.thinking-header');
    await expect(disclosure).toHaveCount(1);
    await expect(button).toHaveAttribute('aria-expanded', 'false');
    await expect(disclosure.locator('.thinking-content')).not.toHaveClass(/expanded/);
    await expect(disclosure.locator('.agent-thread-node')).toHaveCount(2);
    await expect(disclosure).toContainText('Line seven');
    await expect(disclosure).toContainText('approval required');
    await expect(disclosure).toContainText('Reached the 20-step limit');
    await expect(disclosure).toContainText('Tool budget reached');
    await expect(assistant.locator('.assistant-turn-final')).toHaveText('The response is consolidated.');

    await button.focus();
    await page.keyboard.press('Enter');
    await expect(button).toHaveAttribute('aria-expanded', 'true');
    await expect(disclosure.locator('.thinking-content')).toHaveClass(/expanded/);
    if (process.env.MAD841_CAPTURE_PROOF) {
      await page.screenshot({
        path: `docs/images/chat-turn-disclosure-${viewportName}.png`,
        fullPage: false,
      });
    }
    await page.keyboard.press('Space');
    await expect(button).toHaveAttribute('aria-expanded', 'false');

    await page.evaluate(() => window.sessionModule.selectSession('session-two', { showLoading: false }));
    await expect.poll(() => page.evaluate(() => window.sessionModule?.getCurrentSessionId())).toBe('session-two');
    await page.evaluate(() => window.sessionModule.selectSession('session-one', { showLoading: false }));
    await waitForSession(page);
    const reopened = page.locator('#chat-history > .msg-ai .assistant-turn-disclosure');
    await expect(reopened).toHaveCount(1);
    const reopenedButton = reopened.locator('.thinking-header');
    await reopenedButton.focus();
    await page.keyboard.press('Enter');
    await expect(reopenedButton).toHaveAttribute('aria-expanded', 'true');

    await page.evaluate(() => window.sessionModule.selectSession('session-three', { showLoading: false }));
    await expect.poll(() => page.evaluate(() => window.sessionModule?.getCurrentSessionId())).toBe('session-three');
    const interrupted = page.locator('#chat-history > .msg-ai');
    await expect(interrupted).toHaveCount(1);
    await expect(interrupted.locator('.assistant-turn-disclosure')).toContainText('Line seven');
    await expect(interrupted.locator('.assistant-turn-final')).toHaveText(
      'The tool call completed, but the model returned no final answer. Please retry the request.',
    );
    await expect(interrupted).toContainText('[Message interrupted]');
  });

  test(`${viewportName}: live multi-tool stream settles into the persisted grouping contract`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await installRoutes(page, { stream: true });
    await page.goto('/static/index.html#session-one');
    await waitForSession(page);
    // Re-select after startup so a late same-session history render cannot
    // clear the composer between readiness and submit.
    await page.evaluate(() => window.sessionModule.selectSession('session-one', { showLoading: false }));
    await waitForSession(page);
    await page.locator('#message:visible').fill('Inspect this with multiple tools');
    await page.evaluate(() => window._updateSendBtnIcon?.());
    await expect(page.locator('.send-btn:visible')).toHaveAttribute('data-mode', 'send');
    const streamResponse = page.waitForResponse(response => new URL(response.url()).pathname === '/api/chat_stream');
    await page.locator('.send-btn:visible').click();
    await streamResponse;
    await expect(page.locator('.send-btn:visible')).not.toHaveAttribute('data-mode', 'streaming');

    await expect(page.locator('#chat-history > .msg-ai')).toHaveCount(1);
    await expect(page.locator('#chat-history > .agent-thread, #chat-history > .msg-continuation, #chat-history > .agent-thinking-dots')).toHaveCount(0);
    const assistant = page.locator('#chat-history > .msg-ai');
    const disclosure = assistant.locator('.assistant-turn-disclosure');
    await expect(disclosure).toHaveCount(1);
    await expect(disclosure.locator('.agent-thread-node')).toHaveCount(2);
    await expect(disclosure).toContainText('Line seven');
    await expect(disclosure).toContainText('Reached the 20-step limit');
    await expect(assistant.locator('.assistant-turn-final')).toHaveText('The response is consolidated.');
    await expect(disclosure.getByRole('button', { name: 'View thinking process' })).toHaveAttribute('aria-expanded', 'false');
    await expect(page.locator('.authority-approval-card')).toContainText('Approval required: manage_mcp');
  });
}
