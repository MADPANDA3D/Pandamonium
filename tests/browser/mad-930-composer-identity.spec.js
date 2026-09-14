// MAD-930: composer identity selector, identity-default model chip, and real
// reasoning level. All API calls are stubbed; the product code must use the
// real MAD-929 endpoints (/api/auth/identities, session PATCH identity_id).
import { expect, test } from '@playwright/test';

const JARVIS = {
  id: 'jarvis',
  display_name: 'Jarvis',
  constitution_version: '1',
  constitution_present: true,
  model_profile: {
    chat: { endpoint_id: 'ep-local', model: 'qwen3-32b', reasoning_level: 'medium' },
    lanes: {},
  },
};

const FRIDAY = {
  id: 'friday',
  display_name: 'Friday',
  constitution_version: '1',
  constitution_present: true,
  model_profile: {
    chat: { endpoint_id: 'ep-or', model: 'deepseek/deepseek-v4.1-flash', reasoning_level: 'high' },
    lanes: {},
  },
};

const MODEL_ITEMS = [
  {
    url: 'http://local.test/v1',
    endpoint_id: 'ep-local',
    endpoint_name: 'Local',
    model_type: 'llm',
    models: ['qwen3-32b'],
    models_display: ['Qwen 32B'],
    models_extra: [],
    models_extra_display: [],
    offline: false,
    reasoning_levels: {},
  },
  {
    url: 'https://openrouter.ai/api/v1',
    endpoint_id: 'ep-or',
    endpoint_name: 'OpenRouter',
    model_type: 'llm',
    models: ['deepseek/deepseek-v4.1-flash'],
    models_display: ['DeepSeek V4.1 Flash'],
    models_extra: [],
    models_extra_display: [],
    offline: false,
    reasoning_levels: { 'deepseek/deepseek-v4.1-flash': ['low', 'medium', 'high'] },
  },
];

function selectorCatalog() {
  return {
    discovery: {
      schema_version: 'pandamonium.discovery.v1',
      generated_at: '2026-09-14T12:00:00Z',
      entities: [{
        kind: 'agent',
        id: 'agent:jarvis',
        display_name: 'Jarvis',
        availability: 'available',
        ownership: { scope: 'installation', id: 'installation:current' },
        health: { state: 'healthy' },
        permissions: { requires_authenticated_request: true, configured_scopes: ['owner:current'], delegation: 'narrower_only' },
        source: { type: 'configuration', ref: 'tests/fixtures.py#selector' },
        actions: [],
      }],
    },
    selections: [{
      entity_id: 'agent:jarvis',
      kind: 'agent',
      target: 'jarvis',
      capabilities: ['model'],
      selectable: true,
      reason: null,
    }],
  };
}

async function mockApp(page, { onPatch } = {}) {
  const now = '2026-09-14T12:00:00Z';
  await page.route('**/api/**', async route => {
    const req = route.request();
    const url = new URL(req.url());
    if (url.pathname === '/api/auth/status') {
      return route.fulfill({ json: { username: 'leo', is_admin: true, privileges: {} } });
    }
    if (url.pathname === '/api/auth/identities' && req.method() === 'GET') {
      return route.fulfill({
        json: { identities: [JARVIS, FRIDAY], active_id: 'jarvis', constitution_included: true },
      });
    }
    if (url.pathname === '/api/selector-catalog') {
      return route.fulfill({ json: selectorCatalog() });
    }
    if (url.pathname === '/api/sessions') {
      return route.fulfill({
        json: [{
          id: 'chat-1', name: 'Chat', model: 'qwen3-32b', endpoint_url: 'http://local.test/v1',
          agent_target: 'jarvis', identity_id: '', reasoning_level: '',
          created_at: now, updated_at: now, message_count: 1,
        }],
      });
    }
    if (url.pathname === '/api/session/chat-1' && req.method() === 'PATCH') {
      const body = req.postData() || '';
      if (onPatch) onPatch(body);
      if (body.includes('name="identity_id"') && body.includes('friday')) {
        return route.fulfill({
          json: {
            id: 'chat-1',
            identity_id: 'friday',
            model: 'deepseek/deepseek-v4.1-flash',
            endpoint_url: 'https://openrouter.ai/api/v1',
            reasoning_level: 'high',
          },
        });
      }
      return route.fulfill({ json: { id: 'chat-1', identity_id: '', model: 'qwen3-32b', endpoint_url: 'http://local.test/v1', reasoning_level: '' } });
    }
    if (url.pathname.startsWith('/api/auth/identities')) {
      if (onPatch) onPatch(`IDENTITY_MUTATION ${req.method()} ${url.pathname}`);
      return route.fulfill({ json: {} });
    }
    if (url.pathname.startsWith('/api/chat_stream')) {
      return route.fulfill({ headers: { 'Content-Type': 'text/event-stream' }, body: 'data: {"delta":"ok"}\n\ndata: [DONE]\n\n' });
    }
    if (url.pathname.startsWith('/api/history')) return route.fulfill({ json: { history: [] } });
    return route.fulfill({ json: {} });
  });
}

