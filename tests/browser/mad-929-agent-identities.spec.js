// MAD-929: Agent Identities settings tab — list, edit, and per-session binding.
import { expect, test } from '@playwright/test';

const JARVIS = {
  id: 'jarvis',
  display_name: 'Jarvis',
  constitution_version: '2',
  constitution: 'Guard the operator context.',
  constitution_present: true,
  model_profile: {
    chat: { endpoint_id: 'ep-1', model: 'qwen3-32b', reasoning_level: 'medium' },
    lanes: {},
  },
};

const FRIDAY = {
  id: 'friday',
  display_name: 'Friday',
  constitution_version: '1',
  constitution: 'Report uncertainty as uncertainty.',
  constitution_present: true,
  model_profile: {
    chat: { endpoint_id: 'ep-1', model: 'qwen3-14b', reasoning_level: 'high' },
    lanes: { utility: { endpoint_id: 'ep-1', model: 'qwen3-4b' } },
  },
};

const ENDPOINT = {
  id: 'ep-1',
  name: 'Local',
  base_url: 'http://127.0.0.1:11434/v1',
  is_enabled: true,
  online: true,
  models: ['qwen3-32b', 'qwen3-14b', 'qwen3-4b'],
};

function stubApi(page, { identities = [JARVIS], isAdmin = true, onPatch } = {}) {
  const state = { identities: identities.map(entry => ({ ...entry })) };
  return page.route('**/api/**', async route => {
    const req = route.request();
    const url = new URL(req.url());
    if (url.pathname === '/api/auth/status') {
      return route.fulfill({ json: { username: 'tester', is_admin: isAdmin, privileges: {} } });
    }
    if (url.pathname === '/api/auth/settings') {
      return route.fulfill({ json: { agent_id: 'assistant', agent_constitution: 'Stay accurate.' } });
    }
    if (url.pathname === '/api/auth/identities' && req.method() === 'GET') {
      return route.fulfill({
        json: {
          identities: state.identities,
          active_id: 'jarvis',
          constitution_included: isAdmin,
        },
      });
    }
    if (url.pathname === '/api/auth/identities' && req.method() === 'POST') {
      const created = JSON.parse(req.postData() || '{}');
      created.id = created.id || 'created';
      created.constitution_present = true;
      state.identities.push(created);
      return route.fulfill({ json: created });
    }
    if (url.pathname === '/api/model-endpoints') {
      return route.fulfill({ json: [ENDPOINT] });
    }
    if (url.pathname === '/api/sessions') {
      return route.fulfill({
        json: [{
          id: 's1',
          name: 'Chat',
          model: 'qwen3-32b',
          endpoint_url: 'http://127.0.0.1:11434/v1',
          agent_target: 'jarvis',
          identity_id: state.boundId || '',
          reasoning_level: '',
        }],
      });
    }
    if (url.pathname === '/api/session/s1' && req.method() === 'PATCH') {
      if (onPatch) onPatch(req.postData() || '');
      state.boundId = 'friday';
      return route.fulfill({ json: { id: 's1', identity_id: 'friday', model: 'qwen3-14b', reasoning_level: 'high' } });
    }
    if (['/api/models', '/api/plugins', '/api/tools', '/api/selector-catalog', '/api/model-endpoints'].includes(url.pathname)) {
      return route.fulfill({ json: [] });
    }
    return route.fulfill({ json: {} });
  });
}

test('Agent Identities tab lists saved identities and binds one to the session', async ({ page }) => {
  let patched = '';
  await stubApi(page, {
    identities: [JARVIS, FRIDAY],
    onPatch: body => { patched = body; },
  });

  await page.goto('/static/index.html');
  await page.evaluate(async () => (await import('/static/js/settings.js')).open('identities'));
  await page.waitForFunction(() => (window.sessionModule?.getSessions?.() || []).length > 0);
  await page.evaluate(() => window.sessionModule.setCurrentSessionId('s1'));

  const panel = page.locator('#settings-modal [data-settings-panel="identities"]');
  await expect(panel).not.toHaveClass(/hidden/);
  await expect(page.locator('#set-identityCount')).toHaveText('2 identities');
  await expect(panel.locator('[data-identity-id="jarvis"]')).toContainText('Jarvis');
  await expect(panel.locator('[data-identity-id="jarvis"]')).toContainText('Installation default');
  await expect(panel.locator('[data-identity-id="friday"]')).toContainText('Friday');
  await expect(panel.locator('[data-identity-id="friday"]')).toContainText('Utility override');

  await panel.locator('[data-identity-id="friday"]').getByRole('button', { name: 'Use for this session' }).click();

  await expect.poll(() => patched).toContain('friday');
  await expect(panel.locator('[data-identity-id="friday"]')).toContainText('In this session');
});

test('admin can create a second identity with an attached model profile', async ({ page }) => {
  let createdPayload = null;
  await stubApi(page, { identities: [JARVIS] });
  await page.route('**/api/auth/identities', async route => {
    const req = route.request();
    if (req.method() !== 'POST') return route.fallback();
    createdPayload = JSON.parse(req.postData() || '{}');
    createdPayload.id = createdPayload.id || 'friday';
    createdPayload.constitution_present = true;
    return route.fulfill({ json: createdPayload });
  });

  await page.goto('/static/index.html');
  await page.evaluate(async () => (await import('/static/js/settings.js')).open('identities'));

  await page.locator('#set-identityNew').click();
  await expect(page.locator('#set-identityEditorCard')).not.toHaveClass(/hidden/);

  await page.locator('#set-identityDisplayName').fill('Friday');
  await page.locator('#set-identityId').fill('friday');
  await page.locator('#set-identityVersion').fill('1');
  await page.locator('#set-identityConstitution').fill('Report uncertainty as uncertainty.');
  await page.locator('#set-identityChatEndpoint').selectOption('ep-1');
  await page.locator('#set-identityChatModel').selectOption('qwen3-14b');
  await page.locator('#set-identityReasoning').selectOption('high');
  await page.locator('#set-identitySave').click();

  await expect.poll(() => createdPayload && createdPayload.display_name).toBe('Friday');
  expect(createdPayload.constitution).toBe('Report uncertainty as uncertainty.');
  expect(createdPayload.model_profile.chat).toEqual({
    endpoint_id: 'ep-1',
    model: 'qwen3-14b',
    reasoning_level: 'high',
  });
  expect(createdPayload.model_profile.lanes.utility).toBe(null);
});

test('non-admin sees the list and session binding without editor controls', async ({ page }) => {
  await stubApi(page, { identities: [JARVIS], isAdmin: false });

  await page.goto('/static/index.html');
  await page.evaluate(async () => (await import('/static/js/settings.js')).open('identities'));

  const panel = page.locator('#settings-modal [data-settings-panel="identities"]');
  await expect(panel.locator('[data-identity-id="jarvis"]')).toContainText('Jarvis');
  await expect(page.locator('#set-identityNew')).toBeHidden();
  await expect(panel.getByRole('button', { name: 'Edit' })).toHaveCount(0);
  await expect(panel.getByRole('button', { name: 'Use for this session' })).toHaveCount(1);
});
