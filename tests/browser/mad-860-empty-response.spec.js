import { expect, test } from '@playwright/test';

// MAD-860: one failed request creates one chronological actionable result.
// A zero-content model turn renders a single diagnostic card with an explicit
// Retry, and a later turn replaces it instead of stacking generic cards.

function session(id) {
  return {
    id,
    name: 'Diagnostic session',
    model: 'gpt-4o-mini',
    endpoint_url: 'http://model.test/v1/chat/completions',
    message_count: 0,
    archived: false,
    created_at: '2026-09-13T20:00:00Z',
    updated_at: '2026-09-13T20:01:00Z',
    last_message_at: '2026-09-13T20:01:00Z',
  };
}

const DIAGNOSTIC = {
  type: 'model_response_diagnostic',
  category: 'zero_content_completion',
  action: 'retry',
  guidance: 'Retry — the model finished without any content. Send the request again.',
  request_id: 'req-browser-1',
  provider: 'openrouter',
  model: 'gpt-4o-mini',
  redacted: true,
};

async function installRoutes(page, state) {
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/sessions') {
      return route.fulfill({ json: [session('session-one')] });
    }
    if (url.pathname === '/api/history/session-one') {
      return route.fulfill({
        json: {
          history: [], model: 'gpt-4o-mini', name: 'Diagnostic session',
          endpoint_url: 'http://model.test/v1/chat/completions',
          offset: 0, limit: 100, total: 0, has_more_before: false,
        },
      });
    }
    if (url.pathname === '/api/default-chat') {
      return route.fulfill({
        json: {
          endpoint_id: 'endpoint-one',
          endpoint_url: 'http://model.test/v1/chat/completions',
          model: 'gpt-4o-mini',
        },
      });
    }
    if (url.pathname === '/api/chat_stream') {
      state.streams += 1;
      const body = state.streams === 1
        ? 'data: {"delta":"The model returned an empty response. Retry — the model finished without any content."}\n\n'
          + `data: ${JSON.stringify(DIAGNOSTIC)}\n\n`
          + 'data: {"type":"metrics","data":{"model":"gpt-4o-mini","tool_events":[]}}\n\n'
          + 'data: {"type":"message_saved","id":"assistant-one"}\n\n'
          + 'data: [DONE]\n\n'
        : 'data: {"delta":"Recovered answer."}\n\n'
          + 'data: {"type":"metrics","data":{"model":"gpt-4o-mini","tool_events":[]}}\n\n'
          + 'data: {"type":"message_saved","id":"assistant-two"}\n\n'
          + 'data: [DONE]\n\n';
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body,
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
}

async function waitForSession(page) {
  await expect.poll(() => page.evaluate(() => typeof document.querySelector('#chat-form')?.onsubmit === 'function')).toBe(true);
  const current = await page.evaluate(() => window.sessionModule?.getCurrentSessionId());
  if (current !== 'session-one') {
    await page.evaluate(() => window.sessionModule.selectSession('session-one', { showLoading: false }));
  }
  await expect.poll(() => page.evaluate(() => window.sessionModule?.getCurrentSessionId())).toBe('session-one');
}

test('a zero-content turn shows one actionable diagnostic card with Retry', async ({ page }) => {
  const state = { streams: 0 };
  await installRoutes(page, state);
  await page.goto('/static/index.html#session-one');
  await waitForSession(page);
  await page.evaluate(() => window.sessionModule.selectSession('session-one', { showLoading: false }));
  await waitForSession(page);

  await page.locator('#message:visible').fill('Say something');
  await page.locator('.send-btn:visible').click();
  await expect.poll(() => state.streams).toBe(1);

  const card = page.locator('.model-response-diagnostic');
  await expect(card).toHaveCount(1);
  await expect(card).toContainText('Retry');
  await expect(card).not.toContainText('zero_content_completion');
  await expect(card.getByRole('button', { name: /Retry/ })).toBeVisible();

  await card.getByRole('button', { name: /Retry/ }).click();
  await expect.poll(() => state.streams).toBe(2);
  await expect(page.locator('.model-response-diagnostic')).toHaveCount(0);
  await expect(page.locator('#chat-history')).toContainText('Recovered answer.');
});
