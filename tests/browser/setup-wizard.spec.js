import { expect, test } from '@playwright/test';


function silentWav() {
  const sampleRate = 8000;
  const samples = 800;
  const dataSize = samples * 2;
  const buffer = Buffer.alloc(44 + dataSize);
  buffer.write('RIFF', 0);
  buffer.writeUInt32LE(36 + dataSize, 4);
  buffer.write('WAVE', 8);
  buffer.write('fmt ', 12);
  buffer.writeUInt32LE(16, 16);
  buffer.writeUInt16LE(1, 20);
  buffer.writeUInt16LE(1, 22);
  buffer.writeUInt32LE(sampleRate, 24);
  buffer.writeUInt32LE(sampleRate * 2, 28);
  buffer.writeUInt16LE(2, 32);
  buffer.writeUInt16LE(16, 34);
  buffer.write('data', 36);
  buffer.writeUInt32LE(dataSize, 40);
  return buffer;
}


async function stubApis(page, state) {
  await page.route('**/api/**', route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === '/api/auth/status') {
      return route.fulfill({
        json: {
          username: 'tester',
          is_admin: state.isAdmin,
          privileges: {},
          agent_identity: {
            source: state.identityConfigured ? 'configured' : 'default',
            display_name: state.identityConfigured ? 'Friday' : 'Assistant',
            status: 'healthy',
          },
        },
      });
    }
    if (path === '/api/setup/status') {
      return route.fulfill({
        json: {
          is_admin: state.isAdmin,
          identity: {
            configured: state.identityConfigured,
            display_name: state.identityConfigured ? 'Friday' : 'Assistant',
            status: 'healthy',
          },
          model: { usable: false, endpoints: 0, models: 0 },
          voice: {
            ready: !!state.voiceReady,
            enabled: true,
            provider: state.voiceReady ? 'local' : 'disabled',
          },
          integrations: {
            configured: state.servicesConfigured || 0,
            portal_connected: !!state.portalConnected,
          },
          extensions: { installed: 0, enabled: 0 },
          update: state.isAdmin
            ? { version: '1.0.55', state: 'idle', target_version: null, rollback_available: false }
            : null,
        },
      });
    }
    if (path === '/api/voice/status') {
      return route.fulfill({
        json: {
          assistant: 'Friday',
          model_override: state.voiceReady ? 'model-a' : null,
          stt: { available: !!state.voiceReady, provider: state.voiceReady ? 'local' : 'disabled' },
          tts: {
            available: !!state.voiceReady,
            provider: state.voiceReady ? 'local' : 'disabled',
            voice: state.voiceReady ? 'Friday' : '',
            speed: 1,
          },
          setup: {
            core_ready: !!state.voiceReady,
            guidance: state.voiceReady
              ? ['Core voice setup is ready.']
              : ['Voice setup needs attention.', 'Configure an available chat model for Voice Orb.'],
            text: state.voiceReady
              ? 'Core voice setup is ready.'
              : 'Voice setup needs attention. Configure an available chat model for Voice Orb.',
          },
        },
      });
    }
    if (path === '/api/tts/synthesize' && request.method() === 'POST') {
      state.ttsPosts = state.ttsPosts || [];
      state.ttsPosts.push(request.postData() || '');
      return route.fulfill({ status: 200, contentType: 'audio/wav', body: silentWav() });
    }
    if (path === '/api/mcp/portal/status') {
      return route.fulfill({
        json: {
          configured: !!state.portalConnected,
          status: state.portalConnected ? 'connected' : 'disconnected',
          tool_count: state.portalConnected ? 12 : 0,
          portal_url: state.portalConnected ? 'https://portal.example/api/mcp' : '',
          services: [],
        },
      });
    }
    if (path === '/api/mcp/portal/connect' && request.method() === 'POST') {
      state.portalPosts = state.portalPosts || [];
      state.portalPosts.push(request.postData() || '');
      if (state.portalConnectFails) {
        return route.fulfill({
          status: 502,
          json: { detail: 'MAD MCP rejected the key or catalog discovery was unavailable' },
        });
      }
      state.portalConnected = true;
      return route.fulfill({
        json: {
          configured: true,
          status: 'connected',
          tool_count: 12,
          portal_url: 'https://portal.example/api/mcp',
        },
      });
    }
    if (path === '/api/auth/settings' && request.method() === 'POST') {
      state.identityConfigured = true;
      return route.fulfill({ json: { agent_display_name: 'Friday' } });
    }
    if (path === '/api/gallery/discovery') {
      return route.fulfill({ json: { connected: 0, sources: [] } });
    }
    if (path === '/api/models' || path === '/api/model-endpoints' || path === '/api/sessions') {
      return route.fulfill({ json: [] });
    }
    return route.fulfill({ json: {} });
  });
}


