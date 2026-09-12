// MAD-899 quick visual batch: orb symmetry, right-docked Settings/Updater,
// display size controls, and sidebar bucket reorder.
import { expect, test } from '@playwright/test';

async function installMockRoutes(page) {
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/auth/status') {
      return route.fulfill({ json: { username: 'leo', is_admin: true, privileges: {} } });
    }
    if (['/api/sessions', '/api/model-endpoints', '/api/models', '/api/plugins'].includes(url.pathname)) {
      return route.fulfill({ json: [] });
    }
    return route.fulfill({ json: {} });
  });
}

test('quick visual batch — orb, docks, display size, bucket reorder', async ({ page }) => {
  await installMockRoutes(page);
  await page.goto('/static/index.html');
  await expect(page.locator('#access-mode-btn')).toBeVisible();

  // 1. Shield/access orb matches the voice orb for symmetry (MAD-899).
  const shield = await page.locator('#access-mode-btn').boundingBox();
  const voice = await page.locator('#jarvis-input-sphere').boundingBox();
  expect(Math.abs(shield.width - voice.width)).toBeLessThanOrEqual(1);
  expect(Math.abs(shield.height - voice.height)).toBeLessThanOrEqual(1);

  // 2. Settings opens docked to the right.
  const iconBefore = await page.locator('#email-section .section-icon').evaluate(el => el.getBoundingClientRect().width);
  expect(iconBefore).toBeGreaterThan(0);
  await page.evaluate(async () => (await import('/static/js/settings.js')).open());
  await expect(page.locator('#settings-modal')).toBeVisible();
  await expect(page.locator('#settings-modal')).toHaveClass(/modal-right-docked/);

  // 3. Display size controls apply and persist.
  await page.locator('#settings-modal [data-settings-tab="appearance"]').click();
  await page.locator('#set-text-size').selectOption('125');
  await expect.poll(() => page.evaluate(() => document.documentElement.classList.contains('ui-scale-125'))).toBe(true);
  await page.locator('#set-icon-size').selectOption('150');
  await expect.poll(() => page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--ui-icon-scale').trim())).toBe('1.5');
  expect(await page.evaluate(() => localStorage.getItem('odysseus-ui-scale'))).toBe('125');
  expect(await page.evaluate(() => localStorage.getItem('odysseus-ui-icon-scale'))).toBe('150');
  await page.locator('#settings-modal .close-btn').click();
  await expect(page.locator('#settings-modal')).toBeHidden();

  // Persisted sizes re-apply on load and visibly grow the sidebar icons.
  await page.reload();
  const iconAfter = await page.locator('#email-section .section-icon').evaluate(el => el.getBoundingClientRect().width);
  expect(iconAfter).toBeGreaterThan(iconBefore);

  // Reset to defaults for the reorder/updater checks.
  await page.evaluate(() => {
    localStorage.removeItem('odysseus-ui-scale');
    localStorage.removeItem('odysseus-ui-icon-scale');
  });
  await page.reload();
  await expect(page.locator('#access-mode-btn')).toBeVisible();

  // 4. Bucket reorder — handle drag moves Plugins above Tools and persists.
  const handle = page.locator('#plugins-section .section-drag-handle');
  await page.locator('#plugins-section').hover();
  const handleBox = await handle.boundingBox();
  const toolsBox = await page.locator('#tools-section').boundingBox();
  expect(toolsBox).not.toBeNull();
  await page.mouse.move(handleBox.x + handleBox.width / 2, handleBox.y + handleBox.height / 2);
  await page.mouse.down();
  await page.mouse.move(toolsBox.x + 8, Math.max(1, toolsBox.y + 4), { steps: 8 });
  await page.mouse.up();
  await expect.poll(() => page.evaluate(() => {
    const ids = [...document.querySelectorAll('.sidebar-inner .section')].map(s => s.id);
    return ids.indexOf('plugins-section') < ids.indexOf('tools-section');
  })).toBe(true);
  const saved = await page.evaluate(() => localStorage.getItem('sidebar-section-order'));
  const order = JSON.parse(saved);
  expect(order.indexOf('plugins-section')).toBeLessThan(order.indexOf('tools-section'));

  // 5. Updater modal also opens right-docked (sidebar button path).
  await page.locator('#sidebar-update-check').click();
  await expect(page.locator('#updater-modal')).toBeVisible();
  await expect(page.locator('#updater-modal')).toHaveClass(/modal-right-docked/);
});

test('reasoning-capable API model switches the composer to reasoning effort (MAD-900)', async ({ page }) => {
  const now = new Date().toISOString();
  let submitted = '';
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/auth/status') {
      return route.fulfill({ json: { username: 'leo', is_admin: true, privileges: {} } });
    }
    if (url.pathname === '/api/sessions') {
      return route.fulfill({ json: [{
        id: 'reason-chat', name: 'Reason chat', model: 'deepseek/deepseek-v4.1-flash',
        endpoint_url: 'https://openrouter.ai/api/v1', agent_target: 'jarvis',
        created_at: now, updated_at: now, message_count: 1,
      }] });
    }
    if (url.pathname.startsWith('/api/chat_stream')) {
      submitted = route.request().postData() || '';
      return route.fulfill({
        headers: { 'Content-Type': 'text/event-stream' },
        body: 'data: {"delta":"ok"}\n\ndata: [DONE]\n\n',
      });
    }
    if (url.pathname.startsWith('/api/history')) return route.fulfill({ json: { history: [] } });
    return route.fulfill({ json: {} });
  });
  await page.addInitScript(() => localStorage.setItem('lastSessionId', 'reason-chat'));
  await page.goto('/static/index.html');
  await expect.poll(() => page.evaluate(() => Boolean(window.sessionModule))).toBe(true);
  await page.evaluate(async () => {
    const module = await import('/static/js/sessions.js');
    module.setCurrentSessionId('reason-chat');
    module.updateModelPicker();
  });
  await page.evaluate(() => {
    window.modelsModule.getCachedItems = () => [{
      url: 'https://openrouter.ai/api/v1',
      models: ['deepseek/deepseek-v4.1-flash'],
      models_extra: [],
      reasoning_levels: { 'deepseek/deepseek-v4.1-flash': ['low', 'medium', 'high'] },
    }];
    window.dispatchEvent(new Event('odysseus:session-rendered'));
    document.dispatchEvent(new Event('odysseus:model-picked'));
  });

  await expect(page.locator('#conversation-effort-label')).toHaveText('Reasoning effort');
  await page.locator('#composer-effort-btn').click();
  await page.locator('#conversation-effort').evaluate(el => {
    el.value = el.max;
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await expect(page.locator('#conversation-effort-value')).toHaveText('High');
  expect(await page.evaluate(() => window.conversationContext.getReasoningEffort())).toBe('high');
  expect(await page.evaluate(() => window.conversationContext.getAgentEffort())).toBe('');
  await page.locator('#composer-effort-btn').click();

  await page.locator('#message').fill('hello');
  await page.locator('.send-btn').click();
  await expect.poll(() => submitted).toContain('reasoning_effort');
  expect(submitted).toMatch(/name="reasoning_effort"\r?\n\r?\nhigh/);
  expect(submitted).not.toContain('name="agent_effort"');
});

test('Add Local Models offers STT and TTS types (MAD-901)', async ({ page }) => {
  await page.route('**/api/**', route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/auth/status') {
      return route.fulfill({ json: { username: 'leo', is_admin: true, privileges: {} } });
    }
    return route.fulfill({ json: {} });
  });
  await page.goto('/static/index.html');
  await page.evaluate(async () => (await import('/static/js/settings.js')).open('services'));
  await expect(page.locator('#settings-modal')).toBeVisible();
  const values = await page.locator('#adm-epLocalType option').evaluateAll(options => options.map(option => option.value));
  expect(values).toEqual(['llm', 'image', 'stt', 'tts']);
});
