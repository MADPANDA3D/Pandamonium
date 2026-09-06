import { expect, test } from '@playwright/test';

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
      kind: 'agent', id: 'agent:gordon', display_name: 'Gordon', availability: 'available',
      ownership: { scope: 'installation', id: 'installation:current' }, health: { state: 'healthy' },
      permissions: { requires_authenticated_request: true, configured_scopes: ['owner:current'], delegation: 'narrower_only' },
      source: { type: 'worker', ref: 'tests/fixtures.py#worker' }, actions: [],
    }, {
      kind: 'worker', id: 'worker:pc', display_name: 'Friday', availability: 'available',
      ownership: { scope: 'installation', id: 'installation:current' },
      health: { state: 'healthy' },
      permissions: { requires_authenticated_request: true, configured_scopes: ['workspace:test-project'], delegation: 'narrower_only' },
      source: { type: 'worker', ref: 'tests/fixtures.py#worker' }, actions: [],
    }],
  },
  selections: [
    { entity_id: 'agent:jarvis', kind: 'agent', target: 'jarvis', selectable: true, reason: null },
    { entity_id: 'agent:gordon', kind: 'agent', target: 'hermes', selectable: true, reason: null },
    { entity_id: 'worker:pc', kind: 'worker', target: 'pc-codex', selectable: true, reason: null },
  ],
};

async function mockShell(page, catalogHandler, taskHandler = null, sessionItems = [], chatHandler = null) {
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/selector-catalog') return route.fulfill({ json: selector });
    if (url.pathname === '/api/auth/status') return route.fulfill({ json: { username: 'tester', is_admin: true, privileges: {} } });
    if (url.pathname === '/api/models') return route.fulfill({ json: { items: [] } });
    if (url.pathname === '/api/default-chat') return route.fulfill({ json: {} });
    if (url.pathname === '/api/sessions') return route.fulfill({ json: sessionItems });
    if (url.pathname === '/api/model-endpoints') return route.fulfill({ json: [] });
    if (url.pathname === '/api/chat_stream' && chatHandler) return chatHandler(route, url);
    if (url.pathname.startsWith('/api/codex/')) return catalogHandler(route, url);
    if (url.pathname.startsWith('/api/agent-tasks') && taskHandler) return taskHandler(route, url);
    return route.fulfill({ json: {} });
  });
}

async function selectFriday(page) {
  await page.locator('#model-picker-btn').click();
  await page.locator('#model-picker-list').getByText('Friday', { exact: true }).click();
  await expect(page.locator('#model-picker-menu')).toBeHidden();
  await expect(page.locator('#codex-workspace-browser')).toBeVisible();
}