async function openComposer(page) {
  await page.addInitScript(() => localStorage.setItem('lastSessionId', 'chat-1'));
  await page.goto('/static/index.html');
  await expect.poll(() => page.evaluate(() => Boolean(window.sessionModule))).toBe(true);
  await page.evaluate(async () => {
    const sessions = await import('/static/js/sessions.js');
    sessions.setCurrentSessionId('chat-1');
    sessions.updateModelPicker();
  });
  await page.evaluate(() => {
    window.modelsModule.getCachedItems = () => window.__mad930Models;
  });
  await page.evaluate(async () => {
    const sessions = await import('/static/js/sessions.js');
    sessions.updateModelPicker();
  });
  await page.evaluate(() => {
    window.dispatchEvent(new Event('odysseus:session-rendered'));
    document.dispatchEvent(new Event('odysseus:model-picked'));
  });
  await expect(page.locator('#identity-model-btn')).toBeVisible();
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(items => { window.__mad930Models = items; }, MODEL_ITEMS);
});

test('identity selector binds the saved identity and loads its model and real reasoning level', async ({ page }) => {
  const patches = [];
  await mockApp(page, { onPatch: body => patches.push(body) });
  await openComposer(page);

  // The installation default identity is the initial chip label.
  await expect(page.locator('#model-picker-label')).toHaveText('Jarvis');
  await expect(page.locator('#identity-model-label')).toHaveText('Qwen 32B');
  // The local default model has no reasoning contract, so the chip stays on
  // the work-budget control until a reasoning-capable identity is bound.
  await expect(page.locator('#conversation-effort-label')).toHaveText('Agent work budget');

  await page.locator('#model-picker-btn').click();
  const identityRows = page.locator('#model-picker-list .model-switch-item');
  await expect(identityRows.filter({ hasText: 'Jarvis' })).toHaveCount(1);
  await expect(identityRows.filter({ hasText: 'Friday' })).toHaveCount(1);
  await identityRows.filter({ hasText: 'Friday' }).click();

  await expect.poll(() => patches.some(body => body.includes('identity_id') && body.includes('friday'))).toBe(true);
  await expect(page.locator('#model-picker-label')).toHaveText('Friday');
  await expect(page.locator('#identity-model-label')).toHaveText('DeepSeek V4.1 Flash');
  await expect(page.locator('#composer-effort-value')).toHaveText('High');
  await expect(page.locator('#conversation-effort-value')).toHaveText('High');
  // No identity write happened: selecting for a session never mutates the store.
  expect(patches.filter(body => body.startsWith('IDENTITY_MUTATION'))).toEqual([]);
});

