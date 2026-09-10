import { expect, test } from '@playwright/test';

const THREAD_ID = '019f5022-a520-7de0-9208-018cd2d4d222';

const selector = {
  discovery: {
    schema_version: 'pandamonium.discovery.v1',
    generated_at: '2026-09-05T12:00:00Z',
    entities: [{
      kind: 'agent', id: 'agent:jarvis', display_name: 'Jarvis', availability: 'available',
      ownership: { scope: 'owner', id: 'owner:current' }, health: { state: 'healthy' },
      permissions: { requires_authenticated_request: true, configured_scopes: ['owner:current'], delegation: 'narrower_only' },
      source: { type: 'configuration', ref: 'tests/fixtures.py#agent' }, actions: [],
    }, {
      kind: 'agent', id: 'agent:gordon', display_name: 'Gordon', availability: 'unavailable',
      ownership: { scope: 'installation', id: 'installation:current' }, health: { state: 'unavailable', reason: 'connection_failed' },
      permissions: { requires_authenticated_request: true, configured_scopes: ['owner:current'], delegation: 'narrower_only' },
      source: { type: 'worker', ref: 'tests/fixtures.py#worker' }, actions: [],
    }, {
      kind: 'worker', id: 'worker:pc', display_name: 'Friday', availability: 'available',
      ownership: { scope: 'installation', id: 'installation:current' }, health: { state: 'healthy' },
      permissions: { requires_authenticated_request: true, configured_scopes: ['workspace:test-project'], delegation: 'narrower_only' },
      source: { type: 'worker', ref: 'tests/fixtures.py#worker' }, actions: [],
    }],
  },
  selections: [
    { entity_id: 'agent:jarvis', kind: 'agent', target: 'jarvis', selectable: true, reason: null },
    { entity_id: 'agent:gordon', kind: 'agent', target: 'hermes', selectable: false, reason: 'connection_failed' },
    { entity_id: 'worker:pc', kind: 'worker', target: 'pc-codex', capabilities: ['codex'], runtime: 'Codex', location: 'Local workstation', selectable: true, reason: null },
  ],
};

const projectCatalog = [{
  project_id: 'test-project', display_name: 'Disposable Test Project',
  approved_root: 'workspace:test-project', availability: 'available', task_count: 51,
}, {
  project_id: 'missing-project', display_name: 'Missing Project',
  approved_root: 'workspace:missing-project', availability: 'unavailable', reason: 'project_root_unavailable',
}];

function taskPage(cursor = null) {
  const offset = cursor ? 50 : 0;
  const count = cursor ? 1 : 50;
  return {
    project_id: 'test-project',
    items: Array.from({ length: count }, (_, index) => ({
      task_id: index === 0 && !cursor ? THREAD_ID : `task-${offset + index}`,
      project_id: 'test-project',
      title: index === 0 && !cursor ? 'Fixture resume task' : `Fixture task ${offset + index}`,
      status: index === 0 && !cursor ? 'running' : 'idle',
      created_at: 1,
      updated_at: 2,
    })),
    next_cursor: cursor ? null : 'page-two',
  };
}

