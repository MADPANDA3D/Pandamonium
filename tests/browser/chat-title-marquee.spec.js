import { expect, test } from '@playwright/test';

test('chat hover scrolls a truncated title and hides the drag dots', async ({ page }) => {
  const longName = 'Simple Hello Greeting Extended Conversation With A Very Long Descriptive Title That Overflows The Row';
  const now = new Date().toISOString();
  const session = {
    id: 'session-long',
    name: longName,
    model: 'test/model',
    endpoint_url: 'http://model.test/v1/chat/completions',
    message_count: 2,
    archived: false,
    created_at: now,
    updated_at: now,
    last_message_at: now,
  };
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/sessions') return route.fulfill({ json: [session] });
    if (url.pathname === '/api/auth/status') return route.fulfill({ json: { username: 'tester', is_admin: false, privileges: {} } });
    if (url.pathname === '/api/projects') return route.fulfill({ json: { projects: [], root: '' } });
    if (['/api/models', '/api/model-endpoints', '/api/default-chat'].includes(url.pathname)) return route.fulfill({ json: [] });
    return route.fulfill({ json: {} });
  });
  await page.goto('/static/index.html');

  const row = page.locator('#session-list .list-item[data-session-id="session-long"]');
  await expect(row).toHaveCount(1);
  const grow = row.locator(':scope > .grow');

  // No marquee before hover.
  await expect(grow).not.toHaveClass(/marquee/);

  await row.hover();

  // The title scrolls: marquee class plus a negative travel distance.
  await expect(grow).toHaveClass(/marquee/);
  const shift = await grow.evaluate(el => getComputedStyle(el).getPropertyValue('--marquee-shift'));
  expect(parseFloat(shift)).toBeLessThan(0);

  // The drag dots stay invisible, but the drag zone remains interactive.
  const handle = row.locator('.item-drag-handle');
  expect(await handle.evaluate(el => getComputedStyle(el).opacity)).toBe('0');
  expect(await handle.evaluate(el => getComputedStyle(el).pointerEvents)).toBe('auto');
});