test('first-run admin is guided to name the assistant without opening settings', async ({ page }) => {
  const state = { isAdmin: true, identityConfigured: false };
  await stubApis(page, state);
  await page.goto('/static/index.html');

  const modal = page.locator('#guide-modal');
  await expect(modal).not.toHaveClass(/hidden/);
  await expect(modal).toContainText('Set up Pandamonium');
  await expect(modal.getByRole('button', { name: 'Name it' })).toBeFocused();
  await expect(modal.locator('.setup-lane').filter({ hasText: 'Assistant name' }))
    .toContainText('Required');

  await modal.getByRole('button', { name: 'Name it' }).click();
  await expect(modal).toContainText('What should we call your assistant?');
  const input = modal.locator('.setup-wizard-field input');
  await input.fill('Friday');
  await modal.getByRole('button', { name: 'Save name' }).click();

  await expect(modal.locator('.setup-wizard-notice')).toContainText('Saved — your assistant is now called Friday.');
  await expect(modal.locator('.setup-lane').filter({ hasText: 'Assistant name' }))
    .toContainText('Ready — Friday');
  await expect(modal.locator('.setup-lane').filter({ hasText: 'Model engine' }))
    .toContainText('Required');
});


test('non-admin gets a status-only setup view with no dead-end actions', async ({ page }) => {
  const state = { isAdmin: false, identityConfigured: false };
  await stubApis(page, state);
  await page.goto('/static/index.html');

  const modal = page.locator('#guide-modal');
  await expect(modal).toHaveClass(/hidden/);

  const guideButton = page.locator('#user-bar-guide');
  await expect(guideButton).toBeVisible();
  await guideButton.click();

  await expect(modal).not.toHaveClass(/hidden/);
  await expect(modal).toContainText('Setup status');
  await expect(modal).toContainText('Managed by your administrator');
  await expect(modal.locator('.setup-lane-action')).toHaveCount(0);
  await expect(modal).not.toContainText('Updates');
});


test('an unreadable setup status does not auto-open the wizard', async ({ page }) => {
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/auth/status') {
      return route.fulfill({ json: { username: 'tester', is_admin: true, privileges: {} } });
    }
    if (path === '/api/setup/status') return route.fulfill({ json: {} });
    if (path === '/api/models' || path === '/api/model-endpoints' || path === '/api/sessions') {
      return route.fulfill({ json: [] });
    }
    return route.fulfill({ json: {} });
  });
  await page.goto('/static/index.html');

  await expect(page.locator('#user-bar-guide')).toBeVisible();
  await expect(page.locator('#guide-modal')).toHaveClass(/hidden/);
});