async function mockShell(page, { sessions = [], catalog = projectCatalog, onChat = null, pinned = [], preferences = {} } = {}) {
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/selector-catalog') return route.fulfill({ json: selector });
    if (url.pathname === '/api/auth/status') return route.fulfill({ json: { username: 'tester', is_admin: true, privileges: {} } });
    if (url.pathname === '/api/models') return route.fulfill({ json: { items: [] } });
    if (url.pathname === '/api/default-chat') return route.fulfill({ json: {} });
    if (url.pathname === '/api/sessions') return route.fulfill({ json: sessions });
    if (url.pathname === '/api/model-endpoints') return route.fulfill({ json: [] });
    if (url.pathname === '/api/codex/models') return route.fulfill({ json: {
      items: [{ model: 'fixture-model', display_name: 'Fixture Codex', reasoning_efforts: ['medium', 'high'], default_reasoning_effort: 'medium' }],
    } });
    if (url.pathname.startsWith('/api/prefs/')) {
      const key = url.pathname.split('/').pop();
      if (route.request().method() === 'PUT') preferences[key] = route.request().postDataJSON().value;
      return route.fulfill({ json: { value: preferences[key] ?? null } });
    }
    if (url.pathname === '/api/codex/projects') return route.fulfill({ json: { items: catalog, pinned_tasks: pinned, next_cursor: null } });
    if (url.pathname.startsWith('/api/codex/projects/test-project/tasks/')) return route.fulfill({ json: {
      ...taskPage().items[0], cwd: '/work/disposable', model: 'recorded-model', recorded_branch: 'recorded-branch',
      sources: ['attached.png'], tools: ['portal/list_services'], outputs: ['src/fix.py'], activity_available: true,
    } });
    if (url.pathname === '/api/codex/projects/test-project/tasks') {
      return route.fulfill({ json: taskPage(url.searchParams.get('cursor')) });
    }
    if (url.pathname === '/api/chat_stream' && onChat) return onChat(route);
    if (url.pathname.endsWith('/events')) {
      return route.fulfill({ status: 200, headers: { 'Content-Type': 'text/event-stream' }, body: '' });
    }
    return route.fulfill({ json: {} });
  });
}

async function selectFriday(page) {
  await page.locator('#model-picker-btn').click();
  await page.locator('#model-picker-list').getByText('Friday', { exact: true }).click();
  await expect(page.locator('#model-picker-menu')).toBeHidden();
  await expect(page.locator('#codex-workspace-browser')).toBeVisible();
}

test('sidebar chrome is fixed, resizable, and uses one scrolling middle region', async ({ page }) => {
  await mockShell(page);
  await page.goto('/static/index.html');

  await expect(page.locator('#sidebar-header-search, #sidebar-search-btn')).toHaveCount(1);
  await expect(page.locator('.sidebar-header > #sidebar-search-btn')).toBeVisible();
  await expect(page.locator('.sidebar-primary-actions > #sidebar-new-chat-btn')).toContainText('New task');
  await expect(page.locator('.sidebar-inner #sidebar-new-chat-btn')).toHaveCount(0);
  await expect(page.locator('#codex-action-view, #codex-task-prompt, #codex-task-write-consent')).toHaveCount(0);

  const before = await page.locator('#sidebar').boundingBox();
  const handle = page.locator('#sidebar-resize-handle');
  const edge = await handle.boundingBox();
  await handle.dispatchEvent('mousedown', { clientX: edge.x + edge.width / 2, clientY: edge.y + 120 });
  await page.mouse.move(edge.x + 70, edge.y + 120);
  await page.mouse.up();
  const after = await page.locator('#sidebar').boundingBox();
  expect(after.width).toBeGreaterThan(before.width + 40);

  const fixedState = await page.evaluate(() => {
    const inner = document.querySelector('.sidebar-inner');
    const footer = document.querySelector('.sidebar-user-bar');
    const y = footer.getBoundingClientRect().y;
    inner.scrollTop = inner.scrollHeight;
    return new Promise(resolve => requestAnimationFrame(() => resolve({
      overflow: getComputedStyle(inner).overflowY,
      footerBefore: y,
      footerAfter: footer.getBoundingClientRect().y,
    })));
  });
  expect(fixedState.overflow).toMatch(/auto|scroll/);
  expect(fixedState.footerAfter).toBe(fixedState.footerBefore);
});

