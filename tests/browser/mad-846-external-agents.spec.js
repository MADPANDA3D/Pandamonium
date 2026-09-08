import { expect, test } from '@playwright/test';

const SESSION_ID = 'session-one';
const SECOND_SESSION_ID = 'session-two';
const TARGET = 'configured-sidecar';
const WORKSPACE = 'sample-project';

function sessionFixture(id = SESSION_ID, agentTarget = 'jarvis') {
  return {
    id,
    name: `External worker chat ${id}`,
    model: 'test/model',
    endpoint_url: 'http://model.test/v1/chat/completions',
    agent_target: agentTarget,
    message_count: 0,
    archived: false,
    created_at: '2026-09-08T12:00:00Z',
    updated_at: '2026-09-08T12:00:00Z',
    last_message_at: '2026-09-08T12:00:00Z',
  };
}

function canonicalTask(taskId, sessionId, status = 'running') {
  return {
    task_id: taskId,
    session_id: sessionId,
    worker: TARGET,
    workspace: WORKSPACE,
    permission_mode: 'read_only',
    presenter: 'Installation supplied worker',
    status,
    events: [],
    artifacts: [],
  };
}

function selectorCatalog({
  available = true,
  governed = true,
  canStart = governed,
  canSteer = governed,
} = {}) {
  const state = available ? 'healthy' : 'unavailable';
  return {
    discovery: {
      schema_version: 'pandamonium.discovery.v1',
      generated_at: '2026-09-08T12:00:00Z',
      entities: [{
        kind: 'agent', id: 'agent:jarvis', display_name: 'Jarvis', availability: 'available',
        ownership: { scope: 'owner', id: 'owner:current' }, health: { state: 'healthy' },
        permissions: { requires_authenticated_request: true, configured_scopes: ['owner:current'], delegation: 'narrower_only' },
        source: { type: 'configuration', ref: 'fixture' }, actions: [],
      }, {
        kind: 'worker', id: 'worker:external', display_name: 'Installation supplied worker',
        availability: available ? 'available' : 'unavailable',
        ownership: { scope: 'installation', id: 'installation:current' },
        health: { state, ...(available ? {} : { reason: 'sidecar_unavailable' }) },
        permissions: {
          requires_authenticated_request: true,
          configured_scopes: [`workspace:${WORKSPACE}`],
          delegation: 'narrower_only',
        },
        source: { type: 'worker', ref: 'fixture' }, actions: [],
      }],
    },
    selections: [{
      entity_id: 'agent:jarvis', kind: 'agent', target: 'jarvis',
      capabilities: ['model'], selectable: true, reason: null,
    }, {
      entity_id: 'worker:external', kind: 'worker', target: TARGET,
      capabilities: [
        'external_agent',
        ...(governed ? ['governed_task_actions'] : []),
        ...(canStart ? ['task.start'] : []),
        ...(canSteer ? ['task.steer'] : []),
      ],
      selectable: available, reason: available ? null : 'sidecar_unavailable',
    }],
  };
}

const taskStates = [
  'loading', 'empty', 'unavailable', 'reconnecting', 'active', 'completed',
  'failed', 'canceled', 'stale',
];

