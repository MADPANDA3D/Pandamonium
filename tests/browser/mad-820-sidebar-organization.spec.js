import { expect, test } from '@playwright/test';

const PROJECT_ORDER_PREF_PATH = '/api/prefs/sidebar-project-order';

function sessionFixture(id, name, { folder = null, pinned = false, minutes = 0 } = {}) {
  const timestamp = new Date(Date.now() - minutes * 60_000).toISOString();
  return {
    id,
    name,
    model: 'test/model',
    endpoint_url: 'http://model.test/v1/chat/completions',
    message_count: 2,
    archived: false,
    is_important: pinned,
    agent_target: 'jarvis',
    folder,
    created_at: timestamp,
    updated_at: timestamp,
    last_message_at: timestamp,
  };
}

async function mockShell(page, state) {
  await page.route('**/api/**', async route => {
    const request = route.request();
    const url = new URL(request.url());

    if (url.pathname === PROJECT_ORDER_PREF_PATH) {
      if (request.method() === 'PUT') {
        const body = request.postDataJSON();
        state.puts.push(body);
        if (state.prefStatus >= 400) return route.fulfill({ status: state.prefStatus, json: {} });
        state.remoteOrder = body.value;
        return route.fulfill({ json: { key: 'sidebar-project-order', value: state.remoteOrder } });
      }
      if (state.prefStatus >= 400) return route.fulfill({ status: state.prefStatus, json: {} });
      return route.fulfill({
        json: { key: 'sidebar-project-order', value: state.remoteOrder },
      });
    }
    if (url.pathname.startsWith('/api/prefs/')) {
      return route.fulfill({ json: { key: url.pathname.split('/').pop(), value: null } });
    }
    if (url.pathname === '/api/sessions') return route.fulfill({ json: state.sessions });
    if (url.pathname === '/api/auth/status') {
      return route.fulfill({ json: { username: 'tester', is_admin: false, privileges: {} } });
    }
    if (url.pathname === '/api/model-endpoints' || url.pathname === '/api/models') {
      return route.fulfill({ json: [] });
    }
    if (url.pathname === '/api/default-chat') return route.fulfill({ json: {} });
    return route.fulfill({ json: {} });
  });
}

function projectOrder(page) {
  return page.locator('#session-list > .session-folder[data-folder-name]').evaluateAll(
    folders => folders.map(folder => folder.dataset.folderName),
  );
}

async function dragBefore(page, sourceId, targetId) {
  await page.evaluate(({ sourceId, targetId }) => {
    const source = document.querySelector(`.list-item[data-session-id="${sourceId}"]`);
    const target = document.querySelector(`.list-item[data-session-id="${targetId}"]`);
    const handle = source.querySelector('.item-drag-handle');
    const from = handle.getBoundingClientRect();
    const to = target.getBoundingClientRect();
    const event = (type, clientX, clientY) => new MouseEvent(type, {
      bubbles: true, button: 0, clientX, clientY,
    });
    handle.dispatchEvent(event('mousedown', from.left + from.width / 2, from.top + from.height / 2));
    document.dispatchEvent(event('mousemove', to.left + 8, Math.max(1, to.top - 8)));
    document.dispatchEvent(event('mouseup', to.left + 8, Math.max(1, to.top - 8)));
  }, { sourceId, targetId });
}