test('Friday shows readable project folders and nested tasks without a sidebar form', async ({ page }) => {
  await mockShell(page);
  await page.goto('/static/index.html');
  await selectFriday(page);

  await expect(page.locator('#chats-section-label')).toHaveText('Chats');
  await expect(page.locator('#codex-browser-title')).toHaveText('Projects');
  const project = page.locator('.codex-project-row').filter({ hasText: 'Disposable Test Project' });
  await expect(project.locator('.codex-browser-icon')).toBeVisible();
  await expect(project.locator('small')).toHaveCount(0);
  const titleBox = await project.locator('.codex-browser-label').boundingBox();
  expect(titleBox.height).toBeGreaterThan(10);
  await expect(page.locator('.codex-project-row').filter({ hasText: 'Missing Project' })).toBeDisabled();

  await project.click();
  await expect(project).toHaveAttribute('aria-expanded', 'true');
  await expect(page.locator('#codex-task-list .codex-task-row')).toHaveCount(5);
  await page.locator('#codex-task-more').click();
  await expect(page.locator('#codex-task-list .codex-task-row')).toHaveCount(10);
  for (let count = 15; count <= 55; count += 5) {
    await page.locator('#codex-task-more').click();
    await expect(page.locator('#codex-task-list .codex-task-row')).toHaveCount(Math.min(count, 51));
  }
  await expect(page.locator('#codex-task-more')).toBeHidden();
  expect(await page.locator('#codex-task-list').evaluate(list => getComputedStyle(list).overflowY)).toBe('visible');

  await page.getByText('Fixture resume task', { exact: true }).click();
  await expect(page.locator('#message:visible')).toHaveAttribute('placeholder', /Message Friday about Fixture resume task/);
  await expect(page.locator('#codex-workspace-browser textarea')).toHaveCount(0);
  await expect(page.locator('#session-context-environment')).toContainText('/work/disposable');
  await expect(page.locator('#session-context-environment')).toContainText('recorded-branch');
  await expect(page.locator('#session-context-tools')).toHaveText('portal/list_services');
  await expect(page.locator('#session-context-sources')).toHaveText('attached.png');
  await expect(page.locator('#session-context-outputs')).toHaveText('src/fix.py');
  await project.click();
  await expect(page.locator('#session-context-environment')).not.toContainText('/work/disposable');
  await expect(page.locator('#session-context-tools')).toHaveText('No tools recorded');
});

test('Chats reuses the Tools ripple when Friday projects collapse and expand', async ({ page }) => {
  await mockShell(page);
  await page.goto('/static/index.html');
  await selectFriday(page);

  const section = page.locator('#sessions-section');
  const title = page.locator('#chats-section-title');
  const firstProject = page.locator('#codex-project-list > .codex-project-group').first();

  await title.click();
  await expect(section).toHaveClass(/section-just-collapsing/);
  await expect.poll(() => firstProject.evaluate(row => (
    row.getAnimations().map(animation => animation.animationName)
  ))).toContain('section-domino-out');
  await expect(section).toHaveClass(/collapsed/);

  await title.click();
  await expect(section).toHaveClass(/section-just-expanded/);
  await expect.poll(() => firstProject.evaluate(row => (
    row.getAnimations().map(animation => animation.animationName)
  ))).toContain('section-domino-in');

  // A quick reversal must settle cleanly instead of leaving an animation class behind.
  await title.click();
  await expect(section).toHaveClass(/collapsed/);
  await title.click();
  await expect(section).not.toHaveClass(/collapsed|section-just-collapsing/);
});

