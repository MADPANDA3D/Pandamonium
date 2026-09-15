import { expect, test } from '@playwright/test';

const DEEPSEEK = 'deepseek/deepseek-v4.1-flash';

const endpoint = {
  host: 'custom',
  port: 0,
  url: 'https://openrouter.ai/api/v1',
  models: [DEEPSEEK],
  models_display: ['DeepSeek V4.1 Flash'],
  models_extra: [],
  models_extra_display: [],
  endpoint_id: 'ep-openrouter',
  endpoint_name: 'OpenRouter',
  category: 'custom',
  endpoint_kind: 'openai_compatible',
  model_type: 'llm',
  reasoning_levels: { [DEEPSEEK]: ['max', 'xhigh', 'high', 'medium', 'low'] },
};

async function mockApp(page) {
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/models') return route.fulfill({ json: { items: [endpoint] } });
    if (path === '/api/model-endpoints') return route.fulfill({ json: { items: [] } });
    if (path === '/api/sessions') return route.fulfill({
      json: [{
        id: 'session-1', name: 'DeepSeek chat', folder: 'General',
        agent_target: 'jarvis', model: DEEPSEEK, endpoint_url: endpoint.url,
      }],
    });
    if (path === '/api/default-chat') return route.fulfill({ json: { endpoint_url: endpoint.url, model: DEEPSEEK } });
    if (path === '/api/auth/status') return route.fulfill({ json: { username: 'tester', is_admin: true, privileges: {} } });
    return route.fulfill({ json: {} });
  });
}

test('composer shows capability reasoning tiers and icon-sized logos', async ({ page }) => {
  await mockApp(page);
  await page.goto('/static/index.html');

  const effort = page.locator('#composer-effort-btn');
  await expect(effort).toBeVisible();

  const logo = page.locator('#identity-model-logo svg');
  await expect(logo).toBeVisible();
  const box = await logo.boundingBox();
  expect(box.width).toBeLessThanOrEqual(16);
  expect(box.height).toBeLessThanOrEqual(16);

  await expect(page.locator('#composer-effort-value')).toHaveText('Model default');

  const radius = await effort.evaluate(el => getComputedStyle(el).borderRadius);
  expect(radius).toBe('999px');
  const identityRadius = await page.locator('#model-picker-btn').evaluate(el => getComputedStyle(el).borderRadius);
  expect(identityRadius).toBe('999px');

  await effort.click();
  await expect(page.locator('#conversation-effort-card')).toBeVisible();
  await expect(page.locator('#conversation-effort')).toHaveJSProperty('max', '4');
  await page.locator('#conversation-effort').evaluate(el => {
    el.value = '4';
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await expect(page.locator('#composer-effort-value')).toHaveText('Ultra');
});