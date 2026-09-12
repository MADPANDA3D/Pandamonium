// MAD-892: protocol layer operator controls in Settings → AI.
import { expect, test } from '@playwright/test';

test('protocol controls load, toggle, save, and surface a malformed pack', async ({ page }) => {
  const puts = [];
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/auth/status') {
      return route.fulfill({ json: { username: 'leo', is_admin: true, privileges: {} } });
    }
    if (url.pathname === '/api/auth/settings' && route.request().method() === 'GET') {
      return route.fulfill({
        json: {
          agent_id: 'assistant',
          agent_display_name: 'Assistant',
          agent_constitution_version: '1',
          agent_constitution: 'Stay accurate.',
          protocol_layer_enabled: true,
          disabled_protocol_packs: ['jos-p6-learning'],
          model_context_windows: { 'deepseek/deepseek-v4.1-flash': 1048576 },
          model_input_token_budgets: {},
        },
      });
    }
    if (url.pathname === '/api/auth/settings' && route.request().method() === 'POST') {
      puts.push(JSON.parse(route.request().postData() || '{}'));
      return route.fulfill({ json: { ok: true } });
    }
    if (url.pathname === '/api/diagnostics/protocol/packs') {
      return route.fulfill({
        json: {
          status: 'degraded',
          packs: [
            {
              id: 'jos-p0-engine',
              protocol: 'JOS-P0',
              title: 'Engine compatibility and system ownership',
              version: '0.2',
              token_budget: 160,
              status: 'loaded',
              enabled: true,
            },
            {
              id: 'jos-p6-learning',
              protocol: 'JOS-P6',
              title: 'Learning and promotion',
              version: '0.1',
              token_budget: 90,
              status: 'loaded',
              enabled: false,
            },
            {
              id: 'broken-name',
              protocol: '',
              title: 'broken-name',
              version: '',
              token_budget: 0,
              status: 'error',
              enabled: true,
              error: 'missing required manifest key(s): version',
            },
          ],
        },
      });
    }
    if (
      ['/api/sessions', '/api/model-endpoints', '/api/models', '/api/plugins'].includes(
        url.pathname
      )
    ) {
      return route.fulfill({ json: [] });
    }
    return route.fulfill({ json: {} });
  });

  await page.goto('/static/index.html');
  await page.evaluate(async () => (await import('/static/js/settings.js')).open());
  await expect(page.locator('#settings-modal')).toBeVisible();
  await page.locator('#settings-modal [data-settings-tab="ai"]').click();
  await expect(page.locator('#set-protocolStatus')).toHaveText(/Ready|Degraded/);

  await expect(page.locator('#set-protocolLayerEnabled')).toBeChecked();
  const p0 = page.locator('#set-protocolPackList input[data-pack-id="jos-p0-engine"]');
  const p6 = page.locator('#set-protocolPackList input[data-pack-id="jos-p6-learning"]');
  await expect(p0).toBeChecked();
  await expect(p6).not.toBeChecked();
  await expect(page.locator('#set-protocolPackList')).toContainText('invalid pack');
  await expect(page.locator('#set-modelContextWindows')).toHaveValue(/deepseek/);

  await p6.check();
  await page.locator('#set-protocolLayerEnabled').uncheck();
  // Let the docked-modal layout settle before the pointer lands on the button.
  await page.waitForTimeout(400);
  await page.locator('#set-protocolSave').click();
  await expect.poll(() => puts.length).toBeGreaterThan(0);
  const payload = puts[puts.length - 1];
  expect(payload.protocol_layer_enabled).toBe(false);
  expect(payload.disabled_protocol_packs).toEqual([]);

  // Malformed JSON is rejected locally without a POST.
  await page.locator('#set-modelContextWindows').fill('not json');
  const before = puts.length;
  await page.locator('#set-protocolSave').click();
  await expect(page.locator('#set-protocolMsg')).toContainText('JSON');
  expect(puts.length).toBe(before);
});
