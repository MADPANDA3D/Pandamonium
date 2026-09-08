import { getSelectedAgentSelection } from './modelPicker.js';
import {
  renderAuthorityApprovalCard,
  renderAuthorityDecisionResolved,
} from './chatRenderer.js';

const PAGE_SIZE = 50;

const state = {
  mode: null,
  target: '',
  targetLabel: '',
  governedTaskActions: false,
  canStartTask: false,
  canSteerTask: false,
  workspaces: [],
  projectCursor: null,
  taskCursor: null,
  selectedProject: null,
  selectedProjectName: '',
  selectedTask: null,
  canonicalTask: null,
  canonicalTaskContext: '',
  canonicalTaskRequest: 0,
  canonicalTaskRestore: Promise.resolve(false),
  renderedSessionId: '',
  projectRequest: 0,
  taskRequest: 0,
  transcriptRequest: 0,
};
const pendingAuthorityActions = new Map();
const retryableRequestIds = new Map();
let authorityLifecycle = 0;

function byId(id) { return document.getElementById(id); }

async function requestJson(path, options = {}) {
  const response = await fetch(path, { credentials: 'same-origin', ...options });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof body.detail === 'string' ? body.detail : (body.error || 'Codex catalog request failed');
    const error = new Error(detail);
    error.status = response.status;
    error.body = body;
    throw error;
  }
  return body;
}

function statusRow(text, isError = false) {
  const row = document.createElement('div');
  row.className = `codex-browser-status${isError ? ' is-error' : ''}`;
  row.setAttribute('role', isError ? 'alert' : 'status');
  row.textContent = text;
  return row;
}

function folderIcon() {
  const icon = document.createElement('span');
  icon.className = 'codex-browser-icon';
  icon.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6.5A2.5 2.5 0 0 1 5.5 4H9l2 2h7.5A2.5 2.5 0 0 1 21 8.5v8A2.5 2.5 0 0 1 18.5 19h-13A2.5 2.5 0 0 1 3 16.5z"/></svg>';
  return icon;
}

function activityDot(kind, title = '') {
  const dot = document.createElement('span');
  dot.className = `codex-browser-dot is-${kind}`;
  dot.title = title;
  dot.setAttribute('aria-label', title || kind);
  return dot;
}

function renderProjects(items, append = false) {
  const list = byId('codex-project-list');
  if (!list) return;
  const taskView = byId('codex-task-view');
  if (!append) {
    if (taskView) {
      taskView.hidden = true;
      byId('codex-project-view')?.appendChild(taskView);
    }
    list.replaceChildren();
  }
  items.forEach(project => {
    const projectId = String(project.project_id || '');
    const projectName = String(project.display_name || projectId || 'Project');
    const group = document.createElement('div');
    group.className = 'codex-project-group';
    group.dataset.projectId = projectId;

    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'codex-browser-row codex-project-row';
    button.dataset.projectId = projectId;
    button.dataset.projectName = projectName;
    button.setAttribute('aria-expanded', 'false');
    button.disabled = project.availability !== 'available';
    button.title = button.disabled
      ? String(project.reason || 'Project unavailable').replaceAll('_', ' ')
      : String(project.approved_root || projectName);
    const title = document.createElement('span');
    title.className = 'codex-browser-label';
    title.textContent = projectName;
    button.append(folderIcon(), title);
    if (button.disabled) button.append(activityDot('unavailable', button.title));
    group.appendChild(button);
    list.appendChild(group);
  });
  if (!list.children.length) list.appendChild(statusRow(`No projects are available for ${state.targetLabel || 'this worker'}.`));
}

function renderExternalWorkspaces() {
  renderProjects(state.workspaces.map(workspace => ({
    project_id: workspace,
    display_name: workspace,
    availability: 'available',
    approved_root: `workspace:${workspace}`,
  })));
}

function taskState(value) {
  const normalized = String(value || 'stale').trim().toLowerCase().replace(/[^a-z0-9_-]+/g, '_');
  if (normalized === 'cancelled') return 'canceled';
  return new Set([
    'loading', 'empty', 'unavailable', 'reconnecting', 'queued', 'active', 'running',
    'waiting', 'waiting_approval', 'completed', 'failed', 'canceled', 'blocked', 'stale',
  ]).has(normalized) ? normalized : 'stale';
}