async function stubModelApis(page, state) {
  await page.route('**/api/**', route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === '/api/auth/status') {
      return route.fulfill({
        json: {
          username: 'tester',
          is_admin: true,
          privileges: {},
          agent_identity: { source: 'configured', display_name: 'Friday', status: 'healthy' },
        },
      });
    }
    if (path === '/api/setup/status') {
      return route.fulfill({
        json: {
          is_admin: true,
          identity: { configured: true, display_name: 'Friday', status: 'healthy' },
          model: state.modelUsable
            ? { usable: true, endpoints: 1, models: 2 }
            : { usable: false, endpoints: 0, models: 0 },
          voice: { ready: false, enabled: true, provider: 'disabled' },
          integrations: { configured: 0, portal_connected: false },
          extensions: { installed: 0, enabled: 0 },
          update: { version: '1.0.59', state: 'idle', target_version: null, rollback_available: false },
        },
      });
    }
    if (path === '/api/model-endpoints' && request.method() === 'POST') {
      state.posts.push(request.postData() || '');
      if (state.connectFails) {
        return route.fulfill({ status: 400, json: { detail: 'provider_key_invalid' } });
      }
      state.modelUsable = true;
      return route.fulfill({ json: { id: 'ep-1', models: state.addedModels || ['model-a', 'model-b'] } });
    }
    if (path === '/api/probe-selected' && request.method() === 'POST') {
      if (state.validationFails) {
        return route.fulfill({
          json: {
            results: [{
              status: 'fail',
              category: 'authentication',
              action: 'validate_settings',
              guidance: 'Validate settings — the endpoint rejected the credential.',
            }],
          },
        });
      }
      return route.fulfill({
        json: {
          results: [{
            status: 'ok',
            category: 'ok',
            action: 'none',
            guidance: '',
            model: 'model-a',
          }],
        },
      });
    }
    if (path === '/api/discover') {
      return route.fulfill({ json: { hosts: ['127.0.0.1'], items: state.discoverItems || [] } });
    }
    if (path === '/api/gallery/discovery') {
      return route.fulfill({ json: { connected: 0, sources: [] } });
    }
    return route.fulfill({ json: { items: [] } });
  });
}

async function openWizardModelStep(page) {
  const modal = page.locator('#guide-modal');
  await expect(modal).not.toHaveClass(/hidden/);
  await modal.locator('.setup-lane').filter({ hasText: 'Model engine' })
    .getByRole('button', { name: 'Connect' }).click();
  await expect(modal).toContainText('Give it a brain');
  return modal;
}


test('admin connects an API model from the wizard alone and the model lane turns ready', async ({ page }) => {
  const state = { modelUsable: false, connectFails: false, posts: [] };
  await stubModelApis(page, state);
  await page.goto('/static/index.html');

  const modal = await openWizardModelStep(page);
  const input = modal.locator('.setup-wizard-field input');
  await input.fill('sk-or-v1-0000000000000000000000');
  await modal.locator('.setup-wizard-primary', { hasText: 'Connect' }).click();

  await expect(modal.locator('.setup-wizard-notice')).toContainText('Found 2 models on OpenRouter');
  await expect(modal.locator('.setup-lane').filter({ hasText: 'Model engine' }))
    .toContainText('Ready — 2 models validated');
  await expect(page.locator('#settings-modal')).toHaveClass(/hidden/);
  expect(state.posts).toHaveLength(1);
  expect(state.posts[0]).toContain('https://openrouter.ai/api/v1');
  expect(state.posts[0]).toContain('sk-or-v1-0000000000000000000000');
});


test('a saved endpoint whose minimal completion fails is not called ready', async ({ page }) => {
  const state = { modelUsable: false, connectFails: false, validationFails: true, posts: [] };
  await stubModelApis(page, state);
  await page.goto('/static/index.html');

  const modal = await openWizardModelStep(page);
  await modal.locator('.setup-wizard-field input').fill('sk-or-v1-0000000000000000000000');
  await modal.locator('.setup-wizard-primary', { hasText: 'Connect' }).click();

  const message = modal.locator('.setup-wizard-message.is-error');
  await expect(message).toContainText('Validate settings');
  await expect(modal).not.toContainText("you're ready to chat");
  await expect(modal.locator('.setup-lane').filter({ hasText: 'Model engine' }))
    .not.toContainText('Ready');
});


test('a bad key shows next steps and never a raw backend code', async ({ page }) => {
  const state = { modelUsable: false, connectFails: true, posts: [] };
  await stubModelApis(page, state);
  await page.goto('/static/index.html');

  const modal = await openWizardModelStep(page);
  await modal.locator('.setup-wizard-field input').fill('sk-or-v1-0000000000000000000000');
  await modal.locator('.setup-wizard-primary', { hasText: 'Connect' }).click();

  const message = modal.locator('.setup-wizard-message.is-error');
  await expect(message).toContainText("We couldn't connect to OpenRouter");
  await expect(message).toContainText('try again');
  await expect(modal).not.toContainText('provider_key_invalid');
  await expect(modal).not.toContainText('400');
  await expect(modal).toContainText('Give it a brain');
  await expect(modal.locator('.setup-lane').filter({ hasText: 'Model engine' }))
    .toContainText('Not connected yet');
});