test('chat project folders ripple their direct rows when collapsed and expanded', async ({ page }) => {
  const now = new Date().toISOString();
  const yesterday = new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString();
  const sessions = [
    { id: 'folder-now', name: 'Current project chat', model: 'fixture', archived: false, folder: 'Home Lab', agent_target: 'jarvis', created_at: now, updated_at: now, last_message_at: now },
    { id: 'folder-old', name: 'Earlier project chat', model: 'fixture', archived: false, folder: 'Home Lab', agent_target: 'jarvis', created_at: yesterday, updated_at: yesterday, last_message_at: yesterday },
    { id: 'unfiled-now', name: 'Current unfiled chat', model: 'fixture', archived: false, agent_target: 'jarvis', created_at: now, updated_at: now, last_message_at: now },
  ];
  await page.addInitScript(() => localStorage.setItem('lastSessionId', 'folder-now'));
  await mockShell(page, { sessions });
  await page.goto('/static/index.html');
  await expect.poll(() => page.evaluate(() => window.sessionModule?.getSessions?.().length)).toBe(3);

  const assertFolderRipple = async (folderName) => {
    const folder = page.locator(`.session-folder[data-folder-key="${folderName}"]`);
    const header = folder.locator(':scope > .session-folder-header');
    const rows = folder.locator(':scope > .session-folder-content > :is(.date-section-header, .list-item)');

    await header.click();
    await expect(folder).toHaveClass(/session-folder-just-collapsing/);
    await expect.poll(() => rows.first().evaluate(row => (
      row.getAnimations().map(animation => animation.animationName)
    ))).toContain('section-domino-out');
    await expect(folder.locator(':scope > .session-folder-content')).toHaveCount(0);
    await expect(header).toBeVisible();

    await header.click();
    await expect(folder).toHaveClass(/session-folder-just-expanded/);
    await expect.poll(() => rows.first().evaluate(row => (
      row.getAnimations().map(animation => animation.animationName)
    ))).toContain('section-domino-in');
  };

  await assertFolderRipple('Home Lab');
  await expect(page.locator('#session-list .unsorted-folder')).toHaveCount(0);
  await expect(page.locator('#session-list .session-unfiled-region')).toContainText('Current unfiled chat');

  const homeLab = page.locator('.session-folder[data-folder-key="Home Lab"]');
  await homeLab.locator(':scope > .session-folder-header').click();
  await expect(homeLab).toHaveClass(/session-folder-just-collapsing/);
  await homeLab.locator(':scope > .session-folder-header').click();
  await expect(homeLab).toHaveClass(/session-folder-just-expanded/);
  await expect(homeLab.locator(':scope > .session-folder-content')).toBeVisible();
  await expect.poll(() => page.evaluate(() => (
    JSON.parse(localStorage.getItem('odysseus-folder-state') || '{}')['Home Lab']
  ))).toBe(true);
});

test('Chats stay agent-scoped while pinned chats and project folders keep their hierarchy', async ({ page }) => {
  const now = new Date().toISOString();
  const sessions = [
    { id: 'jarvis-pin', name: 'Pinned Jarvis chat', model: 'fixture', archived: false, is_important: true, agent_target: 'jarvis', created_at: now, updated_at: now, last_message_at: now },
    { id: 'jarvis-project', name: 'Project Jarvis chat', model: 'fixture', archived: false, folder: 'Home Lab', agent_target: 'jarvis', created_at: now, updated_at: now, last_message_at: now },
    { id: 'jarvis-chat', name: 'Regular Jarvis chat', model: 'fixture', archived: false, agent_target: 'jarvis', created_at: now, updated_at: now, last_message_at: now },
    { id: 'gordon-chat', name: 'Gordon chat', model: 'fixture', archived: false, agent_target: 'hermes', created_at: now, updated_at: now, last_message_at: now },
    { id: 'friday-chat', name: 'Friday chat', model: 'fixture', archived: false, agent_target: 'pc-codex', created_at: now, updated_at: now, last_message_at: now },
  ];
  await page.addInitScript(() => localStorage.setItem('lastSessionId', 'jarvis-chat'));
  await mockShell(page, { sessions });
  await page.goto('/static/index.html');
  await expect.poll(() => page.evaluate(() => window.sessionModule?.getSessions?.().length)).toBe(5);

  const activate = id => page.evaluate(async sessionId => {
    const module = await import('/static/js/sessions.js');
    module.setCurrentSessionId(sessionId);
    module.updateModelPicker();
  }, id);

  await activate('jarvis-chat');
  await expect(page.locator('#chats-section-label')).toHaveText('Chats');
  await expect(page.locator('#session-list .sidebar-nav-label')).toHaveText(['Pinned', 'Projects']);
  await expect(page.locator('#session-list')).toContainText('Pinned Jarvis chat');
  await expect(page.locator('#session-list .session-folder-header[data-folder-name="Home Lab"]')).toContainText('Home Lab');
  await expect(page.locator('#session-list')).not.toContainText('Gordon chat');

  await activate('gordon-chat');
  await expect(page.locator('#chats-section-label')).toHaveText('Chats');
  await expect(page.locator('#session-list .session-item')).toHaveCount(1);
  await expect(page.locator('#session-list')).toContainText('Gordon chat');

  await page.locator('#model-picker-btn').click();
  const gordon = page.locator('#model-picker-list').getByText('Gordon', { exact: true }).locator('..');
  await expect(gordon).toBeVisible();
  await expect(gordon).toHaveAttribute('aria-disabled', 'true');
});