function renderTasks(items, append = false) {
  const list = byId('codex-task-list');
  if (!list) return;
  if (!append) list.replaceChildren();
  items.forEach(task => {
    const taskId = String(task.task_id || task.task_ref || '');
    const status = taskState(task.status);
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'codex-browser-row codex-task-row';
    button.dataset.taskId = taskId;
    button.dataset.projectId = String(task.project_id || state.selectedProject || '');
    button.dataset.taskTitle = String(task.title || 'Untitled task');
    button.dataset.taskStatus = status;
    button.title = `${button.dataset.taskTitle} · ${status.replaceAll('_', ' ')}`;
    if (state.selectedTask?.taskId === taskId) button.classList.add('is-selected');
    const title = document.createElement('span');
    title.className = 'codex-browser-label';
    title.textContent = button.dataset.taskTitle;
    button.append(title);
    if (['active', 'running', 'queued', 'waiting', 'waiting_approval', 'reconnecting'].includes(status)) {
      button.append(activityDot('active', status.replaceAll('_', ' ')));
    } else if (['unavailable', 'failed', 'canceled', 'blocked', 'stale'].includes(status)) {
      button.append(activityDot('unavailable', status.replaceAll('_', ' ')));
    }
    list.appendChild(button);
  });
  if (!list.children.length) list.appendChild(statusRow('No tasks in this project yet.'));
}

async function loadProjects({ append = false } = {}) {
  const list = byId('codex-project-list');
  const more = byId('codex-project-more');
  const requestId = ++state.projectRequest;
  if (!append) {
    state.projectCursor = null;
    list?.replaceChildren(statusRow('Loading projects…'));
  }
  if (more) more.hidden = true;
  const params = new URLSearchParams({ limit: String(PAGE_SIZE) });
  if (append && state.projectCursor) params.set('cursor', state.projectCursor);
  try {
    const page = await requestJson(`/api/codex/projects?${params}`);
    if (requestId !== state.projectRequest) return;
    renderProjects(Array.isArray(page.items) ? page.items : [], append);
    state.projectCursor = page.next_cursor || null;
    if (more) more.hidden = !state.projectCursor;
  } catch (error) {
    if (requestId !== state.projectRequest || !list) return;
    list.replaceChildren(statusRow(error.message || 'Friday workstation is unavailable.', true));
  }
}

async function loadTasks({ append = false } = {}) {
  if (!state.selectedProject) return;
  const list = byId('codex-task-list');
  const more = byId('codex-task-more');
  const requestId = ++state.taskRequest;
  if (!append) {
    state.taskCursor = null;
    list?.replaceChildren(statusRow('Loading tasks…'));
  }
  if (more) more.hidden = true;
  const params = new URLSearchParams({ limit: String(PAGE_SIZE) });
  if (append && state.taskCursor) params.set('cursor', state.taskCursor);
  try {
    const path = state.mode === 'external'
      ? `/api/agent-workers/${encodeURIComponent(state.target)}/tasks?workspace=${encodeURIComponent(state.selectedProject)}&${params}`
      : `/api/codex/projects/${encodeURIComponent(state.selectedProject)}/tasks?${params}`;
    const page = await requestJson(path);
    if (requestId !== state.taskRequest) return;
    renderTasks(Array.isArray(page.items) ? page.items : [], append);
    state.taskCursor = page.next_cursor || null;
    if (more) more.hidden = !state.taskCursor;
  } catch (error) {
    if (requestId !== state.taskRequest || !list) return;
    list.replaceChildren(statusRow(error.message || `${state.targetLabel || 'Worker'} task list is unavailable.`, true));
  }
}

function clearTranscript() {
  state.transcriptRequest += 1;
  document.querySelectorAll('[data-external-agent-transcript="true"]').forEach(node => node.remove());
}

function clearExternalAuthorityUI({ denyPending = false } = {}) {
  authorityLifecycle += 1;
  const pending = [...pendingAuthorityActions.entries()];
  pendingAuthorityActions.clear();
  document.querySelectorAll('[data-external-agent-authority="true"]').forEach(node => node.remove());
  if (!denyPending) return;
  pending.forEach(([decisionId, action]) => {
    retryableRequestIds.delete(action.fingerprint);
    void fetch(`/api/authority/decisions/${encodeURIComponent(decisionId)}`, {
      method: 'POST',
      credentials: 'same-origin',
      keepalive: true,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ choice: 'deny', scope: 'once' }),
    }).then(() => {
      document.querySelector(
        `.authority-approval-card[data-decision-id="${CSS.escape(decisionId)}"]`,
      )?.remove();
    }).catch(() => {});
  });
}