test('Codex browser loads allowlisted projects lazily and paginates a large task catalog', async ({ page }) => {
  let taskRequests = 0;
  await mockShell(page, (route, url) => {
    if (url.pathname === '/api/codex/projects') {
      return route.fulfill({ json: {
        items: [
          { project_id: 'test-project', display_name: 'Test Project', approved_root: 'workspace:test-project', availability: 'available', task_count: 1001 },
          { project_id: 'missing-project', display_name: 'Missing Project', approved_root: 'workspace:missing-project', availability: 'unavailable', task_count: 0, reason: 'project_root_unavailable' },
        ],
        next_cursor: null,
      } });
    }
    taskRequests += 1;
    const query = url.searchParams.get('query') || '';
    const cursor = url.searchParams.get('cursor');
    if (query) {
      return route.fulfill({ json: {
        project_id: 'test-project',
        items: [{ task_id: 'task-search', project_id: 'test-project', title: `Match ${query}`, status: 'idle', created_at: 1, updated_at: 2 }],
        next_cursor: null,
      } });
    }
    const offset = cursor ? 50 : 0;
    const count = cursor ? 1 : 50;
    return route.fulfill({ json: {
      project_id: 'test-project',
      items: Array.from({ length: count }, (_, index) => ({
        task_id: `task-${offset + index}`,
        project_id: 'test-project',
        title: `Fixture task ${offset + index}`,
        status: 'idle',
        created_at: 1,
        updated_at: 2,
      })),
      next_cursor: cursor ? null : 'page-two',
    } });
  });

  await page.goto('/static/index.html');
  await expect(page.locator('#codex-browser-toggle')).toHaveCount(0);
  expect(taskRequests).toBe(0);
  await selectFriday(page);
  await expect(page.locator('#sessions-section > #codex-workspace-browser')).toBeVisible();
  await expect(page.locator('#chats-section-label')).toHaveText('Friday Projects');
  await expect(page.locator('#codex-project-list')).toContainText('1,001 tasks · workspace:test-project');
  await expect(page.locator('#codex-project-list').getByText('Missing Project').locator('..')).toBeDisabled();
  expect(taskRequests).toBe(0);

  await page.locator('#codex-project-list').getByText('Test Project').click();
  await expect(page.locator('.codex-project-group').filter({ hasText: 'Test Project' }).locator('#codex-task-view')).toBeVisible();
  await expect(page.locator('#codex-task-list .codex-browser-row')).toHaveCount(50);
  expect(taskRequests).toBe(1);
  await page.locator('#codex-task-more').click();
  await expect(page.locator('#codex-task-list .codex-browser-row')).toHaveCount(51);
  expect(taskRequests).toBe(2);

  await page.locator('#codex-task-search').fill('needle');
  await expect(page.locator('#codex-task-list')).toContainText('Match needle');
  await expect(page.locator('#codex-task-list .codex-browser-row')).toHaveCount(1);
  expect(taskRequests).toBe(3);
});

test('sidebar follows the server-owned target and persists selector changes', async ({ page }) => {
  const now = new Date().toISOString();
  const sessionItems = [
    { id: 'jarvis-chat', name: 'Jarvis dated chat', model: 'fixture', endpoint_url: 'http://model.test', archived: false, agent_target: 'jarvis', created_at: now, updated_at: now, last_message_at: now, message_count: 1 },
    { id: 'gordon-chat', name: 'Gordon dated chat', model: 'fixture', endpoint_url: 'http://model.test', archived: false, agent_target: 'hermes', created_at: now, updated_at: now, last_message_at: now, message_count: 1 },
    { id: 'friday-chat', name: 'Legacy Friday chat', model: 'fixture', endpoint_url: 'http://model.test', archived: false, agent_target: 'pc-codex', created_at: now, updated_at: now, last_message_at: now, message_count: 1 },
  ];
  await mockShell(page, (route, url) => {
    if (url.pathname === '/api/codex/projects') {
      return route.fulfill({ json: { items: [], next_cursor: null } });
    }
    return route.fulfill({ json: { project_id: 'test-project', items: [], next_cursor: null } });
  }, null, sessionItems);
  await page.goto('/static/index.html');
  await expect.poll(() => page.evaluate(() => window.sessionModule?.getSessions?.().length)).toBe(3);

  const activate = id => page.evaluate(async sessionId => {
    const sessions = await import('/static/js/sessions.js');
    sessions.setCurrentSessionId(sessionId);
    sessions.updateModelPicker();
  }, id);

  await activate('jarvis-chat');
  await expect(page.locator('#chats-section-label')).toHaveText('Jarvis Chats');
  await expect(page.locator('#session-list .session-item')).toHaveCount(1);
  await expect(page.locator('#session-list')).toContainText('Jarvis dated chat');
  await expect(page.locator('#session-list .date-section-header')).toHaveText(['Today']);

  await activate('gordon-chat');
  await expect(page.locator('#chats-section-label')).toHaveText('Gordon Chats');
  await expect(page.locator('#session-list .session-item')).toHaveCount(1);
  await expect(page.locator('#session-list')).toContainText('Gordon dated chat');

  const patchRequest = page.waitForRequest(request => (
    request.method() === 'PATCH' && new URL(request.url()).pathname === '/api/session/gordon-chat'
  ));
  await selectFriday(page);
  expect((await patchRequest).postData()).toContain('pc-codex');
  await expect(page.locator('#session-list')).toBeHidden();
  await expect(page.locator('#codex-workspace-browser')).toBeVisible();
  await expect(page.locator('#codex-project-list')).toContainText('No allowlisted Codex projects found');
});

