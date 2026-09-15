import { test, expect } from '@playwright/test';

for (const width of [1280, 390]) {
  test(`purpose, setup and readiness remain accessible at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    const plugin = {
      id: 'speech-demo', name: 'Speech Demo', summary: 'Turn a paragraph into spoken audio.', icon: '◈', categories: ['audio'],
      examples: ['Read my welcome message aloud.'], requirements: ['Choose a speech service.'],
      capability_summaries: [{ name: 'synthesize', kind: 'tool', description: 'Create spoken audio' }],
      state: 'enabled', version: '1.0.0', origin: 'registry', runtime: 'service',
      readiness: { state: 'ready', message: 'Operation checks passed.' },
      configuration: [{ key: 'ENDPOINT_ID', description: 'Speech service', required: true }],
      capabilities: [], permissions: {}, data_boundaries: {},
    };
    const calls = [];
    await page.route('**/api/**', route => {
      const path = new URL(route.request().url()).pathname;
      calls.push(path);
      if (path === '/api/extensions/installed') return route.fulfill({ json: { plugins: [plugin] } });
      if (path === '/api/extensions/installed/speech-demo') return route.fulfill({ json: plugin });
      if (path === '/api/extensions/runtime/speech-demo/configuration') {
        if (route.request().method() === 'PUT') {
          plugin.state = 'disabled';
          plugin.readiness = { state: 'needs_setup', message: 'Setup changed. Enable to validate the new configuration.' };
          return route.fulfill({ json: {} });
        }
        return route.fulfill({ json: { fields: [{ ...plugin.configuration[0], value: 'node', configured: true }], targets: [{ id: 'node', name: 'Speech node', kind: 'tts', base_url: 'https://speech.example' }] } });
      }
      if (path === '/api/extensions/marketplace') return route.fulfill({ json: { status: 'empty', plugins: [] } });
      if (path === '/api/auth/status') return route.fulfill({ json: { username: 'tester', is_admin: true, privileges: {} } });
      if (['/api/models', '/api/model-endpoints', '/api/sessions'].includes(path)) return route.fulfill({ json: [] });
      return route.fulfill({ json: {} });
    });
    await page.goto('/static/index.html');
    if (width < 768) await page.getByRole('button', { name: 'Toggle sidebar' }).click();
    await page.getByRole('button', { name: 'Browse plugins' }).click();
    const card = page.locator('[data-installed-id="speech-demo"]');
    await expect(card).toContainText(plugin.summary);
    expect(await card.evaluate(node => node.scrollHeight <= node.clientHeight + 1)).toBeTruthy();
    await card.press('Enter');
    const detail = page.locator('#marketplace-installed-detail');
    await expect(detail).toBeFocused();
    await expect(detail.locator('[data-readiness="ready"]')).toBeVisible();
    await expect(detail).toContainText(plugin.examples[0]);
    await expect(detail.locator('.marketplace-technical')).not.toHaveAttribute('open');
    await expect(detail.getByRole('button', { name: 'Save setup' })).toBeVisible();
    await detail.getByRole('button', { name: 'Save setup' }).click();
    await expect(detail.locator('[data-readiness="needs_setup"]')).toBeVisible();
    await expect(detail.getByRole('button', { name: 'Enable', exact: true })).toBeVisible();
    await detail.locator('.marketplace-technical > summary').press('Enter');
    await expect(detail.locator('.marketplace-technical')).toHaveAttribute('open');
    expect(await detail.evaluate(node => node.scrollWidth <= node.clientWidth + 1)).toBeTruthy();
    await page.getByRole('button', { name: '← Back to installed' }).click();
    await expect(card).toBeFocused();
    await page.getByRole('tab', { name: 'Marketplace' }).click();
    await expect(page.locator('[data-plugin-id="speech-demo"]')).toContainText(plugin.summary);
    await page.locator('[data-plugin-id="speech-demo"]').press('Enter');
    await expect(page.locator('#marketplace-detail')).toContainText(plugin.examples[0]);
    expect(calls.some(path => /mount|execute|agent\/tool/.test(path))).toBeFalsy();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('button', { name: 'Browse plugins' })).toBeFocused();
  });
}