async function installRoutes(page, {
  available = true,
  governed = true,
  canStart = governed,
  canSteer = governed,
  approval = null,
  extensionSurface = false,
  sessions = [sessionFixture()],
  canonicalTasks = {},
  restoreGates = {},
  dropActionResponses = 0,
  ambiguousActionStatus = 0,
  authorityGate = null,
  requests = [],
} = {}) {
  let approvalResolved = false;
  let actionResponsesDropped = 0;
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    const method = route.request().method();
    if (url.pathname === '/api/selector-catalog') {
      return route.fulfill({ json: selectorCatalog({ available, governed, canStart, canSteer }) });
    }
    if (url.pathname === '/api/auth/status') {
      return route.fulfill({ json: { username: 'tester', is_admin: true, privileges: {} } });
    }
    if (url.pathname === '/api/default-chat') {
      return route.fulfill({ json: {
        endpoint_id: 'endpoint-one', endpoint_url: 'http://model.test/v1/chat/completions', model: 'test/model',
      } });
    }
    if (url.pathname === '/api/models') return route.fulfill({ json: { items: [] } });
    if (url.pathname === '/api/model-endpoints') return route.fulfill({ json: [] });
    if (url.pathname === '/api/voice/oracle-config') {
      return route.fulfill({ json: {
        oracle_protocol_url: '',
        extension_surfaces: extensionSurface ? [{
          extension_id: 'fixture-surface',
          name: 'Fixture canvas',
          url: `${url.origin}/static/vendor/organic-sphere/index.html?mad-846=1`,
          origin: url.origin,
        }, {
          extension_id: 'wrong-origin',
          name: 'Rejected canvas',
          url: `${url.origin}/static/vendor/organic-sphere/index.html?mad-846=2`,
          origin: 'https://wrong-origin.invalid',
        }] : [],
      } });
    }
    if (url.pathname === '/api/sessions') return route.fulfill({ json: sessions });
    if (url.pathname.startsWith('/api/history/')) {
      const sessionId = decodeURIComponent(url.pathname.slice('/api/history/'.length));
      return route.fulfill({ json: {
        history: [], model: 'test/model', name: `External worker chat ${sessionId}`,
        endpoint_url: 'http://model.test/v1/chat/completions', offset: 0,
        limit: 100, total: 0, has_more_before: false,
      } });
    }
    if (url.pathname.startsWith('/api/session/') && method === 'PATCH') {
      requests.push({
        kind: 'target',
        sessionId: decodeURIComponent(url.pathname.slice('/api/session/'.length)),
        body: route.request().postData() || '',
      });
      return route.fulfill({ json: { ok: true } });
    }
    if (url.pathname === `/api/agent-workers/${TARGET}/tasks` && method === 'GET') {
      return route.fulfill({ json: {
        items: taskStates.map((status, index) => ({
          task_ref: `task-${index}`, title: `${status} task`, status,
          project_id: WORKSPACE,
        })),
        next_cursor: null,
      } });
    }
    if (url.pathname === `/api/agent-workers/${TARGET}/tasks/task-5/transcript`) {
      return route.fulfill({ json: {
        items: [
          { role: 'user', text: 'Inspect the bounded fixture.' },
          { role: 'assistant', text: 'The bounded inspection completed.', metadata: { host_path: '/private/not-rendered' } },
          { role: 'tool', text: 'One safe result.' },
        ],
        next_cursor: null,
      } });
    }
    if (url.pathname === '/api/agent-tasks' && method === 'GET') {
      const sessionId = url.searchParams.get('session_id') || '';
      requests.push({ kind: 'restore', sessionId });
      const gate = restoreGates[sessionId];
      if (gate) await gate;
      const configured = canonicalTasks[sessionId];
      const tasks = typeof configured === 'function'
        ? configured({ sessionId, requests })
        : configured;
      return route.fulfill({ json: { tasks: Array.isArray(tasks) ? tasks : [] } });
    }
    if (url.pathname === '/api/agent-tasks' && method === 'POST') {
      const body = route.request().postDataJSON();
      requests.push({ kind: 'task', action: 'create', body });
      if (approval && !approvalResolved) {
        approval.pending = true;
        if (approval.bindRequestId !== false && approval.decision) {
          approval.decision.request_id = body.request_id;
        }
        return route.fulfill({ status: 403, json: { detail: 'worker_task_not_authorized' } });
      }
      if (actionResponsesDropped < dropActionResponses) {
        actionResponsesDropped += 1;
        if (ambiguousActionStatus) {
          return route.fulfill({
            status: ambiguousActionStatus,
            json: { detail: 'sidecar_unavailable' },
          });
        }
        return route.abort('connectionfailed');
      }
      return route.fulfill({ json: {
        task_id: 'canonical-external-task', session_id: body.session_id || SESSION_ID,
        worker: TARGET, workspace: WORKSPACE, permission_mode: 'read_only',
        presenter: 'Installation supplied worker', status: 'completed', events: [], artifacts: [],
      } });
    }
    const steerMatch = url.pathname.match(/^\/api\/agent-tasks\/([^/]+)\/steer$/);
    if (steerMatch && method === 'POST') {
      const body = route.request().postDataJSON();
      const taskId = decodeURIComponent(steerMatch[1]);
      const taskSessionId = Object.entries(canonicalTasks).find(([, configured]) => {
        const tasks = typeof configured === 'function'
          ? []
          : (Array.isArray(configured) ? configured : []);
        return tasks.some(task => task.task_id === taskId);
      })?.[0] || SESSION_ID;
      requests.push({ kind: 'task', action: 'steer', taskId, body });
      if (actionResponsesDropped < dropActionResponses) {
        actionResponsesDropped += 1;
        if (ambiguousActionStatus) {
          return route.fulfill({
            status: ambiguousActionStatus,
            json: { detail: 'sidecar_unavailable' },
          });
        }
        return route.abort('connectionfailed');
      }
      return route.fulfill({ json: {
        task_id: taskId, session_id: taskSessionId,
        worker: TARGET, workspace: WORKSPACE, permission_mode: 'read_only',
        presenter: 'Installation supplied worker', status: 'running', events: [], artifacts: [],
      } });
    }
    if (url.pathname === '/api/authority' && method === 'GET') {
      return route.fulfill({ json: {
        decisions: approval?.pending && !approvalResolved
          ? (approval.decisions || [approval.decision])
          : [],
        receipts: [],
      } });
    }
    if (url.pathname === `/api/authority/decisions/${approval?.decision?.decision_id}` && method === 'POST') {
      const body = route.request().postDataJSON();
      requests.push({ kind: 'authority', body });
      if (authorityGate) await authorityGate;
      approvalResolved = true;
      return route.fulfill({ json: {
        receipt_id: 'receipt-one', decision_id: approval.decision.decision_id,
        capability: approval.decision.capability,
        action_effect: approval.decision.action_effect,
        workspace: WORKSPACE, decision: body.choice === 'approve' ? 'allow' : 'deny',
        scope: body.scope, status: 'active',
      } });
    }
    if (/^\/api\/voice\/sessions\/[^/]+\/extensions\/[^/]+\/results$/.test(url.pathname) && method === 'POST') {
      requests.push({ kind: 'extension_result', body: route.request().postDataJSON() });
      return route.fulfill({ json: { ok: true } });
    }
    if (url.pathname === '/api/chat_stream') {
      requests.push({ kind: 'chat_stream' });
      return route.fulfill({ status: 500, json: { error: 'must_not_reroute' } });
    }
    return route.fulfill({ json: {} });
  });
}