test('Codex browser reports empty and workstation failure states explicitly', async ({ page }) => {
  let fail = false;
  await mockShell(page, (route, url) => {
    if (url.pathname !== '/api/codex/projects') return route.fulfill({ status: 404, json: { detail: 'denied project' } });
    if (fail) return route.fulfill({ status: 503, json: { detail: 'Codex workstation is not configured' } });
    return route.fulfill({ json: { items: [], next_cursor: null } });
  });
  await page.goto('/static/index.html');
  await selectFriday(page);
  await expect(page.locator('#codex-project-list')).toContainText('No allowlisted Codex projects found');

  fail = true;
  await page.locator('#codex-project-search').fill('retry');
  await expect(page.locator('#codex-project-list [role="alert"]')).toContainText('Codex workstation is not configured');
});

const THREAD_ID = '019f5022-a520-7de0-9208-018cd2d4d222';

function singleProjectCatalog(route, url) {
  if (url.pathname === '/api/codex/projects') {
    return route.fulfill({ json: {
      items: [{ project_id: 'test-project', display_name: 'Disposable Test Project', approved_root: 'workspace:test-project', availability: 'available', task_count: 1 }],
      next_cursor: null,
    } });
  }
  return route.fulfill({ json: {
    project_id: 'test-project',
    items: [{ task_id: THREAD_ID, project_id: 'test-project', title: 'Fixture resume task', status: 'idle', created_at: 1, updated_at: 2 }],
    next_cursor: null,
  } });
}

test('Codex browser resumes an exact fixture and renders progress and completion', async ({ page }) => {
  let createPayload;
  await mockShell(page, singleProjectCatalog, (route, url) => {
    if (url.pathname === '/api/agent-tasks' && route.request().method() === 'POST') {
      createPayload = route.request().postDataJSON();
      return route.fulfill({ status: 202, json: {
        task_id: 'broker-resume', worker: 'pc-codex', session_id: 'session-1', workspace: 'test-project',
        status: 'running', codex_thread_id: THREAD_ID, created_at: 1, updated_at: 2,
      } });
    }
    if (url.pathname.endsWith('/events')) {
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: [
          `data: ${JSON.stringify({ event_id: 'progress-1', seq: 0, task_id: 'broker-resume', worker: 'pc-codex', type: 'progress', text: 'Reading the fixture.' })}\n\n`,
          `data: ${JSON.stringify({ event_id: 'artifact-1', seq: 1, task_id: 'broker-resume', worker: 'pc-codex', type: 'artifact', text: 'Opened fixture report.', metadata: { citation: 'workspace:test-project/reports/fixture.md', review_mode: 'read_only_citation' } })}\n\n`,
          `data: ${JSON.stringify({ event_id: 'result-1', seq: 2, task_id: 'broker-resume', worker: 'pc-codex', type: 'result', text: 'Fixture complete.', metadata: { codex_thread_id: THREAD_ID } })}\n\n`,
        ].join(''),
      });
    }
    return route.fulfill({ json: {} });
  });

  await page.goto('/static/index.html');
  await expect.poll(() => page.evaluate(() => Boolean(window.sessionModule))).toBe(true);
  await page.evaluate(() => { window.sessionModule.getCurrentSessionId = () => 'session-1'; });
  await selectFriday(page);
  await page.getByText('Disposable Test Project').click();
  await page.getByText('Fixture resume task').click();
  await expect(page.locator('#codex-task-identity')).toContainText(`exact task ${THREAD_ID}`);
  await page.locator('#codex-task-prompt').fill('Read the fixture and cite the result.');
  await page.locator('#codex-task-run').click();

  await expect(page.locator('#codex-task-events')).toContainText('Reading the fixture.');
  await expect(page.locator('#codex-task-events')).toContainText('workspace:test-project/reports/fixture.md');
  await expect(page.locator('#codex-task-events')).toContainText('Fixture complete.');
  await expect(page.locator('#codex-task-status')).toContainText('completed');
  expect(createPayload.workspace).toBe('test-project');
  expect(createPayload.codex_thread_id).toBe(THREAD_ID);
  expect(createPayload.permission_mode).toBe('read_only');
  expect(createPayload.approved).toBe(false);
});

