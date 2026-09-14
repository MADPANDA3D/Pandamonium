// MAD-931: guided help for the AI Defaults model sections.
// Help controls open in the existing tour tooltip language; the model-defaults
// tour chapter walks the five cards and can be replayed without touching
// settings.
import { expect, test } from '@playwright/test';

const SECTION_LABELS = {
  utility: 'Utility model',
  vision: 'Vision',
  research: 'Research model',
  image: 'Image generation',
  voice: 'Voice',
};

async function mockApp(page) {
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/auth/status') {
      return route.fulfill({ json: { username: 'leo', is_admin: true, privileges: {} } });
    }
    if (url.pathname === '/api/auth/settings') {
      return route.fulfill({
        json: {
          utility_endpoint_id: 'ep-local',
          utility_model: 'qwen3-4b',
          utility_model_fallbacks: [{ endpoint_id: 'ep-local', model: 'qwen3-4b' }],
          vision_model: 'qwen3-vl',
          vision_model_fallbacks: [],
          research_endpoint_id: '',
          research_model: 'deepseek-r1',
          image_model: 'sd-3.5',
          image_quality: 'medium',
          tts_provider: 'local',
          tts_model: 'kokoro',
          tts_voice: 'af_heart',
          tts_enabled: true,
        },
      });
    }
    if (url.pathname === '/api/model-endpoints') {
      return route.fulfill({
        json: [{
          id: 'ep-local', name: 'Local Studio', is_enabled: true, online: true,
          models: ['qwen3-4b', 'qwen3-vl'],
        }],
      });
    }
    if (url.pathname === '/api/models') {
      return route.fulfill({
        json: {
          items: [{
            url: 'http://local.test/v1', endpoint_id: 'ep-local', endpoint_name: 'Local Studio',
            model_type: 'llm', offline: false,
            models: ['qwen3-4b', 'qwen3-vl'],
            models_display: ['Qwen 4B', 'Qwen VL'],
            models_extra: [],
            models_extra_display: [],
          }],
        },
      });
    }
    if (url.pathname === '/api/setup/status') {
      return route.fulfill({
        json: {
          is_admin: true,
          identity: { configured: true, display_name: 'Jarvis', status: 'healthy' },
          model: { usable: true, endpoints: 1, models: 1 },
          voice: { ready: false, enabled: true, provider: 'disabled' },
          integrations: { configured: 0, portal_connected: false },
          extensions: { installed: 0, enabled: 0 },
          update: { version: '1.0.62', state: 'idle', target_version: null, rollback_available: false },
        },
      });
    }
    if (url.pathname === '/api/gallery/discovery') {
      return route.fulfill({ json: { sources: [], connected: 0 } });
    }
    if (url.pathname === '/api/sessions' || url.pathname === '/api/selector-catalog') {
      return route.fulfill({ json: [] });
    }
    if (url.pathname === '/api/default-chat') {
      return route.fulfill({ json: {} });
    }
    return route.fulfill({ json: {} });
  });
}

async function openAiSettings(page) {
  await page.goto('/static/index.html');
  await page.evaluate(async () => (await import('/static/js/settings.js')).open('ai'));
  await expect(page.locator('#settings-modal')).toBeVisible();
}

test('each model section has a keyboard help control that opens and closes in the tour style', async ({ page }) => {
  await mockApp(page);
  await openAiSettings(page);

  for (const [key, label] of Object.entries(SECTION_LABELS)) {
    const button = page.locator(`[data-model-help="${key}"]`);
    await expect(button).toBeVisible();
    await button.focus();
    await page.keyboard.press('Enter');

    const tooltip = page.locator('#tour-tooltip');
    await expect(tooltip).toBeVisible();
    await expect(tooltip).toHaveAttribute('data-model-help-key', key);
    await expect(tooltip).toContainText(label);
    await expect(tooltip).toContainText('What it does');
    await expect(tooltip).toContainText('What to pick');
    await expect(tooltip).toContainText('fails');
    await expect(tooltip).toContainText('Cost');

    // Escape closes and hands focus back to the help button.
    await page.keyboard.press('Escape');
    await expect(tooltip).toHaveCount(0);
    const focused = await page.evaluate(() => document.activeElement?.getAttribute('data-model-help'));
    expect(focused).toBe(key);
  }
});