function addTranscriptMessage(role, text, taskId) {
  const visibleRole = role === 'user' ? 'user' : 'assistant';
  const prefix = role === 'system' || role === 'tool' ? `[${role}] ` : '';
  const message = window.chatModule?.addMessage?.(visibleRole, `${prefix}${String(text || '')}`, '', {
    source: 'external_agent_transcript',
    worker: state.target,
    task_id: taskId,
    character_name: state.targetLabel || 'External worker',
  });
  if (message) message.dataset.externalAgentTranscript = 'true';
  return message;
}

async function loadExternalTranscript(taskId) {
  if (state.mode !== 'external' || !state.selectedProject || !taskId) return;
  clearTranscript();
  const requestId = state.transcriptRequest;
  const loading = addTranscriptMessage('assistant', `Loading ${state.targetLabel || 'worker'} task transcript…`, taskId);
  try {
    const params = new URLSearchParams({ workspace: state.selectedProject, limit: '64' });
    const page = await requestJson(
      `/api/agent-workers/${encodeURIComponent(state.target)}/tasks/${encodeURIComponent(taskId)}/transcript?${params}`,
    );
    if (requestId !== state.transcriptRequest) return;
    loading?.remove();
    const items = Array.isArray(page.items) ? page.items.slice(0, 64) : [];
    if (!items.length) {
      addTranscriptMessage('assistant', 'This task has no transcript entries yet.', taskId);
    } else {
      items.forEach(item => addTranscriptMessage(item.role, item.text, taskId));
      if (page.next_cursor) addTranscriptMessage('assistant', 'Older transcript entries are not loaded.', taskId);
    }
    window.uiModule?.scrollHistory?.();
  } catch (error) {
    if (requestId !== state.transcriptRequest) return;
    loading?.remove();
    addTranscriptMessage('assistant', `Transcript unavailable: ${error.message || 'connection failed'}`, taskId);
  }
}

function currentSessionId() {
  return String(window.sessionModule?.getCurrentSessionId?.() || '');
}

function canonicalContext(sessionId = currentSessionId()) {
  const id = String(sessionId || '');
  if (state.mode !== 'external' || !id || !state.target || !state.selectedProject) return '';
  return JSON.stringify([id, state.target, state.selectedProject]);
}

function invalidateCanonicalTask() {
  state.canonicalTaskRequest += 1;
  state.canonicalTask = null;
  state.canonicalTaskContext = '';
  state.canonicalTaskRestore = Promise.resolve(false);
  setComposerHint();
}

function restoreCanonicalTask(sessionId = currentSessionId()) {
  const id = String(sessionId || '');
  const target = state.target;
  const workspace = state.selectedProject;
  const context = canonicalContext(id);
  const requestId = ++state.canonicalTaskRequest;
  state.canonicalTask = null;
  state.canonicalTaskContext = context;
  setComposerHint();
  if (!context) {
    state.canonicalTaskRestore = Promise.resolve(false);
    return state.canonicalTaskRestore;
  }
  const restore = (async () => {
    try {
      const page = await requestJson(`/api/agent-tasks?session_id=${encodeURIComponent(id)}&limit=100`);
      if (
        requestId !== state.canonicalTaskRequest
        || currentSessionId() !== id
        || canonicalContext(id) !== context
        || state.target !== target
        || state.selectedProject !== workspace
      ) return false;
      state.canonicalTask = (Array.isArray(page.tasks) ? page.tasks : []).find(task => (
        task.worker === target && task.workspace === workspace
      )) || null;
      setComposerHint();
      return true;
    } catch (_) {
      if (requestId === state.canonicalTaskRequest && canonicalContext(id) === context) {
        state.canonicalTask = null;
        setComposerHint();
      }
      return false;
    }
  })();
  state.canonicalTaskRestore = restore;
  return restore;
}