test('Codex browser creates once, steers, and cancels the running fixture', async ({ page }) => {
  const calls = [];
  await mockShell(page, singleProjectCatalog, (route, url) => {
    const method = route.request().method();
    if (url.pathname === '/api/agent-tasks' && method === 'POST') {
      calls.push({ action: 'create', body: route.request().postDataJSON() });
      return route.fulfill({ status: 202, json: {
        task_id: 'broker-create', worker: 'pc-codex', session_id: 'session-1', workspace: 'test-project',
        status: 'running', codex_thread_id: THREAD_ID, created_at: 1, updated_at: 2,
      } });
    }
    if (url.pathname.endsWith('/steer') && method === 'POST') {
      calls.push({ action: 'steer', body: route.request().postDataJSON() });
      return route.fulfill({ json: {
        task_id: 'broker-create', worker: 'pc-codex', session_id: 'session-1', workspace: 'test-project',
        status: 'running', codex_thread_id: THREAD_ID,
      } });
    }
    if (url.pathname.endsWith('/cancel') && method === 'POST') {
      calls.push({ action: 'cancel' });
      return route.fulfill({ json: {
        task_id: 'broker-create', worker: 'pc-codex', session_id: 'session-1', workspace: 'test-project',
        status: 'cancelled', codex_thread_id: THREAD_ID,
      } });
    }
    if (url.pathname.endsWith('/events')) {
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: `data: ${JSON.stringify({ event_id: 'progress-1', seq: 0, task_id: 'broker-create', worker: 'pc-codex', type: 'progress', text: 'Working in the disposable project.' })}\n\n`,
      });
    }
    return route.fulfill({ json: {} });
  });

  await page.goto('/static/index.html');
  await expect.poll(() => page.evaluate(() => Boolean(window.sessionModule))).toBe(true);
  await page.evaluate(() => { window.sessionModule.getCurrentSessionId = () => 'session-1'; });
  await selectFriday(page);
  await page.getByText('Disposable Test Project').click();
  await page.locator('#codex-new-task').click();
  await page.locator('#codex-task-prompt').fill('Apply one reversible fixture edit.');
  await page.locator('#codex-task-write-consent').check();
  await expect(page.locator('#codex-task-run')).toHaveText('Start approved edit task');
  await page.locator('#codex-task-run').click();
  await expect(page.locator('#codex-task-events')).toContainText('Working in the disposable project.');

  await page.locator('#codex-task-steer-prompt').fill('Use the corrected fixture name.');
  await page.locator('#codex-task-steer').click();
  await expect(page.locator('#codex-task-status')).toContainText('Steering accepted');
  await page.locator('#codex-task-cancel').click();
  await expect(page.locator('#codex-task-status')).toContainText('Cancellation requested');

  expect(calls).toEqual([
    { action: 'create', body: expect.objectContaining({ workspace: 'test-project', codex_thread_id: null, permission_mode: 'workspace_write', approved: true }) },
    { action: 'steer', body: { prompt: 'Use the corrected fixture name.' } },
    { action: 'cancel' },
  ]);
});