test('selected Friday project and task flow through the normal composer', async ({ page }) => {
  const now = new Date().toISOString();
  const fridaySession = {
    id: 'friday-chat', name: 'Friday task chat', model: 'fixture', endpoint_url: 'http://model.test',
    archived: false, agent_target: 'pc-codex', created_at: now, updated_at: now, last_message_at: now, message_count: 1,
  };
  await page.addInitScript(() => localStorage.setItem('lastSessionId', 'friday-chat'));
  let submitted = '';
  await mockShell(page, {
    sessions: [fridaySession],
    onChat: route => {
      submitted = route.request().postData() || '';
      const task = {
        task_id: 'direct-friday-task', worker: 'pc-codex', presenter: 'Friday',
        session_id: 'friday-chat', workspace: 'test-project', status: 'running', codex_thread_id: THREAD_ID,
      };
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: [
          `data: ${JSON.stringify({ type: 'model_info', model: 'Friday', character_name: 'Friday' })}\n\n`,
          `data: ${JSON.stringify({ type: 'agent_task', action: 'started', ...task })}\n\n`,
          `data: ${JSON.stringify({ type: 'metrics', data: { model: 'Friday', route: 'pc-codex' } })}\n\n`,
          'data: [DONE]\n\n',
        ].join(''),
      });
    },
  });
  await page.goto('/static/index.html');
  await expect.poll(() => page.evaluate(() => Boolean(window.sessionModule))).toBe(true);
  await page.evaluate(async () => {
    const module = await import('/static/js/sessions.js');
    module.setCurrentSessionId('friday-chat');
    module.updateModelPicker();
  });
  await expect(page.locator('#codex-workspace-browser')).toBeVisible();
  await page.getByText('Disposable Test Project', { exact: true }).click();
  await page.getByText('Fixture resume task', { exact: true }).click();
  await page.locator('#model-picker-btn').click();
  await page.locator('#model-picker-refresh-btn').click();
  await expect(page.locator('#message:visible')).toHaveAttribute('placeholder', /Fixture resume task/);
  await expect(page.locator('#session-context-environment')).toContainText('recorded-branch');
  await page.locator('#codex-model').selectOption('fixture-model');
  await expect(page.locator('#codex-reasoning')).toHaveValue('medium');
  await page.locator('#conversation-effort').focus();
  await page.locator('#conversation-effort').press('ArrowRight');
  await expect(page.locator('#codex-reasoning')).toHaveValue('high');
  await expect(page.locator('#conversation-effort-value')).toHaveText('High');
  await page.screenshot({ path: test.info().outputPath('friday-model-selection.png'), animations: 'disabled' });
  await page.locator('#model-picker-btn').click();
  await page.locator('#message:visible').fill('Inspect the selected project.');
  await page.locator('.send-btn:visible').click();

  await expect.poll(() => submitted).toContain('worker_workspace');
  expect(submitted).toContain('test-project');
  expect(submitted).toContain('worker_thread_id');
  expect(submitted).toContain(THREAD_ID);
  expect(submitted).toContain('codex_model');
  expect(submitted).toContain('fixture-model');
  expect(submitted).toContain('codex_reasoning_effort');
  expect(submitted).toContain('high');
  await expect(page.locator('.jarvis-task-activity[data-task-id="direct-friday-task"]')).toContainText('Friday');
  await expect(page.locator('#session-context-environment')).toContainText('recorded-branch');
  await page.setViewportSize({ width: 390, height: 844 });
  if (!await page.locator('#sidebar').evaluate(sidebar => sidebar.classList.contains('hidden'))) await page.locator('#hamburger-btn').click();
  await page.locator('#model-picker-btn').click();
  await expect(page.locator('#session-context-panel')).toBeHidden();
  await page.locator('#conversation-effort').focus();
  await page.locator('#conversation-effort').press('Home');
  await expect(page.locator('#codex-reasoning')).toHaveValue('medium');
  const bounds = await page.locator('#model-picker-menu').boundingBox();
  expect(bounds.x).toBeGreaterThanOrEqual(0);
  expect(bounds.y).toBeGreaterThanOrEqual(0);
  expect(bounds.x + bounds.width).toBeLessThanOrEqual(390);
  const composer = await page.locator('.chat-input-bar:visible').boundingBox();
  expect(composer.x).toBeGreaterThanOrEqual(0);
  expect(composer.x + composer.width).toBeLessThanOrEqual(391);
  await page.screenshot({ path: test.info().outputPath('friday-model-selection-mobile.png'), animations: 'disabled' });
});