async function openExternalWorker(page) {
  await page.goto(`/static/index.html#${SESSION_ID}`);
  await expect.poll(
    () => page.evaluate(expectedId => (
      typeof window.sessionModule?.selectSession === 'function'
      && window.sessionModule.getSessions?.().some(session => session.id === expectedId)
    ), SESSION_ID),
    { timeout: 15_000 },
  ).toBe(true);
  await page.evaluate(
    id => window.sessionModule.selectSession(id, { showLoading: false }),
    SESSION_ID,
  );
  await expect.poll(() => page.evaluate(() => window.sessionModule?.getCurrentSessionId())).toBe(SESSION_ID);
  await page.locator('#model-picker-btn').click();
  const choice = page.locator('#model-picker-list .model-switch-item')
    .filter({ hasText: 'Installation supplied worker' });
  await expect(choice.locator('[data-canonical-identity-icon="worker"]')).toBeVisible();
  await expect(choice).toHaveAttribute('aria-label', /Installation supplied worker, External worker/);
  await choice.click();
  await expect(page.locator('#codex-workspace-browser')).toBeVisible();
  await expect(page.locator('#codex-browser-title')).toHaveText('Installation supplied worker');
}

async function selectWorkspace(page) {
  const workspace = page.locator('.codex-project-row').filter({ hasText: WORKSPACE });
  await expect(workspace.locator('.codex-browser-icon')).toBeVisible();
  await workspace.click();
  await expect(workspace).toHaveAttribute('aria-expanded', 'true');
}

async function sendPrompt(page, prompt) {
  await page.locator('#message:visible').fill(prompt);
  await page.evaluate(() => window._updateSendBtnIcon?.());
  await page.locator('.send-btn:visible').click();
}

test('external worker reuses the canonical selector, workspace browser, transcript, and teardown', async ({ page }) => {
  const requests = [];
  await installRoutes(page, { requests });
  await openExternalWorker(page);

  await expect(page.locator('#model-picker-label')).toHaveText('Installation supplied worker');
  await expect(page.locator('#extension-surface-panel')).toBeHidden();
  await expect(page.locator('#extension-surface-frame')).not.toHaveAttribute('src', /./);
  const voiceChoice = page.locator('#jarvis-agent-menu .jarvis-target').filter({ hasText: 'Installation supplied worker' });
  await expect(voiceChoice).toBeDisabled();

  await selectWorkspace(page);
  for (const status of taskStates) {
    await expect(page.locator(`.codex-task-row[data-task-status="${status}"]`)).toHaveCount(1);
  }
  await page.getByText('completed task', { exact: true }).click();
  await expect(page.locator('[data-external-agent-transcript="true"]')).toHaveCount(3);
  await expect(page.locator('#chat-history')).toContainText('The bounded inspection completed.');
  await expect(page.locator('#chat-history')).not.toContainText('/private/not-rendered');

  await page.locator('#model-picker-btn').click();
  await page.locator('#model-picker-list').getByText('Jarvis', { exact: true }).click();
  await expect(page.locator('#codex-workspace-browser')).toBeHidden();
  await expect(page.locator('[data-external-agent-transcript="true"]')).toHaveCount(0);
  expect(requests.some(item => item.kind === 'chat_stream')).toBe(false);
});