test('unfiled chats stay above Projects in their own bounded region without a synthetic folder', async ({ page }) => {
  const state = {
    sessions: [
      ...Array.from({ length: 18 }, (_, index) => sessionFixture(
        `chat-${String(index).padStart(2, '0')}`,
        `Recent chat ${index + 1}`,
        { minutes: index },
      )),
      sessionFixture('project-alpha', 'Alpha project chat', { folder: 'Alpha', minutes: 30 }),
      sessionFixture('project-beta', 'Beta project chat', { folder: 'Beta', minutes: 31 }),
    ],
    remoteOrder: null,
    prefStatus: 200,
    puts: [],
  };
  await page.setViewportSize({ width: 1280, height: 500 });
  await mockShell(page, state);
  await page.goto('/static/index.html');
  await expect.poll(() => page.evaluate(() => window.sessionModule?.getSessions?.().length)).toBe(20);

  const unfiled = page.locator('#session-list > .session-unfiled-region');
  await expect(unfiled).toHaveAttribute('role', 'group');
  await expect(unfiled).toHaveAttribute('aria-label', 'Recent chats');
  await expect(page.locator('#session-list .unsorted-folder')).toHaveCount(0);
  await expect(page.locator('#session-list .session-folder-header .folder-name', { hasText: /^Chats$/ })).toHaveCount(0);
  await expect(unfiled.locator('.session-item')).toHaveCount(10);

  const topLevelOrder = await page.locator('#session-list').evaluate(list => (
    Array.from(list.children).map(child => {
      if (child.classList.contains('session-unfiled-region')) return 'recent';
      if (child.classList.contains('sidebar-nav-label')) return `label:${child.textContent.trim()}`;
      if (child.classList.contains('session-folder')) return `project:${child.dataset.folderName}`;
      return child.className;
    })
  ));
  expect(topLevelOrder.slice(0, 2)).toEqual(['recent', 'label:Projects']);

  await unfiled.getByRole('button', { name: 'Show 8 more' }).click();
  await expect(unfiled.locator('.session-item')).toHaveCount(18);
  const bounded = await unfiled.evaluate(region => ({
    clientHeight: region.clientHeight,
    scrollHeight: region.scrollHeight,
    overflowY: getComputedStyle(region).overflowY,
    viewportHeight: window.innerHeight,
  }));
  expect(bounded.overflowY).toBe('auto');
  expect(bounded.scrollHeight).toBeGreaterThan(bounded.clientHeight);
  expect(bounded.clientHeight).toBeLessThanOrEqual(Math.ceil(bounded.viewportHeight * 0.34) + 1);

  await page.setViewportSize({ width: 390, height: 640 });
  if (await page.locator('#sidebar').evaluate(sidebar => sidebar.classList.contains('hidden'))) {
    await page.locator('#hamburger-btn').click();
  }
  await expect(unfiled).toBeVisible();
  const narrow = await unfiled.evaluate(region => ({
    clientWidth: region.clientWidth,
    scrollWidth: region.scrollWidth,
  }));
  expect(narrow.scrollWidth).toBeLessThanOrEqual(narrow.clientWidth + 1);

  state.sessions = [];
  await page.evaluate(() => window.sessionModule.loadSessions());
  await expect(page.locator('#session-list')).toBeEmpty();
  await expect(page.locator('#session-list .session-unfiled-region, #session-list .session-folder')).toHaveCount(0);
});

test('pointer and keyboard project reorder persist through the authenticated preference API', async ({ browser, page }) => {
  const state = {
    sessions: [
      sessionFixture('alpha', 'Alpha chat', { folder: 'Alpha' }),
      sessionFixture('beta', 'Beta chat', { folder: 'Beta' }),
      sessionFixture('gamma', 'Gamma chat', { folder: 'Gamma' }),
      sessionFixture('loose', 'Unfiled chat'),
    ],
    remoteOrder: ['Gamma', 'Alpha', 'Beta'],
    prefStatus: 200,
    puts: [],
  };
  await page.addInitScript(() => {
    localStorage.setItem('odysseus-folder-order', JSON.stringify(['Beta', 'Alpha', 'Gamma']));
    localStorage.setItem('odysseus-folder-state', JSON.stringify({ Alpha: false, Beta: false, Gamma: false }));
  });
  await mockShell(page, state);
  await page.goto('/static/index.html');
  await expect.poll(() => projectOrder(page)).toEqual(['Gamma', 'Alpha', 'Beta']);

  const gammaHandle = page.locator('.session-folder[data-folder-name="Gamma"] .folder-drag-handle');
  await expect(gammaHandle).toHaveAttribute('role', 'button');
  await expect(gammaHandle).toHaveAttribute('aria-keyshortcuts', 'Alt+ArrowUp Alt+ArrowDown');
  await gammaHandle.focus();
  await gammaHandle.press('Alt+ArrowDown');
  await expect.poll(() => projectOrder(page)).toEqual(['Alpha', 'Gamma', 'Beta']);
  await expect(page.locator('.session-folder[data-folder-name="Gamma"] .folder-drag-handle')).toBeFocused();
  await expect.poll(() => state.puts.at(-1)?.value).toEqual(['Alpha', 'Gamma', 'Beta']);

  await expect(page.locator('#app-loader')).toHaveCount(0);
  const betaHandle = page.locator('.session-folder[data-folder-name="Beta"] .folder-drag-handle');
  const betaFolder = page.locator('.session-folder[data-folder-name="Beta"]');
  const alphaFolder = page.locator('.session-folder[data-folder-name="Alpha"]');
  const betaBox = await betaHandle.boundingBox();
  const alphaBox = await alphaFolder.boundingBox();
  expect(betaBox).not.toBeNull();
  expect(alphaBox).not.toBeNull();
  expect(await page.evaluate(({ x, y }) => (
    document.elementFromPoint(x, y)?.closest('.folder-drag-handle')?.getAttribute('aria-label')
  ), { x: betaBox.x + betaBox.width / 2, y: betaBox.y + betaBox.height / 2 })).toContain('Beta');
  await page.mouse.move(betaBox.x + betaBox.width / 2, betaBox.y + betaBox.height / 2);
  await page.mouse.down();
  await expect(betaFolder).toHaveClass(/dragging/);
  await page.mouse.move(alphaBox.x + 8, Math.max(1, alphaBox.y - 8), { steps: 8 });
  await page.mouse.up();
  await expect.poll(() => projectOrder(page)).toEqual(['Beta', 'Alpha', 'Gamma']);
  await expect.poll(() => state.puts.at(-1)?.value).toEqual(['Beta', 'Alpha', 'Gamma']);

  // A separate browser context has no localStorage from the first page; its
  // order therefore proves the authenticated preference readback path.
  const secondContext = await browser.newContext();
  const secondPage = await secondContext.newPage();
  await mockShell(secondPage, state);
  await secondPage.goto('/static/index.html');
  await expect.poll(() => projectOrder(secondPage)).toEqual(['Beta', 'Alpha', 'Gamma']);
  await secondContext.close();
});

