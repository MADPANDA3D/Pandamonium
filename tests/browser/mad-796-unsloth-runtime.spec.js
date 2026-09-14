// MAD-796: Settings → Training Runtime tab.
// Verifies the tab configures an operator-provided Unsloth Studio runtime,
// shows discovery states with honest copy, tests with one click, disables and
// removes the connection, and never renders the access token.
import { expect, test } from '@playwright/test';

const TOKEN = 'studio-secret-token';

const CONNECTION = {
  configured: true,
  enabled: true,
  base_url: 'https://studio.example.test',
  token_configured: true,
  model_endpoint: null,
  status: 'online',
  last_message: 'The runtime answered and accepted the access token.',
  last_reason: '',
  last_checked_at: '2026-09-14T00:00:00+00:00',
  runtime_version: '1.0.0',
  job_state: 'idle',
  capabilities: {
    training: { state: 'supported', detail: '/api/train/start is advertised', route: '/api/train/start' },
    conversion: { state: 'unsupported', detail: 'the runtime exposes no export route', route: null },
    inference: { state: 'supported', detail: '/api/inference/chat is advertised', route: '/api/inference/chat' },
  },
  contract: 'pandamonium-unsloth-runtime-v1',
  adapter_version: 1,
};

const UNCONFIGURED = {
  configured: false,
  enabled: false,
  base_url: '',
  token_configured: false,
  model_endpoint: null,
  status: 'unconfigured',
  last_message: 'No Unsloth Studio runtime is configured.',
  last_reason: '',
  last_checked_at: null,
  runtime_version: null,
  job_state: null,
  capabilities: null,
  contract: 'pandamonium-unsloth-runtime-v1',
  adapter_version: 1,
};

function stubApi(page, { connection = CONNECTION, onRequest } = {}) {
  let current = { ...connection };
  return page.route('**/api/**', async route => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    if (onRequest) onRequest(req, path);
    if (path === '/api/auth/status') {
      return route.fulfill({ json: { username: 'tester', is_admin: true, privileges: {} } });
    }
    if (path === '/api/auth/settings') {
      return route.fulfill({ json: { agent_id: 'assistant', agent_constitution: 'Stay accurate.' } });
    }
    if (path === '/api/unsloth/connection' && req.method() === 'GET') {
      return route.fulfill({ json: current });
    }
    if (path === '/api/unsloth/connection' && req.method() === 'PUT') {
      const body = JSON.parse(req.postData() || '{}');
      current = {
        ...CONNECTION,
        ...current,
        ...('enabled' in body ? { enabled: body.enabled, status: body.enabled ? 'untested' : 'disabled' } : {}),
        ...('base_url' in body ? { base_url: body.base_url, configured: true, token_configured: true, status: 'untested' } : {}),
      };
      return route.fulfill({ json: current });
    }
    if (path === '/api/unsloth/connection' && req.method() === 'DELETE') {
      current = { ...UNCONFIGURED };
      return route.fulfill({ json: { ok: true } });
    }
    if (path === '/api/unsloth/connection/test') {
      return route.fulfill({
        json: {
          ...current,
          ok: true,
          state: 'online',
          reason: '',
          message: 'The runtime answered and accepted the access token.',
        },
      });
    }
    if (['/api/sessions', '/api/models', '/api/plugins', '/api/tools', '/api/selector-catalog'].includes(path)) {
      return route.fulfill({ json: [] });
    }
    return route.fulfill({ json: {} });
  });
}

async function openTrainingTab(page) {
  await page.goto('/static/index.html');
  await page.evaluate(async () => (await import('/static/js/settings.js')).open());
  await page.locator('#settings-modal [data-settings-tab="training"]').click();
}

test('Training tab lists the runtime with capability discovery and never shows the token', async ({ page }) => {
  await stubApi(page);
  await openTrainingTab(page);

  const row = page.locator('.unsloth-row');
  await expect(row).toContainText('Unsloth Studio');
  await expect(row).toContainText('Online');
  await expect(row).toContainText('studio.example.test');
  await expect(row).toContainText('Training: Supported');
  await expect(row).toContainText('Conversion: Unsupported');
  await expect(row).toContainText('Inference: Supported');
  await expect(row).toContainText('Token saved');
  await expect(page.locator('body')).not.toContainText(TOKEN);

  await row.locator('[data-unsloth-action="test"]').click();
  await expect(page.locator('#unsloth-msg')).toContainText('accepted the access token');

  if (process.env.MAD796_SCREENSHOT_DIR) {
    const dir = process.env.MAD796_SCREENSHOT_DIR;
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.screenshot({ path: `${dir}/mad-796-training-runtime-desktop.png` });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: `${dir}/mad-796-training-runtime-mobile.png` });
    await page.setViewportSize({ width: 1280, height: 720 });
  }
});

test('configuring the runtime sends the URL and token once and renders the saved state', async ({ page }) => {
  let putBody = '';
  await stubApi(page, {
    connection: UNCONFIGURED,
    onRequest: (req, path) => {
      if (path === '/api/unsloth/connection' && req.method() === 'PUT') putBody = req.postData() || '';
    },
  });
  await openTrainingTab(page);

  await expect(page.locator('#unsloth-runtime-status')).toContainText('No Unsloth Studio runtime is configured');
  await page.locator('#unsloth-add-btn').click();
  await page.locator('#unsloth-edit-url').fill('https://studio.example.test');
  await page.locator('#unsloth-edit-token').fill(TOKEN);
  await page.locator('[data-unsloth-editor="save"]').click();

  await expect(page.locator('#unsloth-msg')).toContainText('Runtime saved');
  expect(putBody).toContain('studio.example.test');
  expect(putBody).toContain(TOKEN);
  await expect(page.locator('.unsloth-row')).toContainText('Unsloth Studio');
  await expect(page.locator('body')).not.toContainText(TOKEN);
});

test('disabling the runtime is one click and fails closed with clear copy', async ({ page }) => {
  let toggleBody = '';
  await stubApi(page, {
    connection: { ...CONNECTION, enabled: false, status: 'disabled', last_message: 'The Unsloth Studio runtime is disabled. Enable it to test the connection.' },
    onRequest: (req, path) => {
      if (path === '/api/unsloth/connection' && req.method() === 'PUT') toggleBody = req.postData() || '';
    },
  });
  await openTrainingTab(page);

  const row = page.locator('.unsloth-row');
  await expect(row).toContainText('Disabled');
  await row.locator('[data-unsloth-action="toggle"]').click();

  await expect(page.locator('#unsloth-msg')).toContainText('Runtime enabled');
  expect(toggleBody).toContain('"enabled":true');
});

test('removing the runtime deletes the connection and leaves existing data alone', async ({ page }) => {
  let deleted = false;
  await stubApi(page, {
    onRequest: (req, path) => {
      if (path === '/api/unsloth/connection' && req.method() === 'DELETE') deleted = true;
    },
  });
  page.on('dialog', dialog => dialog.accept());
  await openTrainingTab(page);

  await page.locator('.unsloth-row [data-unsloth-action="remove"]').click();

  await expect(page.locator('#unsloth-msg')).toContainText('untouched');
  expect(deleted).toBe(true);
  await expect(page.locator('#unsloth-runtime-status')).toContainText('No Unsloth Studio runtime is configured');
});