test('material external action uses the canonical approval card and retries only after exact approval', async ({ page }) => {
  const requests = [];
  const decision = {
    decision_id: 'external-decision-one', session_id: SESSION_ID,
    decision: 'approval_required', status: 'pending',
    expires_at: '2099-09-08T12:10:00Z', created_at: '2026-09-08T12:00:00Z',
    capability: { name: 'start_agent_task', target: 'worker:installation' },
    action_effect: 'external_publication_or_communication', workspace: WORKSPACE,
    preview: {
      action: 'create', worker: TARGET, workspace: WORKSPACE,
      arguments: { prompt: 'Run the exact bounded task.', permission_mode: 'read_only' },
    },
  };
  await installRoutes(page, { approval: { pending: false, decision }, requests });
  await openExternalWorker(page);
  await selectWorkspace(page);

  await sendPrompt(page, 'Run the exact bounded task.');
  const card = page.locator('.authority-approval-card[data-decision-id="external-decision-one"]');
  await expect(card).toBeVisible();
  await expect(card).toContainText('Review exact action');
  expect(requests.filter(item => item.kind === 'task')).toHaveLength(1);
  expect(requests.some(item => item.kind === 'chat_stream')).toBe(false);

  await card.getByRole('button', { name: 'Approve once' }).click();
  await expect.poll(() => requests.filter(item => item.kind === 'task').length).toBe(2);
  expect(requests.filter(item => item.kind === 'task')[0].body).toEqual(
    requests.filter(item => item.kind === 'task')[1].body,
  );
  expect(requests.filter(item => item.kind === 'authority')[0].body).toEqual({ choice: 'approve', scope: 'once' });
  expect(requests.some(item => item.kind === 'chat_stream')).toBe(false);
  await expect(card).toContainText('Approved once');
});

test('denial never dispatches the external action again and unavailable workers stay explicit', async ({ page }) => {
  const requests = [];
  const decision = {
    decision_id: 'external-decision-deny', session_id: SESSION_ID,
    decision: 'approval_required', status: 'pending',
    expires_at: '2099-09-08T12:10:00Z', created_at: '2026-09-08T12:00:00Z',
    capability: { name: 'start_agent_task', target: 'worker:installation' },
    action_effect: 'purchase', workspace: WORKSPACE,
    preview: {
      action: 'create', worker: TARGET, workspace: WORKSPACE,
      arguments: { prompt: 'Do not dispatch twice.', permission_mode: 'read_only' },
    },
  };
  await installRoutes(page, { approval: { pending: false, decision }, requests });
  await openExternalWorker(page);
  await selectWorkspace(page);
  await sendPrompt(page, 'Do not dispatch twice.');
  const card = page.locator('.authority-approval-card[data-decision-id="external-decision-deny"]');
  await card.getByRole('button', { name: 'Deny' }).click();
  await expect(card).toContainText('Denied');
  expect(requests.filter(item => item.kind === 'task')).toHaveLength(1);
  expect(requests.some(item => item.kind === 'chat_stream')).toBe(false);

  const unavailablePage = await page.context().newPage();
  const unavailableRequests = [];
  await installRoutes(unavailablePage, { available: false, requests: unavailableRequests });
  await unavailablePage.goto(`/static/index.html#${SESSION_ID}`);
  await expect.poll(() => unavailablePage.evaluate(() => window.sessionModule?.getCurrentSessionId())).toBe(SESSION_ID);
  await unavailablePage.locator('#model-picker-btn').click();
  const unavailable = unavailablePage.locator('#model-picker-list .model-switch-item')
    .filter({ hasText: 'Installation supplied worker' });
  await expect(unavailable).toHaveAttribute('aria-disabled', 'true');
  await expect(unavailable).toContainText('External worker');
  await unavailable.click({ force: true });
  await expect(unavailablePage.locator('#model-picker-label')).toHaveText('Jarvis');
  await expect(unavailablePage.locator('#codex-workspace-browser')).toBeHidden();
  expect(unavailableRequests.some(item => item.kind === 'task')).toBe(false);
});

test('pending external approval is denied and removed during navigation teardown', async ({ page }) => {
  const requests = [];
  const decision = {
    decision_id: 'external-decision-navigation', session_id: SESSION_ID,
    decision: 'approval_required', status: 'pending',
    expires_at: '2099-09-08T12:10:00Z', created_at: '2026-09-08T12:00:00Z',
    capability: { name: 'start_agent_task', target: 'worker:installation' },
    action_effect: 'reversible_write', workspace: WORKSPACE,
    preview: {
      action: 'create', worker: TARGET, workspace: WORKSPACE,
      arguments: { prompt: 'Clear this continuation safely.', permission_mode: 'read_only' },
    },
  };
  await installRoutes(page, { approval: { pending: false, decision }, requests });
  await openExternalWorker(page);
  await selectWorkspace(page);
  await sendPrompt(page, 'Clear this continuation safely.');
  const card = page.locator('.authority-approval-card[data-decision-id="external-decision-navigation"]');
  await expect(card).toBeVisible();

  await page.evaluate(() => window.dispatchEvent(new CustomEvent('odysseus:session-rendered', {
    detail: { sessionId: 'session-one' },
  })));
  await page.waitForTimeout(75);
  await expect(card).toBeVisible();
  expect(requests.filter(item => item.kind === 'authority')).toHaveLength(0);

  await page.evaluate(() => window.dispatchEvent(new CustomEvent('odysseus:session-rendered', {
    detail: { sessionId: 'session-two' },
  })));

  await expect.poll(() => requests.filter(item => item.kind === 'authority').length).toBe(1);
  expect(requests.find(item => item.kind === 'authority').body).toEqual({ choice: 'deny', scope: 'once' });
  await expect(card).toHaveCount(0);
  expect(requests.filter(item => item.kind === 'task')).toHaveLength(1);
  expect(requests.some(item => item.kind === 'chat_stream')).toBe(false);
});