test('composer model override is per session and leaves the identity default untouched', async ({ page }) => {
  const patches = [];
  await mockApp(page, { onPatch: body => patches.push(body) });
  await openComposer(page);

  await page.locator('#model-picker-btn').click();
  await page.locator('#model-picker-list .model-switch-item').filter({ hasText: 'Friday' }).click();
  await expect(page.locator('#identity-model-label')).toHaveText('DeepSeek V4.1 Flash');

  await page.locator('#identity-model-btn').click();
  const modelRows = page.locator('#model-picker-list .model-switch-item');
  await expect(modelRows.filter({ hasText: 'Qwen 32B' })).toHaveCount(1);
  await modelRows.filter({ hasText: 'Qwen 32B' }).click();

  await expect(page.locator('#identity-model-label')).toHaveText('Qwen 32B');
  await expect(page.locator('#identity-model-btn')).toHaveClass(/is-override/);
  // The override PATCHed the session model, not the saved identity.
  expect(patches.some(body => body.includes('name="model"') && body.includes('qwen3-32b'))).toBe(true);
  expect(patches.filter(body => body.startsWith('IDENTITY_MUTATION'))).toEqual([]);
});

test('identity picked for a new chat preloads its model and reasoning level', async ({ page }) => {
  const patches = [];
  await mockApp(page, { onPatch: body => patches.push(body) });
  await page.goto('/static/index.html');
  await expect.poll(() => page.evaluate(() => Boolean(window.sessionModule && window.modelsModule))).toBe(true);
  await page.evaluate(() => {
    window.modelsModule.getCachedItems = () => window.__mad930Models;
  });
  await page.evaluate(async () => {
    const sessions = await import('/static/js/sessions.js');
    sessions.setCurrentSessionId(null);
    sessions.updateModelPicker();
  });

  await expect(page.locator('#identity-model-btn')).toBeVisible();
  await page.locator('#model-picker-btn').click();
  await page.locator('#model-picker-list .model-switch-item').filter({ hasText: 'Friday' }).click();

  await expect(page.locator('#model-picker-label')).toHaveText('Friday');
  await expect(page.locator('#identity-model-label')).toHaveText('DeepSeek V4.1 Flash');
  await expect(page.locator('#composer-effort-value')).toHaveText('High');

  // No session exists yet: nothing is PATCHed, the pending chat carries the
  // identity so materialization binds it with the first message.
  expect(patches.filter(body => body.includes('identity_id'))).toEqual([]);
  const pending = await page.evaluate(async () => (await import('/static/js/sessions.js')).getPendingChat());
  expect(pending.identityId).toBe('friday');
  expect(pending.modelId).toBe('deepseek/deepseek-v4.1-flash');
  expect(pending.url).toBe('https://openrouter.ai/api/v1');

  // Overriding the model on the pending chat swaps the model but keeps the
  // identity (and its reasoning level) bound for materialization.
  await page.locator('#identity-model-btn').click();
  await page.locator('#model-picker-list .model-switch-item').filter({ hasText: 'Qwen 32B' }).click();
  await expect(page.locator('#identity-model-label')).toHaveText('Qwen 32B');
  await expect(page.locator('#identity-model-btn')).toHaveClass(/is-override/);
  await expect(page.locator('#model-picker-label')).toHaveText('Friday');
  const overridden = await page.evaluate(async () => (await import('/static/js/sessions.js')).getPendingChat());
  expect(overridden.identityId).toBe('friday');
  expect(overridden.modelId).toBe('qwen3-32b');
  expect(overridden.reasoningLevel).toBe('high');
});

test('model chip lists models while the identity chip keeps the saved identities', async ({ page }) => {
  await mockApp(page);
  await openComposer(page);

  await page.locator('#identity-model-btn').click();
  const modelRows = page.locator('#model-picker-list .model-switch-item');
  await expect(modelRows.filter({ hasText: 'Qwen 32B' })).toHaveCount(1);
  await expect(modelRows.filter({ hasText: 'DeepSeek V4.1 Flash' })).toHaveCount(1);
  await expect(modelRows).toHaveCount(2);

  await page.locator('#model-picker-btn').click();
  const identityRows = page.locator('#model-picker-list .model-switch-item');
  await expect(identityRows).toHaveCount(2);
  await expect(identityRows.filter({ hasText: 'Jarvis' })).toHaveCount(1);
  await expect(identityRows.filter({ hasText: 'Friday' })).toHaveCount(1);
});
