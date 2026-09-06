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

function selectorCatalog({ includeGordon = true, gordonState = 'unavailable' } = {}) {
  const entities = [
    ['agent', 'agent:jarvis', 'Configured Jarvis', 'healthy'],
    ['agent', 'agent:gordon', 'Configured Gordon', gordonState],
    ['worker', 'worker:friday', 'Configured Friday', 'healthy'],
    ['worker', 'worker:vps', 'Configured VPS Codex', 'unavailable'],
    ['model', 'model:alpha', 'Configured Jarvis', 'healthy'],
  ].filter(([, id]) => includeGordon || id !== 'agent:gordon').map(([kind, id, display_name, state]) => ({
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
      { entity_id: 'agent:jarvis', kind: 'agent', target: 'jarvis', capabilities: ['model'], selectable: true, reason: null },
      { entity_id: 'agent:gordon', kind: 'agent', target: 'hermes', capabilities: ['hermes'], selectable: gordonState === 'healthy', reason: gordonState === 'healthy' ? null : 'connection_failed' },
      { entity_id: 'worker:friday', kind: 'worker', target: 'pc-codex', capabilities: ['codex'], selectable: true, reason: null },
      { entity_id: 'worker:vps', kind: 'worker', target: 'vps-codex', capabilities: ['codex'], selectable: false, reason: 'connection_failed' },
      { entity_id: 'model:alpha', kind: 'model', model_id: 'vendor/alpha', endpoint_id: 'endpoint-one', capabilities: ['model'], selectable: true, reason: null },
    ].filter(selection => entities.some(entity => entity.id === selection.entity_id)),
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

test('text and voice render one compact identity list without duplicate models or rerouting', async ({ page }) => {
  await mockApp(page, route => route.fulfill({ json: selectorCatalog() }));
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/static/index.html');
  await page.locator('#model-picker-btn').click();

  const textList = page.locator('#model-picker-list');
  await expect(textList.locator('.mp-section-label')).toHaveCount(0);
  await expect(textList.locator('.model-switch-item')).toHaveCount(4);
  await expect(textList).toContainText('Configured Jarvis');
  await expect(textList).toContainText('Configured Gordon');
  await expect(textList).toContainText('Configured Friday');
  await expect(textList).toContainText('Configured VPS Codex');
  await expect(textList.getByText('Configured Jarvis', { exact: true })).toHaveCount(1);
  await expect(textList).toContainText('Self-hosted model');
  await expect(textList).toContainText('Hermes');
  await expect(textList).toContainText('Workstation Codex');
  await expect(textList.getByText('Configured Gordon').locator('..')).toHaveAttribute('aria-disabled', 'true');

  const voiceList = page.locator('#jarvis-agent-menu');
  await expect(voiceList.locator('.jarvis-selector-section')).toHaveCount(0);
  await expect(voiceList.locator('.jarvis-target')).toHaveCount(4);
  await expect(voiceList).toContainText('Configured Jarvis');
  await expect(voiceList).toContainText('Configured Gordon');
  await expect(voiceList).toContainText('Configured Friday');
  await expect(voiceList).toContainText('Configured VPS Codex');
  await expect(voiceList.getByText('Configured Jarvis', { exact: true })).toHaveCount(1);
  await expect(voiceList.getByText('Configured Gordon').locator('..')).toBeDisabled();

  await page.locator('#model-picker-search').focus();
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('Enter');
  await expect(page.locator('#model-picker-label')).toHaveText('Configured Jarvis');

  await page.locator('#message').first().fill('This stays visible while I keep typing.');
  await expect(page.locator('#model-picker-wrap')).toBeVisible();
  await expect(page.locator('#model-picker-wrap')).not.toHaveClass(/model-picker-autohide/);

  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload();
  await expect(page.locator('#model-picker-label')).toHaveText('Configured Jarvis');
  await page.locator('#model-picker-btn').click();
  await expect.poll(() => page.locator('#model-picker-menu').evaluate(node => {
    const bounds = node.getBoundingClientRect();
    return bounds.left >= 0 && bounds.right <= window.innerWidth;
  })).toBe(true);
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
  await expect(page.locator('#model-picker-list')).toContainText('Discovering who you can talk to');

  mode = 'empty';
  release();
  await expect(page.locator('#model-picker-list')).toContainText('No configured identities are available');

  mode = 'failure';
  await page.locator('#model-picker-refresh-btn').click();
  await expect(page.locator('#model-picker-list [role="alert"]')).toContainText('Existing choices were not rerouted');
});

test('a selected identity that disappears stays explicit and is never rerouted', async ({ page }) => {
  let includeGordon = true;
  await mockApp(page, route => route.fulfill({
    json: selectorCatalog({ includeGordon, gordonState: 'healthy' }),
  }));
  await page.goto('/static/index.html');
  await page.locator('#model-picker-btn').click();
  await page.locator('#model-picker-list').getByText('Configured Gordon', { exact: true }).click();
  await expect(page.locator('#model-picker-label')).toHaveText('Configured Gordon');

  includeGordon = false;
  await page.locator('#model-picker-btn').click();
  const unavailable = page.locator('#model-picker-list').getByText('Configured Gordon', { exact: true }).locator('..');
  await expect(unavailable).toHaveAttribute('aria-disabled', 'true');
  await expect(unavailable).toContainText('Unavailable · no longer configured');
  await expect(page.locator('#model-picker-label')).toHaveText('Configured Gordon');
  await expect(page.locator('#model-picker-label')).toHaveAttribute('title', 'Configured Gordon: no longer configured');
});