test('external approval selection requires the exact action request id', async ({ page }) => {
  const requests = [];
  const decision = {
    decision_id: 'external-decision-current', session_id: SESSION_ID,
    decision: 'approval_required', status: 'pending',
    expires_at: '2099-09-08T12:10:00Z', created_at: '2026-09-08T12:00:00Z',
    capability: { name: 'start_agent_task', target: 'worker:installation' },
    action_effect: 'reversible_write', workspace: WORKSPACE,
    preview: {
      action: 'create', worker: TARGET, workspace: WORKSPACE,
      arguments: { prompt: 'Approve only this exact request.', permission_mode: 'read_only' },
    },
  };
  const wrongDecision = {
    ...decision,
    decision_id: 'external-decision-older-request',
    request_id: 'older-request-id',
    created_at: '2026-09-08T12:05:00Z',
    preview: {
      ...decision.preview,
      arguments: { prompt: 'An unrelated older request.', permission_mode: 'read_only' },
    },
  };
  const approval = {
    pending: false,
    decision,
    decisions: [wrongDecision, decision],
  };
  await installRoutes(page, { approval, requests });
  await openExternalWorker(page);
  await selectWorkspace(page);
  await sendPrompt(page, 'Approve only this exact request.');

  await expect(page.locator('.authority-approval-card[data-decision-id="external-decision-current"]')).toBeVisible();
  await expect(page.locator('.authority-approval-card[data-decision-id="external-decision-older-request"]')).toHaveCount(0);
  expect(decision.request_id).toBe(requests.find(item => item.kind === 'task').body.request_id);
  expect(wrongDecision.request_id).not.toBe(decision.request_id);
  expect(requests.some(item => item.kind === 'chat_stream')).toBe(false);
});

test('claimed approval cannot be denied or dispatched after navigation', async ({ page }) => {
  const requests = [];
  let releaseAuthority;
  const authorityGate = new Promise(resolve => { releaseAuthority = resolve; });
  const decision = {
    decision_id: 'external-decision-race', session_id: SESSION_ID,
    decision: 'approval_required', status: 'pending',
    expires_at: '2099-09-08T12:10:00Z', created_at: '2026-09-08T12:00:00Z',
    capability: { name: 'start_agent_task', target: 'worker:installation' },
    action_effect: 'reversible_write', workspace: WORKSPACE,
    preview: {
      action: 'create', worker: TARGET, workspace: WORKSPACE,
      arguments: { prompt: 'Approve before navigating.', permission_mode: 'read_only' },
    },
  };
  await installRoutes(page, {
    approval: { pending: false, decision },
    authorityGate,
    requests,
  });
  await openExternalWorker(page);
  await selectWorkspace(page);
  await sendPrompt(page, 'Approve before navigating.');
  const card = page.locator('.authority-approval-card[data-decision-id="external-decision-race"]');
  await expect(card).toBeVisible();

  await card.getByRole('button', { name: 'Approve once' }).click();
  await expect.poll(() => requests.filter(item => item.kind === 'authority').length).toBe(1);
  await page.evaluate(() => window.dispatchEvent(new CustomEvent('odysseus:session-rendered', {
    detail: { sessionId: 'session-two' },
  })));
  await page.waitForTimeout(75);
  expect(requests.filter(item => item.kind === 'authority')).toHaveLength(1);
  expect(requests.filter(item => item.kind === 'task')).toHaveLength(1);

  releaseAuthority();
  await expect(page.getByText(/conversation changed after approval/i)).toBeVisible();
  expect(requests.filter(item => item.kind === 'authority')).toHaveLength(1);
  expect(requests.filter(item => item.kind === 'task')).toHaveLength(1);
  expect(requests.some(item => item.kind === 'chat_stream')).toBe(false);
});