test('Codex browser keeps waiting and failure events visible', async ({ page }) => {
  await mockShell(page, singleProjectCatalog, (route, url) => {
    if (url.pathname === '/api/agent-tasks' && route.request().method() === 'POST') {
      return route.fulfill({ status: 202, json: {
        task_id: 'broker-failure', worker: 'pc-codex', session_id: 'session-1', workspace: 'test-project',
        status: 'running', codex_thread_id: THREAD_ID, created_at: 1, updated_at: 2,
      } });
    }
    if (url.pathname.endsWith('/events')) {
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: [
          `data: ${JSON.stringify({ event_id: 'question-1', seq: 0, task_id: 'broker-failure', worker: 'pc-codex', type: 'question', text: 'Which fixture should I inspect?' })}\n\n`,
          `data: ${JSON.stringify({ event_id: 'error-1', seq: 1, task_id: 'broker-failure', worker: 'pc-codex', type: 'error', text: 'Fixture task failed safely.' })}\n\n`,
        ].join(''),
      });
    }
    return route.fulfill({ json: {} });
  });

  await page.goto('/static/index.html');
  await expect.poll(() => page.evaluate(() => Boolean(window.sessionModule))).toBe(true);
  await page.evaluate(() => { window.sessionModule.getCurrentSessionId = () => 'session-1'; });
  await selectFriday(page);
  await page.getByText('Disposable Test Project').click();
  await page.locator('#codex-new-task').click();
  await page.locator('#codex-task-prompt').fill('Inspect the failure fixture.');
  await page.locator('#codex-task-run').click();

  await expect(page.locator('#codex-task-events')).toContainText('Which fixture should I inspect?');
  await expect(page.locator('#codex-task-events')).toContainText('Fixture task failed safely.');
  await expect(page.locator('#codex-task-status')).toContainText('failed');
});

test('selected Friday chat renders and reconnects the same Friday-owned task', async ({ page }) => {
  const task = {
    task_id: 'direct-friday-task', worker: 'pc-codex', presenter: 'Friday',
    session_id: 'friday-chat', workspace: 'test-project', status: 'running',
    codex_thread_id: THREAD_ID, created_at: 1, updated_at: 2, events: [],
  };
  let listRequests = 0;
  await mockShell(page, singleProjectCatalog, (route, url) => {
    if (url.pathname === '/api/agent-tasks') {
      listRequests += 1;
      return route.fulfill({ json: { tasks: [task] } });
    }
    if (url.pathname.endsWith('/events')) {
      return route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
        body: [
          `data: ${JSON.stringify({ event_id: 'progress-direct', seq: 0, ...task, type: 'progress', text: 'Friday is inspecting the project.' })}\n\n`,
          `data: ${JSON.stringify({ event_id: 'result-direct', seq: 1, ...task, type: 'result', text: 'Friday completed the inspection.' })}\n\n`,
        ].join(''),
      });
    }
    return route.fulfill({ json: task });
  }, [{
    id: 'friday-chat', name: 'Friday task chat', model: 'fixture', endpoint_url: 'http://model.test',
    archived: false, agent_target: 'pc-codex', created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(), last_message_at: new Date().toISOString(), message_count: 1,
  }], route => route.fulfill({
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
    body: [
      `data: ${JSON.stringify({ type: 'model_info', model: 'Friday', character_name: 'Friday' })}\n\n`,
      `data: ${JSON.stringify({ type: 'agent_task', action: 'started', ...task })}\n\n`,
      `data: ${JSON.stringify({ type: 'metrics', data: { model: 'Friday', route: 'pc-codex' } })}\n\n`,
      'data: [DONE]\n\n',
    ].join(''),
  }));

  await page.goto('/static/index.html');
  await expect.poll(() => page.evaluate(() => Boolean(window.sessionModule && window.jarvisVoice))).toBe(true);
  await page.evaluate(async () => {
    const sessions = await import('/static/js/sessions.js');
    sessions.setCurrentSessionId('friday-chat');
    sessions.updateModelPicker();
  });
  await page.locator('#message:visible').fill('Inspect the selected project.');
  await page.locator('.send-btn:visible').click();

  const activity = page.locator('.jarvis-task-activity[data-task-id="direct-friday-task"]');
  await expect(activity).toContainText('Friday');
  const roles = await page.locator('.msg[data-task-id="direct-friday-task"] .role').allTextContents();
  expect(roles.length).toBeGreaterThan(0);
  expect(roles.every(role => role.includes('Friday'))).toBe(true);
  await expect(page.locator('.msg[data-task-id="direct-friday-task"] .body').filter({ hasText: 'Friday completed the inspection.' })).toHaveCount(1);

  await activity.evaluate(node => node.remove());
  await page.evaluate(() => window.jarvisVoice.restoreSessionTasks('friday-chat'));
  await expect(activity).toContainText('Friday');
  expect(listRequests).toBeGreaterThan(0);
});
