// MAD-928: the protocol layer panel and raw JSON editors are removed from
// Settings → AI. Runtime protocol mounting is unchanged (Python suites).
import { expect, test } from '@playwright/test';

test('AI tab keeps model defaults but renders no protocol controls; identities have their own tab', async ({ page }) => {
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
          model_context_windows: { 'neutral/model': 1048576 },
          model_input_token_budgets: {},
        },
      });
    }
    if (url.pathname === '/api/auth/settings' && route.request().method() === 'POST') {
      return route.fulfill({ json: { ok: true } });
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

  await expect(page.locator('#set-defaultEpSelect')).toBeAttached();

  await expect(page.locator('#set-protocolCard')).toHaveCount(0);
  await expect(page.locator('#set-protocolLayerEnabled')).toHaveCount(0);
  await expect(page.locator('#set-protocolPackList')).toHaveCount(0);
  await expect(page.locator('#set-modelContextWindows')).toHaveCount(0);
  await expect(page.locator('#set-modelInputTokenBudgets')).toHaveCount(0);
  await expect(page.locator('#set-protocolSave')).toHaveCount(0);

  // No protocol pack request is fired from Settings anymore.
  const protocolPacksRequests = [];
  page.on('request', request => {
    if (request.url().includes('/api/diagnostics/protocol/packs')) {
      protocolPacksRequests.push(request.url());
    }
  });
  await page.locator('#settings-modal [data-settings-tab="added-models"]').click();
  await page.locator('#settings-modal [data-settings-tab="ai"]').click();
  await page.locator('#settings-modal [data-settings-tab="identities"]').click();
  await expect(page.locator('#set-identityList')).toBeVisible();
  expect(protocolPacksRequests).toEqual([]);
});