test('same external worker never reuses another conversation task across slow restores', async ({ page }) => {
  const requests = [];
  const restoreGates = {};
  const canonicalTasks = {
    [SESSION_ID]: [canonicalTask('task-session-a', SESSION_ID)],
    [SECOND_SESSION_ID]: [canonicalTask('task-session-b', SECOND_SESSION_ID)],
  };
  await installRoutes(page, {
    requests,
    restoreGates,
    canonicalTasks,
    sessions: [
      sessionFixture(SESSION_ID),
      sessionFixture(SECOND_SESSION_ID, TARGET),
    ],
  });
  await openExternalWorker(page);
  await selectWorkspace(page);
  await expect.poll(() => requests.filter(item => (
    item.kind === 'restore' && item.sessionId === SESSION_ID
  )).length).toBeGreaterThan(0);
  const initialARestores = requests.filter(item => (
    item.kind === 'restore' && item.sessionId === SESSION_ID
  )).length;

  let releaseA;
  restoreGates[SESSION_ID] = new Promise(resolve => { releaseA = resolve; });
  await page.evaluate(([awaySessionId, sessionId]) => {
    window.dispatchEvent(new CustomEvent('odysseus:session-rendered', {
      detail: { sessionId: awaySessionId },
    }));
    window.dispatchEvent(new CustomEvent('odysseus:session-rendered', { detail: { sessionId } }));
  }, [SECOND_SESSION_ID, SESSION_ID]);
  await expect.poll(() => requests.filter(item => (
    item.kind === 'restore' && item.sessionId === SESSION_ID
  )).length).toBeGreaterThan(initialARestores);

  let releaseB;
  restoreGates[SECOND_SESSION_ID] = new Promise(resolve => { releaseB = resolve; });
  await page.evaluate(sessionId => window.sessionModule.selectSession(sessionId), SECOND_SESSION_ID);
  await expect.poll(() => page.evaluate(() => window.sessionModule.getCurrentSessionId())).toBe(SECOND_SESSION_ID);
  await page.waitForTimeout(150);
  const secondWorkspace = page.locator('.codex-project-row').filter({ hasText: WORKSPACE });
  if (await secondWorkspace.getAttribute('aria-expanded') !== 'true') await selectWorkspace(page);
  await expect(secondWorkspace).toHaveAttribute('aria-expanded', 'true');
  await expect.poll(() => requests.some(item => (
    item.kind === 'restore' && item.sessionId === SECOND_SESSION_ID
  ))).toBe(true);

  await sendPrompt(page, 'Continue only the second conversation task.');
  await page.waitForTimeout(75);
  expect(requests.filter(item => item.kind === 'task')).toHaveLength(0);
  releaseB();
  await expect.poll(() => requests.filter(item => item.kind === 'task').length).toBe(1);
  expect(requests.find(item => item.kind === 'task')).toMatchObject({
    action: 'steer',
    taskId: 'task-session-b',
  });
  expect(requests.some(item => item.taskId === 'task-session-a')).toBe(false);

  releaseA();
  await page.waitForTimeout(75);
  expect(requests.some(item => item.taskId === 'task-session-a')).toBe(false);
  expect(requests.some(item => item.kind === 'chat_stream')).toBe(false);
});

test('terminal canonical state is refreshed before a follow-up chooses create over steer', async ({ page }) => {
  const requests = [];
  let terminal = false;
  const canonicalTasks = {
    [SESSION_ID]: () => [canonicalTask(
      'task-that-completed',
      SESSION_ID,
      terminal ? 'completed' : 'queued',
    )],
  };
  await installRoutes(page, { requests, canonicalTasks });
  await openExternalWorker(page);
  await selectWorkspace(page);
  await expect(page.locator('#message')).toHaveAttribute('placeholder', /active governed task/);

  terminal = true;
  await page.evaluate(task => window.jarvisVoice.trackWorkerTask(task), canonicalTask(
    'task-that-completed', SESSION_ID, 'completed',
  ));
  await sendPrompt(page, 'Start the next task after the completed result.');
  await expect.poll(() => requests.filter(item => item.kind === 'task').length).toBe(1);
  expect(requests.find(item => item.kind === 'task')).toMatchObject({ action: 'create' });
  expect(requests.some(item => item.action === 'steer')).toBe(false);
  expect(requests.some(item => item.kind === 'chat_stream')).toBe(false);
});

test('start-only external worker blocks active follow-ups but can create after completion', async ({ page }) => {
  const requests = [];
  const canonicalTasks = {
    [SESSION_ID]: [canonicalTask('start-only-active', SESSION_ID, 'running')],
  };
  await installRoutes(page, {
    requests,
    canonicalTasks,
    canStart: true,
    canSteer: false,
  });
  await openExternalWorker(page);
  await selectWorkspace(page);
  await expect(page.locator('#message')).toHaveAttribute('placeholder', /follow-up messages unavailable/);

  await sendPrompt(page, 'This must not be sent as a steer action.');
  await page.waitForTimeout(75);
  expect(requests.filter(item => item.kind === 'task')).toHaveLength(0);
  expect(requests.some(item => item.kind === 'chat_stream')).toBe(false);

  canonicalTasks[SESSION_ID] = [canonicalTask('start-only-active', SESSION_ID, 'completed')];
  await sendPrompt(page, 'Start a fresh task now that the first one is complete.');
  await expect.poll(() => requests.filter(item => item.kind === 'task').length).toBe(1);
  expect(requests.find(item => item.kind === 'task')).toMatchObject({ action: 'create' });
  expect(requests.some(item => item.action === 'steer')).toBe(false);
});