async function requireCanonicalTask(sessionId, { refresh = false } = {}) {
  const id = String(sessionId || '');
  const context = canonicalContext(id);
  if (!context || currentSessionId() !== id) {
    throw new Error('The conversation changed. Submit the worker task again.');
  }
  if (refresh || state.canonicalTaskContext !== context) restoreCanonicalTask(id);
  const restored = await state.canonicalTaskRestore;
  if (
    restored !== true
    || currentSessionId() !== id
    || canonicalContext(id) !== context
    || state.canonicalTaskContext !== context
  ) {
    throw new Error('The active worker task could not be verified. Try again.');
  }
  return { context, task: state.canonicalTask };
}

function resetComposerHint() {
  const composer = byId('message');
  if (composer?.dataset.codexWorkspaceHint === '1') {
    composer.placeholder = composer.dataset.defaultPlaceholder || 'Message Pandamonium...';
    delete composer.dataset.codexWorkspaceHint;
  }
}

function setComposerHint() {
  const composer = byId('message');
  if (!composer || !state.selectedProject) return;
  if (!composer.dataset.defaultPlaceholder) composer.dataset.defaultPlaceholder = composer.placeholder || 'Message Pandamonium...';
  const canonicalTask = state.canonicalTaskContext === canonicalContext()
    ? state.canonicalTask
    : null;
  const activeTask = canonicalTask
    && !new Set(['completed', 'failed', 'cancelled', 'canceled', 'blocked']).has(taskState(canonicalTask.status));
  const externalSubject = activeTask && !state.canSteerTask
    ? `the active task in ${state.selectedProjectName} (follow-up messages unavailable)`
    : `${activeTask ? 'the active governed task' : 'a new governed task'} in ${state.selectedProjectName}`;
  const subject = state.mode === 'external'
    ? externalSubject
    : (state.selectedTask?.title || `a new task in ${state.selectedProjectName}`);
  composer.placeholder = `Message ${state.targetLabel || 'worker'} about ${subject}`;
  composer.dataset.codexWorkspaceHint = '1';
}

function clearSelection() {
  if (state.mode === 'external') {
    clearTranscript();
    clearExternalAuthorityUI({ denyPending: true });
  }
  state.selectedTask = null;
  invalidateCanonicalTask();
  state.selectedProject = null;
  state.selectedProjectName = '';
  document.querySelectorAll('.codex-project-row[aria-expanded="true"]')
    .forEach(button => button.setAttribute('aria-expanded', 'false'));
  const taskView = byId('codex-task-view');
  if (taskView) {
    taskView.hidden = true;
    byId('codex-project-view')?.appendChild(taskView);
  }
  resetComposerHint();
}

async function selectProject(projectId, displayName = '') {
  if (state.selectedProject === projectId) {
    clearSelection();
    return;
  }
  clearSelection();
  state.selectedProject = projectId;
  state.selectedProjectName = displayName || projectId;
  const group = [...document.querySelectorAll('.codex-project-group')]
    .find(item => item.dataset.projectId === String(projectId));
  const taskView = byId('codex-task-view');
  group?.querySelector('.codex-project-row')?.setAttribute('aria-expanded', 'true');
  if (group && taskView) group.appendChild(taskView);
  if (taskView) taskView.hidden = false;
  setComposerHint();
  await Promise.all([loadTasks(), restoreCanonicalTask(currentSessionId())]);
}

function selectTask(button) {
  state.selectedTask = {
    taskId: button.dataset.taskId,
    projectId: button.dataset.projectId,
    title: button.dataset.taskTitle || 'Codex task',
    status: button.dataset.taskStatus || 'unknown',
  };
  document.querySelectorAll('.codex-task-row.is-selected').forEach(row => row.classList.remove('is-selected'));
  button.classList.add('is-selected');
  setComposerHint();
  byId('message')?.focus();
  if (state.mode === 'external') loadExternalTranscript(state.selectedTask.taskId);
  else document.dispatchEvent(new CustomEvent('odysseus:codex-task-selected', { detail: state.selectedTask }));
}

