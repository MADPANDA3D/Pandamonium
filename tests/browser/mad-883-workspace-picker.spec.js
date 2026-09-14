import { expect, test } from '@playwright/test';

/**
 * MAD-883 — the workspace picker must be reachable and honest.
 *
 * The static test server has no backend, so /api/** is mocked. The spec
 * covers the two failure modes from the issue:
 *   * a usable picker for the owner (browse → pick → pill + POST persistence)
 *   * actionable copy for 401/403 instead of the old generic "Could not
 *     browse folders".
 */

function sessionFixture() {
  return {
    id: 'session-one',
    name: 'Existing chat',
    model: 'test/model',
    endpoint_url: 'http://model.test/v1/chat/completions',
    message_count: 2,
    archived: false,
    created_at: '2026-09-04T20:00:00Z',
    updated_at: '2026-09-04T20:01:00Z',
    last_message_at: '2026-09-04T20:01:00Z',
    workspace: '',
  };
}

async function mockApi(page, { browseStatus = 200, browseDetail = '', posted = [] } = {}) {
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/sessions') {
      return route.fulfill({ json: [sessionFixture()] });
    }
    if (url.pathname === '/api/workspace/browse') {
      if (browseStatus !== 200) {
        return route.fulfill({
          status: browseStatus,
          json: browseDetail ? { detail: browseDetail } : { error: 'Not authenticated' },
        });
      }
      return route.fulfill({
        json: {
          path: '/home/leo',
          parent: '/home',
          dirs: [{ name: 'project', path: '/home/leo/project' }],
          truncated: false,
          selectable: true,
        },
      });
    }
    if (url.pathname === '/api/workspace/session' && route.request().method() === 'POST') {
      posted.push(route.request().postDataJSON());
      return route.fulfill({ json: { ok: true, path: '/home/leo' } });
    }
    if (url.pathname === '/api/auth/status') {
      return route.fulfill({ json: { username: 'tester', is_admin: true, privileges: {} } });
    }
    if (url.pathname === '/api/default-chat') {
      return route.fulfill({
        json: {
          endpoint_id: 'endpoint-one',
          endpoint_url: 'http://model.test/v1/chat/completions',
          model: 'test/model',
        },
      });
    }
    if (url.pathname === '/api/history/session-one') {
      return route.fulfill({
        json: {
          history: [{ role: 'user', content: 'Hello' }],
          model: 'test/model',
          name: 'Existing chat',
          offset: 0,
          limit: 100,
          total: 1,
          has_more_before: false,
        },
      });
    }
    if (url.pathname === '/api/model-endpoints' || url.pathname === '/api/models') {
      return route.fulfill({ json: [] });
    }
    return route.fulfill({ json: {} });
  });
}

async function waitForBoot(page) {
  await expect.poll(() => page.evaluate(async () => {
    const directImport = await import('/static/js/sessions.js');
    return window.sessionModule === directImport.default;
  }), { timeout: 15_000 }).toBe(true);
}

async function selectFixtureSession(page, sessionId = 'session-one') {
  // The picker persists the chosen folder on the CURRENT chat; before a
  // session is selected, the value is deliberately localStorage-only and is
  // persisted on the first send. Select the mocked session explicitly (the
  // static test server has no backend for the initial auto-select) so the POST
  // assertion is deterministic.
  await expect.poll(() => page.evaluate(expectedId => (
    typeof window.sessionModule?.selectSession === 'function'
    && window.sessionModule.getSessions?.().some(session => session.id === expectedId)
  ), sessionId), { timeout: 15_000 }).toBe(true);
  await page.evaluate(id => window.sessionModule.selectSession(id, { showLoading: false }), sessionId);
  await expect.poll(
    () => page.evaluate(() => window.sessionModule?.getCurrentSessionId()),
    { timeout: 15_000 },
  ).toBe(sessionId);
}

async function openPicker(page) {
  await page.goto('/static/index.html#session-one');
  await waitForBoot(page);
  await selectFixtureSession(page, 'session-one');
  await page.evaluate(async () => {
    const module = await import('/static/js/workspace.js');
    await (module.default || module).openWorkspaceBrowser();
  });
  await expect(page.locator('#workspace-modal')).toBeVisible();
}

test('picker lists folders, binds the chosen folder, and persists it on the chat', async ({ page }) => {
  const posted = [];
  await mockApi(page, { posted });
  await openPicker(page);

  await expect(page.locator('#workspace-body .workspace-row', { hasText: 'project' })).toBeVisible();
  await page.locator('#workspace-use').click();

  await expect.poll(() => posted.length).toBe(1);
  expect(posted[0].session_id).toBe('session-one');
  expect(posted[0].path).toBe('/home/leo');
  await expect(page.locator('#workspace-indicator-btn')).toBeVisible();
  await expect(page.locator('#workspace-indicator-name')).toHaveText('leo');
});

test('picker shows actionable copy when the server refuses (403)', async ({ page }) => {
  await mockApi(page, {
    browseStatus: 403,
    browseDetail: 'Workspace browsing requires the installation admin. Sign in as an admin account to choose a workspace.',
  });
  await openPicker(page);
  await expect(page.locator('#workspace-body')).toContainText('admin');
  await expect(page.locator('#workspace-body')).toContainText('Sign in');
});

test('a signed-out session is sent to sign in (401)', async ({ page }) => {
  await mockApi(page, { browseStatus: 401 });
  await page.goto('/static/index.html#session-one');
  await waitForBoot(page);
  // The app's global fetch wrapper redirects any 401 (except /api/auth/) to
  // /login, so a signed-out user gets the actionable sign-in page instead of
  // the old generic "Could not browse folders" toast. Fire the picker WITHOUT
  // awaiting it in the page context: the redirect destroys that JS context and
  // an awaited evaluate would reject with "Execution context was destroyed".
  await page.evaluate(() => {
    import('/static/js/workspace.js').then((module) => {
      (module.default || module).openWorkspaceBrowser();
    });
  });
  await page.waitForURL('**/login**', { timeout: 10_000 });
  await expect(page).toHaveURL(/\/login/);
});