test('action gating permits steer-only follow-up and blocks unsupported creation', async ({ page }) => {
  const requests = [];
  const canonicalTasks = {
    [SESSION_ID]: [canonicalTask('steer-only-active', SESSION_ID, 'running')],
  };
  await installRoutes(page, {
    requests,
    canonicalTasks,
    canStart: false,
    canSteer: true,
  });
  await openExternalWorker(page);
  await selectWorkspace(page);

  await sendPrompt(page, 'Steer the existing task only.');
  await expect.poll(() => requests.filter(item => item.kind === 'task').length).toBe(1);
  expect(requests.find(item => item.kind === 'task')).toMatchObject({
    action: 'steer', taskId: 'steer-only-active',
  });

  canonicalTasks[SESSION_ID] = [canonicalTask('steer-only-active', SESSION_ID, 'completed')];
  await sendPrompt(page, 'Do not create an unsupported task.');
  await expect(page.getByText(/does not allow governed task creation/i)).toBeVisible();
  expect(requests.filter(item => item.kind === 'task')).toHaveLength(1);
  expect(requests.some(item => item.kind === 'chat_stream')).toBe(false);
});

test('ambiguous external 503 and exact resubmit retain one stable request id', async ({ page }) => {
  const requests = [];
  const canonicalTasks = {};
  await installRoutes(page, {
    requests,
    canonicalTasks,
    dropActionResponses: 2,
    ambiguousActionStatus: 503,
  });
  await openExternalWorker(page);
  await selectWorkspace(page);

  await sendPrompt(page, 'Dispatch this idempotent bounded action once.');
  await expect.poll(() => requests.filter(item => item.kind === 'task').length).toBe(2);
  await expect(page.locator('#message')).toHaveValue('Dispatch this idempotent bounded action once.');
  canonicalTasks[SESSION_ID] = [canonicalTask('possibly-created-task', SESSION_ID, 'running')];
  await sendPrompt(page, 'Dispatch this idempotent bounded action once.');
  await expect(page.getByText(/previous worker action has an unknown outcome/i)).toBeVisible();
  const attempts = requests.filter(item => item.kind === 'task');
  expect(attempts.map(item => item.action)).toEqual(['create', 'create']);
  expect(attempts[0].body.request_id).toMatch(/^[a-z0-9-]{16,200}$/i);
  expect(attempts[1].body.request_id).toBe(attempts[0].body.request_id);
  expect(attempts[1].body).toEqual(attempts[0].body);
  expect(requests.some(item => item.kind === 'chat_stream')).toBe(false);
});