test('Friday imports pins and custom order, then persists pin and drag changes in Pandamonium', async ({ page }) => {
  const preferences = {};
  await mockShell(page, {
    preferences, pinned: [taskPage().items[0]],
    catalog: [{ ...projectCatalog[0], task_order: ['task-50', 'task-3'] }, projectCatalog[1]],
  });
  await page.goto('/static/index.html');
  await selectFriday(page);
  await expect(page.locator('#codex-pinned-view')).toContainText('Fixture resume task');
  await page.locator('.codex-project-row').first().click();
  const rows = page.locator('#codex-task-list .codex-task-row');
  await expect(rows).toHaveCount(5);
  await expect(rows.first()).toContainText('Fixture task 50');
  await expect(rows.nth(1)).toContainText('Fixture task 3');
  await rows.nth(1).focus();
  await rows.nth(1).press('Alt+ArrowUp');
  await expect.poll(() => preferences['codex-sidebar-layout']?.tasks['test-project']?.[0]).toBe('task-3');
  await page.getByRole('button', { name: 'Pin Fixture task 3', exact: true }).click();
  await expect(page.locator('#codex-pinned-list .codex-task-row')).toHaveCount(2);

  const handle = page.locator('.codex-project-row .project-drag').first();
  const start = await handle.boundingBox();
  const destination = await page.locator('.codex-project-row').nth(1).boundingBox();
  await handle.dispatchEvent('mousedown', { button: 0, clientX: start.x + 3, clientY: start.y + 3 });
  await page.mouse.move(start.x + 3, destination.y + destination.height - 2, { steps: 8 });
  await page.mouse.up();
  await expect.poll(() => preferences['codex-sidebar-layout']?.projects?.[0]).toBe('missing-project');
  await page.reload();
  await selectFriday(page);
  await expect(page.locator('.codex-project-row').first()).toContainText('Missing Project');
  await expect(page.locator('#codex-pinned-list .codex-task-row')).toHaveCount(2);
  await page.getByRole('button', { name: 'Unpin Fixture task 3', exact: true }).click();
  await page.getByText('Disposable Test Project', { exact: true }).click();
  await expect(page.locator('#codex-task-list .codex-task-row').first()).toContainText('Fixture task 3');
});