test('scanning this machine lists a local server and Add connects it', async ({ page }) => {
  const state = {
    modelUsable: false,
    connectFails: false,
    posts: [],
    addedModels: ['llama3:8b'],
    discoverItems: [{
      host: '127.0.0.1',
      port: 11434,
      url: 'http://127.0.0.1:11434/v1/chat/completions',
      models: ['llama3:8b'],
      models_display: ['llama3:8b'],
      provider: 'ollama',
    }],
  };
  await stubModelApis(page, state);
  await page.goto('/static/index.html');

  const modal = await openWizardModelStep(page);
  await modal.getByRole('button', { name: 'Scan this machine' }).click();

  const row = modal.locator('.setup-lane').filter({ hasText: 'Ollama' });
  await expect(row).toContainText('llama3:8b');
  await row.getByRole('button', { name: 'Add' }).click();

  await expect(modal.locator('.setup-wizard-notice')).toContainText('Found 1 model');
  await expect(modal.locator('.setup-lane').filter({ hasText: 'Model engine' }))
    .toContainText('Ready — 2 models validated');
  expect(state.posts).toHaveLength(1);
  expect(state.posts[0]).toContain('http://127.0.0.1:11434/v1');
});


test('an empty local scan says what to start instead of pretending it worked', async ({ page }) => {
  const state = { modelUsable: false, connectFails: false, posts: [], discoverItems: [] };
  await stubModelApis(page, state);
  await page.goto('/static/index.html');

  const modal = await openWizardModelStep(page);
  await modal.getByRole('button', { name: 'Scan this machine' }).click();

  const message = modal.locator('.setup-wizard-message.is-error');
  await expect(message).toContainText('No local model server found');
  await expect(message).toContainText('Ollama');
  await expect(modal.locator('.setup-lane').filter({ hasText: 'Model engine' }))
    .toContainText('Not connected yet');
});


test('a failed local Add points at the server instead of an API key', async ({ page }) => {
  const state = {
    modelUsable: false,
    connectFails: true,
    posts: [],
    discoverItems: [{
      host: '127.0.0.1',
      port: 11434,
      url: 'http://127.0.0.1:11434/v1/chat/completions',
      models: ['llama3:8b'],
      models_display: ['llama3:8b'],
      provider: 'ollama',
    }],
  };
  await stubModelApis(page, state);
  await page.goto('/static/index.html');

  const modal = await openWizardModelStep(page);
  await modal.getByRole('button', { name: 'Scan this machine' }).click();
  await modal.locator('.setup-lane').filter({ hasText: 'Ollama' })
    .getByRole('button', { name: 'Add' }).click();

  const message = modal.locator('.setup-wizard-message.is-error');
  await expect(message).toContainText("We couldn't connect to Ollama (127.0.0.1:11434)");
  await expect(message).toContainText('still running');
  await expect(modal).not.toContainText('provider_key_invalid');
});


test('skipping the model step returns to setup with an honest notice', async ({ page }) => {
  const state = { modelUsable: false, connectFails: false, posts: [] };
  await stubModelApis(page, state);
  await page.goto('/static/index.html');

  const modal = await openWizardModelStep(page);
  await modal.getByRole('button', { name: 'Skip for now' }).click();

  await expect(modal).toContainText('Set up Pandamonium');
  await expect(modal.locator('.setup-wizard-notice')).toContainText('connect a model');
  const lane = modal.locator('.setup-lane').filter({ hasText: 'Model engine' });
  await expect(lane).toContainText('Required');
  await expect(lane).toContainText('connect');
  expect(state.posts).toHaveLength(0);
});