test('JOS-EXT-1 canvas is bounded, focus-safe, responsive, and tears down on navigation', async ({ page }) => {
  // This case deliberately exercises two viewport layouts, iframe messaging,
  // focus restoration, and teardown. Keep it bounded while allowing slower
  // single-worker CI hosts enough room after the preceding browser suite.
  test.setTimeout(90_000);
  const requests = [];
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 1280, height: 800 });
  await installRoutes(page, { extensionSurface: true, requests });
  await openExternalWorker(page);
  await expect.poll(() => page.evaluate(() => Boolean(window.jarvisVoice))).toBe(true);
  await expect(page.locator('#extension-surface-panel')).toBeHidden();
  await expect(page.locator('#extension-surface-frame')).not.toHaveAttribute('src', /./);
  await page.setViewportSize({ width: 390, height: 720 });
  await page.locator('#message').focus();

  await expect.poll(() => page.evaluate(() => {
    window.jarvisVoice.applyExtensionSurfaceControl({
      ui_event: 'extension_protocol_engage', extension_id: 'fixture-surface',
    });
    return !document.getElementById('extension-surface-panel')?.hidden;
  })).toBe(true);
  const panel = page.locator('#extension-surface-panel');
  await expect(panel).toBeVisible();
  await expect(page.locator('#extension-surface-frame')).toHaveAttribute('title', 'Fixture canvas extension surface');
  await expect(page.locator('#extension-surface-close')).toBeFocused();
  let bounds = await panel.boundingBox();
  expect(bounds?.width).toBe(390);
  expect(bounds?.height).toBe(720);

  await page.evaluate(() => window.jarvisVoice.applyExtensionSurfaceControl({
    ui_event: 'extension_protocol_command', extension_id: 'fixture-surface',
    call_id: 'missing-capability', tool: 'render_fixture', arguments: {},
    voice_session_id: 'voice-fixture', server_managed: true,
  }));
  await expect.poll(() => requests.filter(item => item.kind === 'extension_result').length).toBe(1);
  expect(requests.filter(item => item.kind === 'extension_result')[0].body.result.error)
    .toContain('not available in the current interface');

  const declaredAction = await page.evaluate(() => {
    const frame = document.getElementById('extension-surface-frame');
    window.__mad846ExtensionPosts = [];
    frame.contentWindow.postMessage = (message, origin) => {
      window.__mad846ExtensionPosts.push({ message, origin });
    };
    window.dispatchEvent(new MessageEvent('message', {
      source: frame.contentWindow,
      origin: window.location.origin,
      data: {
        source: 'jos-extension', type: 'extension_ready', extension_id: 'fixture-surface', ready: true,
        state: { mode: 'fixture' },
        capabilities: {
          protocol: 'fixture-surface', version: '1',
          tools: [{
            name: 'render_fixture', description: 'Render bounded fixture output',
            parameters: { type: 'object', properties: {} },
          }],
        },
      },
    }));
    window.jarvisVoice.applyExtensionSurfaceControl({
      ui_event: 'extension_protocol_command', extension_id: 'fixture-surface',
      call_id: 'declared-capability', tool: 'render_fixture', arguments: { bounded: true },
      voice_session_id: 'voice-fixture', server_managed: true,
    });
    return window.__mad846ExtensionPosts.at(-1);
  });
  expect(declaredAction).toEqual({
    origin: 'http://127.0.0.1:4173',
    message: {
      source: 'odysseus', type: 'extension_action', extension_id: 'fixture-surface',
      call_id: 'declared-capability', tool: 'render_fixture', arguments: { bounded: true },
    },
  });

  await page.evaluate(() => {
    const frame = document.getElementById('extension-surface-frame');
    window.dispatchEvent(new MessageEvent('message', {
      source: frame.contentWindow,
      origin: 'https://wrong-origin.invalid',
      data: {
        source: 'jos-extension', type: 'extension_result', extension_id: 'fixture-surface',
        call_id: 'declared-capability', tool: 'render_fixture', result: { ok: true },
      },
    }));
  });
  await page.waitForTimeout(50);
  expect(requests.filter(item => item.kind === 'extension_result')).toHaveLength(1);

  await page.evaluate(() => {
    const frame = document.getElementById('extension-surface-frame');
    window.dispatchEvent(new MessageEvent('message', {
      source: frame.contentWindow,
      origin: window.location.origin,
      data: {
        source: 'jos-extension', type: 'extension_result', extension_id: 'fixture-surface',
        call_id: 'declared-capability', tool: 'render_fixture', result: [],
      },
    }));
  });
  await expect.poll(() => requests.filter(item => item.kind === 'extension_result').length).toBe(2);
  const malformedResult = requests.filter(item => item.kind === 'extension_result')[1].body;
  expect(malformedResult).toMatchObject({
    call_id: 'declared-capability', tool: 'render_fixture',
    result: { ok: false, action: 'render_fixture' },
  });
  expect(malformedResult.result.error).toContain('malformed tool result');

  await page.evaluate(() => window.jarvisVoice.showChatForApproval());
  await expect(panel).toBeHidden();
  await expect(page.locator('#message')).toBeFocused();

  await page.setViewportSize({ width: 1280, height: 800 });
  await page.evaluate(() => window.jarvisVoice.applyExtensionSurfaceControl({
    ui_event: 'extension_protocol_engage', extension_id: 'fixture-surface',
  }));
  await expect(panel).toBeVisible();
  bounds = await panel.boundingBox();
  expect(bounds?.width).toBe(1280);
  expect(bounds?.height).toBe(800);

  await page.evaluate(() => window.dispatchEvent(new CustomEvent('odysseus:session-rendered', {
    detail: { sessionId: 'session-one' },
  })));
  await expect(panel).toBeVisible();
  await expect(page.locator('#extension-surface-frame')).toHaveAttribute('src', /mad-846=1/);

  await page.evaluate(() => window.dispatchEvent(new CustomEvent('odysseus:session-rendered', {
    detail: { sessionId: 'session-two' },
  })));
  await expect(panel).toBeHidden();
  await expect(page.locator('#extension-surface-frame')).not.toHaveAttribute('src', /./);
  await expect(page.locator('#message')).toBeFocused();

  const rejected = await page.evaluate(() => {
    window.jarvisVoice.applyExtensionSurfaceControl({
      ui_event: 'extension_protocol_engage', extension_id: 'wrong-origin',
    });
    return {
      hidden: document.getElementById('extension-surface-panel')?.hidden,
      src: document.getElementById('extension-surface-frame')?.getAttribute('src'),
    };
  });
  expect(rejected).toEqual({ hidden: true, src: null });
});
