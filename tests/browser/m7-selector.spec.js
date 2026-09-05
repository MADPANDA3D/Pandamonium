import { expect, test } from '@playwright/test';

const modelEndpoint = {
  endpoint_id: 'endpoint-one',
  endpoint_name: 'Owner runtime',
  url: 'http://model.test/v1/chat/completions',
  model_type: 'llm',
  models: ['vendor/alpha'],
  models_display: ['Alpha'],
  models_extra: [],
  models_extra_display: [],
  offline: false,
};

function selectorCatalog() {
  const entities = [
    ['agent', 'agent:jarvis', 'Configured Jarvis', 'healthy'],
    ['agent', 'agent:gordon', 'Configured Gordon', 'unavailable'],
    ['worker', 'worker:friday', 'Configured Friday', 'healthy'],
    ['worker', 'worker:vps', 'Configured VPS Codex', 'unavailable'],
    ['model', 'model:alpha', 'Alpha', 'healthy'],
  ].map(([kind, id, display_name, state]) => ({
    kind, id, display_name,
    availability: state === 'healthy' ? 'available' : 'unavailable',
    ownership: { scope: 'installation', id: 'installation:current' },
    health: { state, ...(state === 'healthy' ? {} : { reason: 'connection_failed' }) },
    permissions: { requires_authenticated_request: true, configured_scopes: ['owner:current'], delegation: 'narrower_only' },
    source: { type: kind === 'model' ? 'runtime_discovery' : 'configuration', ref: 'tests/fixtures.py#selector' },
    actions: [],
  }));
  return {
    discovery: { schema_version: 'pandamonium.discovery.v1', generated_at: '2026-09-05T12:00:00Z', entities },
    selections: [
      { entity_id: 'agent:jarvis', kind: 'agent', target: 'jarvis', selectable: true, reason: null },
      { entity_id: 'agent:gordon', kind: 'agent', target: 'hermes', selectable: false, reason: 'connection_failed' },
      { entity_id: 'worker:friday', kind: 'worker', target: 'pc-codex', selectable: true, reason: null },
      { entity_id: 'worker:vps', kind: 'worker', target: 'vps-codex', selectable: false, reason: 'connection_failed' },
      { entity_id: 'model:alpha', kind: 'model', model_id: 'vendor/alpha', endpoint_id: 'endpoint-one', selectable: true, reason: null },
    ],
  };
}

async function mockApp(page, selectorHandler) {
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/selector-catalog') return selectorHandler(route);
    if (url.pathname === '/api/auth/status') {
      return route.fulfill({ json: { username: 'tester', is_admin: true, privileges: {} } });
    }
    if (url.pathname === '/api/models') return route.fulfill({ json: { items: [modelEndpoint] } });
    if (url.pathname === '/api/default-chat') {
      return route.fulfill({ json: { endpoint_id: 'endpoint-one', endpoint_url: modelEndpoint.url, model: 'vendor/alpha' } });
    }
    if (url.pathname === '/api/sessions' || url.pathname === '/api/model-endpoints') return route.fulfill({ json: [] });
    return route.fulfill({ json: {} });
  });
}

test('text and voice render the same typed selector data without rerouting unavailable peers', async ({ page }) => {
  await mockApp(page, route => route.fulfill({ json: selectorCatalog() }));
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/static/index.html');
  await page.locator('#model-picker-btn').click();

  const textList = page.locator('#model-picker-list');
  await expect(textList.locator('.mp-section-label')).toHaveText(['Agents', 'Workers', 'All models']);
  await expect(textList).toContainText('Configured Jarvis');
  await expect(textList).toContainText('Configured Gordon');
  await expect(textList).toContainText('Configured Friday');
  await expect(textList).toContainText('Configured VPS Codex');
  await expect(textList).toContainText('Alpha');
  await expect(textList.getByText('Configured Gordon').locator('..')).toHaveAttribute('aria-disabled', 'true');

  const voiceList = page.locator('#jarvis-agent-menu');
  await expect(voiceList).toContainText('Configured Jarvis');
  await expect(voiceList).toContainText('Configured Gordon');
  await expect(voiceList).toContainText('Configured Friday');
  await expect(voiceList).toContainText('Configured VPS Codex');
  await expect(voiceList).toContainText('Alpha');
  await expect(voiceList.getByText('Configured Gordon').locator('..')).toBeDisabled();

  await page.locator('#model-picker-search').focus();
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('Enter');
  await expect(page.locator('#model-picker-label')).toHaveText('Configured Jarvis');

  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator('#model-picker-btn').click();
  const mobile = await page.locator('#model-picker-menu').evaluate(node => node.getBoundingClientRect());
  expect(mobile.left).toBeGreaterThanOrEqual(0);
  expect(mobile.right).toBeLessThanOrEqual(390);
});

test('selector exposes loading, empty, and failure states', async ({ page }) => {
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  let mode = 'loading';
  await mockApp(page, async route => {
    if (mode === 'loading') await gate;
    if (mode === 'failure') return route.fulfill({ status: 503, json: { error: 'unavailable' } });
    return route.fulfill({
      json: {
        discovery: { schema_version: 'pandamonium.discovery.v1', generated_at: '2026-09-05T12:00:00Z', entities: [] },
        selections: [],
      },
    });
  });
  await page.goto('/static/index.html');
  await page.locator('#model-picker-btn').click();
  await expect(page.locator('#model-picker-list')).toContainText('Discovering configured models, agents, and workers');

  mode = 'empty';
  release();
  await expect(page.locator('#model-picker-list')).toContainText('No configured choices are available');

  mode = 'failure';
  await page.locator('#model-picker-refresh-btn').click();
  await expect(page.locator('#model-picker-list [role="alert"]')).toContainText('Existing choices were not rerouted');
});