function open(detail = {}) {
  const browser = byId('codex-workspace-browser');
  if (!browser) return;
  state.mode = detail.external === true ? 'external' : 'codex';
  state.target = String(detail.target || (state.mode === 'codex' ? 'pc-codex' : ''));
  state.targetLabel = String(detail.label || state.target || 'Worker').slice(0, 80);
  state.governedTaskActions = detail.governedTaskActions === true;
  state.canStartTask = detail.canStartTask === true;
  state.canSteerTask = detail.canSteerTask === true;
  state.workspaces = (Array.isArray(detail.workspaces) ? detail.workspaces : [])
    .map(value => String(value || ''))
    .filter(value => /^[a-z0-9][a-z0-9_-]{0,63}$/.test(value))
    .slice(0, 32);
  byId('sessions-section')?.classList.remove('hidden');
  if (byId('session-list')) byId('session-list').hidden = true;
  if (byId('chats-section-label')) byId('chats-section-label').textContent = 'Chats';
  if (byId('chats-library-btn')) byId('chats-library-btn').hidden = true;
  if (byId('session-sort-btn')) byId('session-sort-btn').hidden = true;
  const bulkBar = byId('session-bulk-bar');
  if (bulkBar && !bulkBar.classList.contains('hidden')) byId('session-bulk-cancel')?.click();
  bulkBar?.classList.add('hidden');
  browser.hidden = false;
  clearSelection();
  if (byId('codex-browser-title')) byId('codex-browser-title').textContent = state.mode === 'external'
    ? state.targetLabel
    : 'Projects';
  const list = byId('codex-project-list');
  if (detail.available === false) {
    list?.replaceChildren(statusRow(detail.reason || 'Friday is not currently available.', true));
    if (byId('codex-project-more')) byId('codex-project-more').hidden = true;
    return;
  }
  if (state.mode === 'external') renderExternalWorkspaces();
  else loadProjects();
}

function close() {
  clearTranscript();
  if (byId('codex-workspace-browser')) byId('codex-workspace-browser').hidden = true;
  if (byId('session-list')) byId('session-list').hidden = false;
  if (byId('chats-section-label')) byId('chats-section-label').textContent = 'Chats';
  if (byId('chats-library-btn')) byId('chats-library-btn').hidden = false;
  if (byId('session-sort-btn')) byId('session-sort-btn').hidden = false;
  clearSelection();
  state.mode = null;
  state.target = '';
  state.targetLabel = '';
  state.governedTaskActions = false;
  state.canStartTask = false;
  state.canSteerTask = false;
  state.workspaces = [];
  invalidateCanonicalTask();
  window.sessionModule?.renderSessionList?.();
}

function syncTarget(detail = {}) {
  if (detail.target === 'pc-codex' || detail.external === true) open(detail);
  else close();
}

function getSelectedContext() {
  if (!state.selectedProject) return null;
  return {
    workspace: state.selectedProject,
    codexThreadId: state.selectedTask?.taskId || null,
  };
}

function isExternalTarget(target) {
  return state.mode === 'external' && state.target === String(target || '');
}

function newExternalRequestId() {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return `ui-${[...bytes].map(value => value.toString(16).padStart(2, '0')).join('')}`;
}

async function externalIntentFingerprint({ context, prompt }) {
  const encoded = new TextEncoder().encode(JSON.stringify([
    context,
    String(prompt || ''),
  ]));
  const digest = await crypto.subtle.digest('SHA-256', encoded);
  return [...new Uint8Array(digest)]
    .map(value => value.toString(16).padStart(2, '0'))
    .join('');
}

function requestIdForFingerprint(fingerprint) {
  const existing = retryableRequestIds.get(fingerprint);
  if (existing?.ambiguous === true) {
    throw new Error('The previous worker action has an unknown outcome. Check its task status before trying a different action.');
  }
  if (existing?.requestId) return existing.requestId;
  const requestId = newExternalRequestId();
  retryableRequestIds.set(fingerprint, { requestId, ambiguous: false });
  return requestId;
}

function retainAmbiguousRequest(fingerprint) {
  const existing = retryableRequestIds.get(fingerprint);
  if (existing) existing.ambiguous = true;
}

async function dispatchExternalTaskRequest(path, options, { sessionId, context }) {
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      return await requestJson(path, options);
    } catch (error) {
      const ambiguous = !error?.status || [502, 503, 504].includes(Number(error.status));
      if (!ambiguous) throw error;
      if (attempt > 0) {
        error.externalActionAmbiguous = true;
        throw error;
      }
      if (currentSessionId() !== sessionId || canonicalContext(sessionId) !== context) {
        const changed = new Error('The conversation changed. The worker action was not retried.');
        changed.externalActionAmbiguous = true;
        throw changed;
      }
    }
  }
  throw new Error('The worker action could not be confirmed.');
}