test('local project order remains usable when preference storage is unavailable', async ({ page }) => {
  const state = {
    sessions: [
      sessionFixture('alpha', 'Alpha chat', { folder: 'Alpha' }),
      sessionFixture('beta', 'Beta chat', { folder: 'Beta' }),
    ],
    remoteOrder: null,
    prefStatus: 503,
    puts: [],
  };
  await page.addInitScript(() => {
    if (!localStorage.getItem('odysseus-folder-order')) {
      localStorage.setItem('odysseus-folder-order', JSON.stringify(['Beta', 'Alpha']));
    }
  });
  await mockShell(page, state);
  await page.goto('/static/index.html');
  await expect.poll(() => projectOrder(page)).toEqual(['Beta', 'Alpha']);

  await page.locator('.session-folder[data-folder-name="Beta"] .folder-drag-handle').press('Alt+ArrowDown');
  await expect.poll(() => projectOrder(page)).toEqual(['Alpha', 'Beta']);
  await expect.poll(() => page.evaluate(() => (
    JSON.parse(localStorage.getItem('odysseus-folder-order') || '[]')
  ))).toEqual(['Alpha', 'Beta']);

  await page.reload();
  await expect.poll(() => projectOrder(page)).toEqual(['Alpha', 'Beta']);
});

test('pinned and unfiled drag sorting preserve one combined session order', async ({ page }) => {
  const state = {
    sessions: [
      sessionFixture('pin-new', 'Pinned new', { pinned: true, minutes: 0 }),
      sessionFixture('pin-old', 'Pinned old', { pinned: true, minutes: 10 }),
      sessionFixture('loose-new', 'Loose new', { minutes: 0 }),
      sessionFixture('loose-old', 'Loose old', { minutes: 10 }),
    ],
    remoteOrder: null,
    prefStatus: 200,
    puts: [],
  };
  await page.addInitScript(() => {
    if (!localStorage.getItem('odysseus-session-sort')) {
      localStorage.setItem('odysseus-session-sort', 'group');
    }
    if (!localStorage.getItem('session-order')) {
      localStorage.setItem('session-order', JSON.stringify([
        'pin-old', 'loose-old', 'pin-new', 'loose-new',
      ]));
    }
  });
  await mockShell(page, state);
  await page.goto('/static/index.html');
  await page.evaluate(() => document.body.classList.add('rearrange-mode'));

  const ids = selector => page.locator(selector).evaluateAll(
    items => items.map(item => item.dataset.sessionId),
  );
  await expect.poll(() => ids('#session-list > .list-item[data-session-id]'))
    .toEqual(['pin-old', 'pin-new']);
  await dragBefore(page, 'pin-new', 'pin-old');
  await expect.poll(() => page.evaluate(() => JSON.parse(localStorage.getItem('session-order'))))
    .toEqual(['pin-new', 'loose-old', 'pin-old', 'loose-new']);

  await dragBefore(page, 'loose-new', 'loose-old');
  await expect.poll(() => page.evaluate(() => JSON.parse(localStorage.getItem('session-order'))))
    .toEqual(['pin-new', 'loose-new', 'pin-old', 'loose-old']);

  await page.reload();
  await expect.poll(() => ids('#session-list > .list-item[data-session-id]'))
    .toEqual(['pin-new', 'pin-old']);
  await expect.poll(() => ids('#session-unfiled-region .list-item[data-session-id]'))
    .toEqual(['loose-new', 'loose-old']);
});
