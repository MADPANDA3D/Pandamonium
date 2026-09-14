// MAD-932: the session Details panel auto-hides while a side-mounted panel
// (Settings, or any docked tool window) owns the edge, restores when the side
// panel closes, and respects an explicit re-open without fighting the dock.
import { expect, test } from '@playwright/test';

async function mockApp(page, viewport = { width: 1440, height: 900 }) {
  await page.setViewportSize(viewport);
  const now = '2026-09-14T12:00:00Z';
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path === '/api/auth/status') {
      return route.fulfill({ json: { username: 'leo', is_admin: true, privileges: {} } });
    }
    if (path === '/api/auth/settings') {
      return route.fulfill({ json: { agent_id: 'assistant', agent_display_name: 'Assistant' } });
    }
    if (path === '/api/sessions') {
      return route.fulfill({
        json: [{
          id: 'ctx-chat', name: 'Chat', model: 'fixture', endpoint_url: 'http://model.test/v1',
          agent_target: 'jarvis', identity_id: '', reasoning_level: '',
          created_at: now, updated_at: now, message_count: 1,
        }],
      });
    }
    if (path.startsWith('/api/history/')) return route.fulfill({ json: { history: [] } });
    if (['/api/models', '/api/model-endpoints', '/api/selector-catalog', '/api/plugins', '/api/tools'].includes(path)) {
      return route.fulfill({ json: [] });
    }
    if (path === '/api/default-chat') return route.fulfill({ json: {} });
    return route.fulfill({ json: {} });
  });
  await page.addInitScript(() => localStorage.setItem('lastSessionId', 'ctx-chat'));
  await page.goto('/static/index.html');
  await expect.poll(() => page.evaluate(() => Boolean(window.sessionModule && window.conversationContext))).toBe(true);
  await expect(page.locator('#session-context-toggle')).toBeAttached();
}

function openSettings(page, tab = 'ai') {
  return page.evaluate(async t => { (await import('/static/js/settings.js')).open(t); }, tab);
}

function closeSettings(page) {
  return page.evaluate(async () => { (await import('/static/js/settings.js')).close(); });
}

test('opening Settings hides Details and closing it restores the panel', async ({ page }) => {
  await mockApp(page);

  await openSettings(page);
  await expect(page.locator('body')).toHaveClass(/right-dock-active/);
  await expect(page.locator('#session-context-panel')).toBeHidden();
  await expect(page.locator('#session-context-toggle')).toHaveAttribute('aria-expanded', 'false');

  await closeSettings(page);
  await expect(page.locator('body')).not.toHaveClass(/right-dock-active/);
  await expect(page.locator('#session-context-panel')).toBeVisible();
  await expect(page.locator('#session-context-toggle')).toHaveAttribute('aria-expanded', 'true');
});

test('an explicit re-open while docked sticks, and the preference survives the close', async ({ page }) => {
  await mockApp(page);

  await openSettings(page);
  await expect(page.locator('#session-context-panel')).toBeHidden();

  // The user explicitly re-opens Details while Settings is docked.
  await page.locator('#session-context-toggle').click();
  await expect(page.locator('#session-context-panel')).toBeVisible();
  // The dock observer must not immediately hide it again.
  await page.waitForTimeout(400);
  await expect(page.locator('#session-context-panel')).toBeVisible();
  await expect(page.locator('body')).toHaveClass(/right-dock-active/);

  // Closing Settings leaves the user's explicit choice alone.
  await closeSettings(page);
  await expect(page.locator('body')).not.toHaveClass(/right-dock-active/);
  await expect(page.locator('#session-context-panel')).toBeVisible();

  // An explicit close clears the preference, so there is nothing to restore
  // for that dock cycle.
  await page.locator('#session-context-toggle').click();
  await expect(page.locator('#session-context-panel')).toBeHidden();
  await openSettings(page);
  await expect(page.locator('#session-context-panel')).toBeHidden();
  await closeSettings(page);
  await expect(page.locator('body')).not.toHaveClass(/right-dock-active/);
  await expect(page.locator('#session-context-panel')).toBeHidden();

  // The preference was cleared: a fresh open → dock → close cycle
  // auto-hides and restores as normal again.
  await page.locator('#session-context-toggle').click();
  await expect(page.locator('#session-context-panel')).toBeVisible();
  await openSettings(page);
  await expect(page.locator('#session-context-panel')).toBeHidden();
  await closeSettings(page);
  await expect(page.locator('body')).not.toHaveClass(/right-dock-active/);
  await expect(page.locator('#session-context-panel')).toBeVisible();
});

test('any docked tool window follows the same rule, not just Settings', async ({ page }) => {
  await mockApp(page);

  await page.evaluate(async () => {
    const compare = await import('/static/js/compare/selector.js');
    window.__comparePromise = compare.showModelSelector();
  });
  await expect(page.locator('#compare-model-overlay')).toBeVisible();
  await expect(page.locator('body')).toHaveClass(/right-dock-active/);
  await expect(page.locator('#session-context-panel')).toBeHidden();

  await page.locator('#compare-model-overlay .close-btn').click();
  await expect(page.locator('body')).not.toHaveClass(/right-dock-active/);
  await expect(page.locator('#session-context-panel')).toBeVisible();
});

for (const viewport of [
  { label: 'wallboard', width: 1920, height: 1080, detailsOpen: true },
  { label: 'laptop', width: 1366, height: 900, detailsOpen: true },
  { label: 'mobile', width: 390, height: 844, detailsOpen: false },
]) {
  test(`${viewport.label} width keeps the layout stable while Settings is open`, async ({ page }) => {
    await mockApp(page, { width: viewport.width, height: viewport.height });
    if (!viewport.detailsOpen) {
      await expect(page.locator('#session-context-panel')).toBeHidden();
    }

    await openSettings(page);
    await expect(page.locator('#settings-modal')).toBeVisible();

    if (viewport.width > 768) {
      await expect(page.locator('body')).toHaveClass(/right-dock-active/);
      if (viewport.detailsOpen) {
        await expect(page.locator('#session-context-panel')).toBeHidden();
      }
    } else {
      await expect(page.locator('body')).not.toHaveClass(/right-dock-active/);
    }

    // No horizontal overflow / layout jump at any width.
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow).toBeLessThanOrEqual(1);

    await closeSettings(page);
    await expect(page.locator('body')).not.toHaveClass(/right-dock-active/);
    if (viewport.detailsOpen) {
      await expect(page.locator('#session-context-panel')).toBeVisible();
    } else {
      await expect(page.locator('#session-context-panel')).toBeHidden();
    }
    const restoredOverflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(restoredOverflow).toBeLessThanOrEqual(1);
  });
}