function verifiedTaskResponse(task, { sessionId, context, taskId = '' }) {
  const valid = task
    && typeof task === 'object'
    && typeof task.task_id === 'string'
    && task.task_id.length > 0
    && task.task_id.length <= 200
    && task.session_id === sessionId
    && task.worker === state.target
    && task.workspace === state.selectedProject
    && typeof task.status === 'string'
    && (!taskId || task.task_id === taskId)
    && currentSessionId() === sessionId
    && canonicalContext(sessionId) === context;
  if (valid) return task;
  const error = new Error('The worker action returned an unverified task result. Its outcome is unknown.');
  error.externalActionAmbiguous = true;
  throw error;
}

async function submitExternalPrompt({ target, sessionId, prompt }) {
  if (!isExternalTarget(target) || !state.selectedProject) {
    throw new Error(`Select a ${state.targetLabel || 'worker'} workspace first.`);
  }
  if (!state.governedTaskActions) {
    throw new Error(`${state.targetLabel || 'This worker'} is configured for read-only browsing only.`);
  }
  const restored = await requireCanonicalTask(sessionId, { refresh: true });
  state.renderedSessionId = String(sessionId);
  const current = restored.task;
  const active = current && !new Set(['completed', 'failed', 'cancelled', 'canceled', 'blocked']).has(taskState(current.status));
  if (active && !state.canSteerTask) {
    throw new Error(`${state.targetLabel || 'This worker'} does not allow follow-up messages while its current task is active.`);
  }
  if (!active && !state.canStartTask) {
    throw new Error(`${state.targetLabel || 'This worker'} does not allow governed task creation.`);
  }
  const action = active ? 'steer' : 'create';
  const path = active
    ? `/api/agent-tasks/${encodeURIComponent(current.task_id)}/steer`
    : '/api/agent-tasks';
  const fingerprint = await externalIntentFingerprint({
    context: restored.context,
    prompt,
  });
  const requestId = requestIdForFingerprint(fingerprint);
  const options = active
    ? {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt, request_id: requestId }),
    }
    : {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        worker: state.target,
        session_id: sessionId,
        workspace: state.selectedProject,
        prompt,
        permission_mode: 'read_only',
        approved: false,
        persist_prompt: true,
        request_id: requestId,
      }),
    };
  let task;
  try {
    task = await dispatchExternalTaskRequest(path, options, {
      sessionId: String(sessionId),
      context: restored.context,
    });
    task = verifiedTaskResponse(task, {
      sessionId: String(sessionId),
      context: restored.context,
      taskId: active ? current.task_id : '',
    });
  } catch (error) {
    if (error?.status !== 403 || error?.message !== 'worker_task_not_authorized') {
      if (error?.externalActionAmbiguous === true) retainAmbiguousRequest(fingerprint);
      else retryableRequestIds.delete(fingerprint);
      throw error;
    }
    const authority = await requestJson('/api/authority');
    const now = Date.now();
    const decision = (Array.isArray(authority.decisions) ? authority.decisions : [])
      .filter(row => (
        row?.session_id === sessionId
        && row?.decision === 'approval_required'
        && !['resolved', 'expired'].includes(String(row?.status || ''))
        && Date.parse(row?.expires_at || '') > now
        && row?.request_id === requestId
        && row?.capability?.name === 'start_agent_task'
        && row?.preview?.worker === state.target
        && row?.preview?.workspace === state.selectedProject
        && row?.preview?.action === action
        && (!active || row?.preview?.task_id === current.task_id)
      ))
      .sort((left, right) => String(right.created_at || '').localeCompare(String(left.created_at || '')))[0];
    if (!decision) throw error;
    if (pendingAuthorityActions.has(decision.decision_id)) {
      return { status: 'waiting_approval', authority_decision: decision };
    }
    clearExternalAuthorityUI({ denyPending: true });
    const anchor = window.chatModule?.addMessage?.(
      'assistant',
      'Approval is required before I can run that exact worker action.',
      '',
      {
        source: 'external_agent_authority',
        worker: state.target,
        character_name: state.targetLabel || 'External worker',
      },
    );
    if (!anchor) throw new Error('The approval request could not be shown in chat.');
    anchor.dataset.externalAgentAuthority = 'true';
    const card = renderAuthorityApprovalCard(decision);
    if (!card) {
      anchor.remove();
      throw new Error('The approval request could not be shown in chat.');
    }
    card.dataset.externalAgentAuthority = 'true';
    pendingAuthorityActions.set(decision.decision_id, {
      path,
      options,
      decision,
      sessionId: String(sessionId),
      context: restored.context,
      fingerprint,
      lifecycle: authorityLifecycle,
    });
    return { status: 'waiting_approval', authority_decision: decision };
  }
  retryableRequestIds.delete(fingerprint);
  state.canonicalTask = task;
  state.canonicalTaskContext = restored.context;
  setComposerHint();
  window.jarvisVoice?.trackWorkerTask?.(task);
  return task;
}