test('Jarvis budget slider submits a work budget and resets to the installation default', async ({ page }) => {
  const now = new Date().toISOString();
  const sessions = [{ id: 'budget-chat', name: 'Budget chat', model: 'fixture', endpoint_url: 'http://model.test',
    agent_target: 'jarvis', created_at: now, updated_at: now, message_count: 1 }];
  let submitted = '';
  await page.addInitScript(() => localStorage.setItem('lastSessionId', 'budget-chat'));
  await mockShell(page, { sessions, onChat: route => {
    submitted = route.request().postData() || '';
    return route.fulfill({ headers: { 'Content-Type': 'text/event-stream' }, body: 'data: {"delta":"Done."}\n\ndata: [DONE]\n\n' });
  } });
  await page.goto('/static/index.html');
  await expect.poll(() => page.evaluate(() => window.sessionModule?.getSessions?.().length)).toBe(1);
  await page.evaluate(async () => {
    const module = await import('/static/js/sessions.js');
    module.setCurrentSessionId('budget-chat');
    module.updateModelPicker();
  });
  await page.locator('#model-picker-btn').click();
  await expect(page.locator('#conversation-effort-label')).toHaveText('Agent work budget');
  await page.locator('#conversation-effort').focus();
  await page.locator('#conversation-effort').press('Home');
  await expect(page.locator('#conversation-effort-help')).toContainText('Up to 8 rounds');
  await page.locator('#model-picker-btn').click();
  await page.locator('#message:visible').fill('Inspect the current state.');
  await page.locator('.send-btn:visible').click();
  await expect.poll(() => submitted).toContain('agent_effort');
  expect(submitted).toMatch(/name="agent_effort"\r?\n\r?\nlow/);
  await page.locator('#model-picker-btn').click();
  await page.locator('#conversation-effort-reset').click();
  await expect(page.locator('#conversation-effort-value')).toHaveText('Default');
});


test('rounded workspace controls follow theme colors on desktop and phone', async ({ page }) => {
  await mockShell(page);
  await page.goto('/static/index.html');
  await selectFriday(page);
  await page.locator('#model-picker-btn').click();
  await page.locator('#codex-model').selectOption('fixture-model');
  await page.locator('#conversation-effort').press('ArrowRight');
  await expect(page.locator('#conversation-effort-value')).toHaveText('High');
  await expect(page.locator('#conversation-effort')).toHaveCSS('--effort-fill', '100%');
  const themes = [
    { bg: '#09080c', fg: '#faf5ed', panel: '#110f15', border: '#343036', red: '#ff294f' },
    { bg: '#f0f4ec', fg: '#203018', panel: '#fcfff7', border: '#a2b396', red: '#347020' },
  ];
  for (const [index, colors] of themes.entries()) {
    await page.evaluate(async colors => (await import('/static/js/theme.js')).applyColors(colors), colors);
    await expect(page.locator('#model-picker-menu')).toHaveCSS('border-radius', '24px');
    await expect(page.locator('#codex-model')).toHaveCSS('border-radius', '999px');
    const expected = await page.evaluate(colors => {
      const probe = document.createElement('span');
      document.body.append(probe);
      probe.style.color = colors.red;
      const accent = getComputedStyle(probe).color;
      probe.style.color = colors.panel;
      const panel = getComputedStyle(probe).color;
      probe.remove();
      return { accent, panel };
    }, colors);
    await expect(page.locator('#conversation-effort-value')).toHaveCSS('color', expected.accent);
    await page.screenshot({ path: test.info().outputPath(`workspace-theme-${index}.png`), animations: 'disabled' });
    await page.locator('#model-picker-btn').click();
    if (!await page.locator('#session-context-panel').isVisible()) await page.locator('#session-context-toggle').click();
    await expect(page.locator('#session-context-panel')).toHaveCSS('background-color', expected.panel);
    await expect(page.locator('#session-context-panel')).toContainText('Local workstation');
    await page.screenshot({ path: test.info().outputPath(`details-theme-${index}.png`), animations: 'disabled' });
    await page.locator('#session-context-close').click();
    await page.locator('#model-picker-btn').click();
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: test.info().outputPath('workspace-phone.png'), animations: 'disabled' });
  const menu = await page.locator('#model-picker-menu').boundingBox();
  expect(menu.x).toBeGreaterThanOrEqual(0);
  expect(menu.x + menu.width).toBeLessThanOrEqual(390);
});