test('voice step shows live readiness and tests the voice inline', async ({ page }) => {
  const state = { isAdmin: true, identityConfigured: false, voiceReady: true, ttsPosts: [] };
  await stubApis(page, state);
  await page.goto('/static/index.html');

  const modal = page.locator('#guide-modal');
  await expect(modal).not.toHaveClass(/hidden/);
  await modal.locator('.setup-lane').filter({ hasText: 'Voice' })
    .getByRole('button', { name: 'Set up' }).click();

  await expect(modal).toContainText('Give it a voice');
  await expect(modal.locator('.setup-lane').filter({ hasText: 'Voice' })).toContainText('Ready');

  await modal.getByRole('button', { name: 'Test voice' }).click();
  await expect(modal.locator('.setup-wizard-message')).toContainText("That's your assistant's voice");
  expect(state.ttsPosts).toHaveLength(1);
  expect(state.ttsPosts[0]).toContain('"format":"audio"');
  expect(state.ttsPosts[0]).toContain('Hello — this is a voice preview');
});


test('a skipped voice lane says so and the skip persists across reloads', async ({ page }) => {
  const state = { isAdmin: true, identityConfigured: false, voiceReady: false, ttsPosts: [] };
  await stubApis(page, state);
  await page.goto('/static/index.html');

  const modal = page.locator('#guide-modal');
  await modal.locator('.setup-lane').filter({ hasText: 'Voice' })
    .getByRole('button', { name: 'Set up' }).click();

  await expect(modal).toContainText('Give it a voice');
  await expect(modal.locator('.setup-lane').filter({ hasText: 'Voice' })).toContainText('Not ready yet');
  await expect(modal).toContainText('Configure an available chat model for Voice Orb.');
  await expect(modal.getByRole('button', { name: 'Test voice' })).toBeDisabled();
  expect(state.ttsPosts).toHaveLength(0);

  await modal.getByRole('button', { name: 'Skip for now' }).click();
  await expect(modal.locator('.setup-wizard-notice')).toContainText('voice');
  await expect(modal.locator('.setup-lane').filter({ hasText: 'Voice' })).toContainText('Skipped');

  await page.reload();
  await expect(page.locator('#guide-modal')).not.toHaveClass(/hidden/);
  await expect(page.locator('#guide-modal .setup-lane').filter({ hasText: 'Voice' }))
    .toContainText('Skipped');
});


test('portal step connects inline and the services lane turns ready', async ({ page }) => {
  const state = { isAdmin: true, identityConfigured: true, portalConnected: false, portalPosts: [] };
  await stubApis(page, state);
  await page.goto('/static/index.html');

  const modal = page.locator('#guide-modal');
  await expect(modal).not.toHaveClass(/hidden/);
  await modal.locator('.setup-lane').filter({ hasText: 'Services' })
    .getByRole('button', { name: 'Connect' }).click();

  await expect(modal).toContainText('Connect your services');
  await modal.getByLabel('Portal MCP URL').fill('https://portal.example/api/mcp');
  await modal.getByLabel('Master key').fill('m'.repeat(40));
  await modal.getByRole('button', { name: 'Connect', exact: true }).click();

  await expect(modal.locator('.setup-wizard-notice')).toContainText('MAD MCP Portal is connected');
  await expect(modal.locator('.setup-lane').filter({ hasText: 'Services' }))
    .toContainText('Ready — MAD MCP Portal connected');
  await expect(page.locator('#settings-modal')).toHaveClass(/hidden/);
  expect(state.portalPosts).toHaveLength(1);
  expect(state.portalPosts[0]).toContain('"portal_url":"https://portal.example/api/mcp"');
  expect(state.portalPosts[0]).toContain('"master_key":"mmmm');
});


test('a failed portal connect explains what to do without backend codes', async ({ page }) => {
  const state = { isAdmin: true, identityConfigured: true, portalConnectFails: true, portalPosts: [] };
  await stubApis(page, state);
  await page.goto('/static/index.html');

  const modal = page.locator('#guide-modal');
  await modal.locator('.setup-lane').filter({ hasText: 'Services' })
    .getByRole('button', { name: 'Connect' }).click();
  await modal.getByLabel('Portal MCP URL').fill('https://portal.example/api/mcp');
  await modal.getByLabel('Master key').fill('m'.repeat(40));
  await modal.getByRole('button', { name: 'Connect', exact: true }).click();

  const message = modal.locator('.setup-wizard-message.is-error');
  await expect(message).toContainText("We couldn't connect to your Portal");
  await expect(message).toContainText('try again');
  await expect(modal).not.toContainText('MAD MCP rejected');
  await expect(modal).not.toContainText('502');
});