async function handleExternalAuthorityDecision({ decisionId, choice, scope }) {
  const key = String(decisionId || '');
  const pending = pendingAuthorityActions.get(key);
  if (!pending) {
    const staleCard = document.querySelector(
      `.authority-approval-card[data-external-agent-authority="true"][data-decision-id="${CSS.escape(String(decisionId || ''))}"]`,
    );
    if (staleCard) throw new Error('This worker approval is stale. Submit the task again.');
    return false;
  }
  pendingAuthorityActions.delete(key);
  let receipt;
  try {
    receipt = await requestJson(`/api/authority/decisions/${encodeURIComponent(key)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ choice, scope }),
    });
  } catch (error) {
    document.querySelector(
      `.authority-approval-card[data-external-agent-authority="true"][data-decision-id="${CSS.escape(key)}"]`,
    )?.remove();
    throw new Error(`Approval outcome could not be confirmed. Submit the worker action again. ${error?.message || ''}`.trim());
  }
  renderAuthorityDecisionResolved({ choice, decision: pending.decision, receipt });
  if (choice === 'approve') {
    if (
      pending.lifecycle !== authorityLifecycle
      || currentSessionId() !== pending.sessionId
      || canonicalContext(pending.sessionId) !== pending.context
    ) {
      throw new Error('The conversation changed after approval. The worker action was not dispatched.');
    }
    let task;
    try {
      task = await dispatchExternalTaskRequest(pending.path, pending.options, pending);
      task = verifiedTaskResponse(task, {
        sessionId: pending.sessionId,
        context: pending.context,
        taskId: pending.decision?.preview?.task_id || '',
      });
    } catch (error) {
      if (error?.externalActionAmbiguous === true) retainAmbiguousRequest(pending.fingerprint);
      else retryableRequestIds.delete(pending.fingerprint);
      throw error;
    }
    retryableRequestIds.delete(pending.fingerprint);
    state.canonicalTask = task;
    state.canonicalTaskContext = pending.context;
    setComposerHint();
    window.jarvisVoice?.trackWorkerTask?.(task);
  } else {
    retryableRequestIds.delete(pending.fingerprint);
  }
  return true;
}

function bind() {
  if (document.documentElement.dataset.codexWorkspaceBound === '1') return;
  document.documentElement.dataset.codexWorkspaceBound = '1';
  byId('codex-project-list')?.addEventListener('click', event => {
    const button = event.target.closest('.codex-project-row[data-project-id]');
    if (button && !button.disabled) selectProject(button.dataset.projectId, button.dataset.projectName);
  });
  byId('codex-task-list')?.addEventListener('click', event => {
    const button = event.target.closest('.codex-task-row[data-task-id]');
    if (button) selectTask(button);
  });
  byId('codex-project-more')?.addEventListener('click', () => loadProjects({ append: true }));
  byId('codex-task-more')?.addEventListener('click', () => loadTasks({ append: true }));
  document.addEventListener('odysseus:conversation-target-changed', event => syncTarget(event.detail || {}));
  window.addEventListener('odysseus:session-rendered', event => {
    const sessionId = String(event.detail?.sessionId || '');
    if (!sessionId || sessionId === state.renderedSessionId) return;
    state.renderedSessionId = sessionId;
    clearTranscript();
    clearExternalAuthorityUI({ denyPending: true });
    restoreCanonicalTask(sessionId);
  });
  window.addEventListener('pagehide', () => clearExternalAuthorityUI({ denyPending: true }));
  const selected = getSelectedAgentSelection();
  if (selected) syncTarget(selected);
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind);
else bind();

window.codexWorkspaceBrowser = {
  open, close, syncTarget, loadProjects, loadTasks, getSelectedContext,
  isExternalTarget, submitExternalPrompt, handleExternalAuthorityDecision,
};