test('help is anchored to the live configuration without exposing secrets', async ({ page }) => {
  await mockApp(page);
  await openAiSettings(page);

  await page.locator('[data-model-help="utility"]').click();
  const tooltip = page.locator('#tour-tooltip');
  await expect(tooltip).toContainText('Local Studio');
  await expect(tooltip).toContainText('qwen3-4b');
  await expect(tooltip).toContainText('1 fallback');

  // No secret-shaped content can leak into the rendered copy.
  const text = await tooltip.innerText();
  for (const token of ['api_key', 'endpoint_id', 'model_id', 'sk-']) {
    expect(text).not.toContain(token);
  }

  // Outside click closes too.
  await page.locator('#settings-modal .settings-modal-content').click({ position: { x: 5, y: 5 } });
  await expect(tooltip).toHaveCount(0);
});

test('model-defaults chapter walks the five sections and replays without resetting settings', async ({ page }) => {
  const settingWrites = [];
  await mockApp(page);
  await page.route('**/api/auth/settings', async route => {
    if (route.request().method() === 'POST') settingWrites.push(route.request().postData() || '');
    return route.fallback();
  });
  await openAiSettings(page);
  const before = await page.locator('#set-utilityModelSelect').inputValue();

  async function runChapter() {
    await page.evaluate(() => {
      import('/static/js/slashCommands.js').then(mod => mod.handleSlashCommand('/tour-models'));
    });
    await expect(page.locator('#tour-tooltip')).toBeVisible();
  }

  await runChapter();
  await expect(page.locator('#settings-modal [data-settings-tab="ai"]')).toHaveClass(/active/);

  // Intro + AI Defaults lead-in, then the five model cards in order.
  await expect(page.locator('#tour-tooltip')).toContainText('Model defaults');
  await page.locator('#tour-tooltip [data-act="next"]').click();
  await expect(page.locator('#tour-tooltip')).toContainText('AI Defaults');
  await page.locator('#tour-tooltip [data-act="next"]').click();
  for (const label of Object.values(SECTION_LABELS)) {
    await expect(page.locator('#tour-tooltip')).toContainText(label);
    await page.locator('#tour-tooltip [data-act="next"]').click();
  }
  // The closing step points back at the guide and the replay command.
  await expect(page.locator('#tour-tooltip')).toContainText('Replay this chapter');
  await page.locator('#tour-tooltip [data-act="skip"]').click();
  await expect(page.locator('#tour-tooltip')).toHaveCount(0);

  // Replay works and never writes settings.
  await runChapter();
  await expect(page.locator('#tour-tooltip')).toContainText('Model defaults');
  await page.locator('#tour-tooltip [data-act="skip"]').click();
  await expect(page.locator('#tour-tooltip')).toHaveCount(0);

  expect(settingWrites).toEqual([]);
  expect(await page.locator('#set-utilityModelSelect').inputValue()).toBe(before);
});

test('the first-run guide links to the model-defaults chapter', async ({ page }) => {
  await mockApp(page);
  await page.goto('/static/index.html');
  // The guide button is enabled once the app has wired the wizard handlers.
  await expect(page.locator('#user-bar-guide')).toBeVisible();
  await page.evaluate(async () => {
    const wizard = await import('/static/js/setupWizard.js');
    wizard.open();
  });
  const modelDefaults = page.locator('#guide-panel').getByRole('button', { name: 'Model defaults' });
  await expect(modelDefaults).toBeVisible();
  await modelDefaults.click();
  await expect(page.locator('#tour-tooltip')).toBeVisible();
  await expect(page.locator('#tour-tooltip')).toContainText('Model defaults');
  await page.locator('#tour-tooltip [data-act="next"]').click();
  await expect(page.locator('#tour-tooltip')).toContainText('AI Defaults');
  await page.locator('#tour-tooltip [data-act="skip"]').click();
});
