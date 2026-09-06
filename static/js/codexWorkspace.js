import { getSelectedAgentSelection } from './modelPicker.js';

const PAGE_SIZE = 50;

const state = {
  projectCursor: null,
  taskCursor: null,
  selectedProject: null,
  projectRequest: 0,
  taskRequest: 0,
  identityLabel: 'Codex',
  selectedProjectName: '',
  selectedTask: null,
  activeTask: null,
  eventSource: null,
};

function byId(id) { return document.getElementById(id); }

async function requestJson(path, options = {}) {
  const response = await fetch(path, {
    credentials: 'same-origin',
    headers: options.body ? { 'Content-Type': 'application/json', ...(options.headers || {}) } : options.headers,
    ...options,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof body.detail === 'string' ? body.detail : (body.error || 'Codex catalog request failed');
    throw new Error(detail);
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

function renderProjects(items, append = false) {
  const list = byId('codex-project-list');
  if (!list) return;
  const taskView = byId('codex-task-view');
  if (!append) {
    if (taskView) {
      taskView.hidden = true;
      byId('codex-project-view')?.appendChild(taskView);
    }
    list.innerHTML = '';
  }
  items.forEach(project => {
    const group = document.createElement('div');
    group.className = 'codex-project-group';
    group.dataset.projectId = String(project.project_id || '');
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'codex-browser-row';
    button.dataset.projectId = String(project.project_id || '');
    button.dataset.projectName = String(project.display_name || project.project_id || 'Project');
    button.setAttribute('aria-expanded', 'false');
    button.disabled = project.availability !== 'available';
    button.title = button.disabled ? String(project.reason || 'Project unavailable') : String(project.approved_root || 'Approved project');
    const title = document.createElement('strong');
    title.textContent = String(project.display_name || project.project_id || 'Project');
    const detail = document.createElement('small');
    detail.textContent = button.disabled
      ? String(project.reason || 'unavailable').replace(/_/g, ' ')
      : `${Number.isFinite(project.task_count) ? `${project.task_count.toLocaleString()} tasks` : 'Open task catalog'} · ${project.approved_root}`;
    button.append(title, detail);
    group.appendChild(button);
    list.appendChild(group);
  });
  if (!list.children.length) list.appendChild(statusRow('No allowlisted Codex projects found.'));
}

function renderTasks(items, append = false) {
  const list = byId('codex-task-list');
  if (!list) return;
  if (!append) list.innerHTML = '';
  items.forEach(task => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'codex-browser-row';
    button.dataset.taskId = String(task.task_id || '');
    button.dataset.projectId = String(task.project_id || '');
    button.dataset.taskTitle = String(task.title || 'Untitled task');
    button.dataset.taskStatus = String(task.status || 'unknown');
    const title = document.createElement('strong');
    title.textContent = String(task.title || 'Untitled task');
    const detail = document.createElement('small');
    detail.textContent = `${String(task.status || 'unknown').replace(/_/g, ' ')} · ${new Date(Number(task.updated_at || 0) * 1000).toLocaleString()}`;
    button.append(title, detail);
    list.appendChild(button);
  });
  if (!list.children.length) list.appendChild(statusRow('No matching tasks found.'));
}

async function loadProjects({ append = false } = {}) {
  const list = byId('codex-project-list');
  const more = byId('codex-project-more');
  const requestId = ++state.projectRequest;
  if (!append) {
    state.projectCursor = null;
    if (list) list.replaceChildren(statusRow('Loading allowlisted projects…'));
  }
  if (more) more.hidden = true;
  const params = new URLSearchParams({
    query: byId('codex-project-search')?.value.trim() || '',
    limit: String(PAGE_SIZE),
  });
  if (append && state.projectCursor) params.set('cursor', state.projectCursor);
  try {
    const page = await requestJson(`/api/codex/projects?${params}`);
    if (requestId !== state.projectRequest) return;
    renderProjects(Array.isArray(page.items) ? page.items : [], append);
    state.projectCursor = page.next_cursor || null;
    if (more) more.hidden = !state.projectCursor;
  } catch (error) {
    if (requestId !== state.projectRequest || !list) return;
    list.replaceChildren(statusRow(error.message || 'Codex workstation is unavailable.', true));
  }
}

async function selectProject(projectId, displayName = '') {
  collapseProject();
  state.selectedProject = projectId;
  state.selectedProjectName = displayName || projectId;
  state.taskCursor = null;
  const group = [...document.querySelectorAll('.codex-project-group')]
    .find(item => item.dataset.projectId === String(projectId));
  const taskView = byId('codex-task-view');
  group?.querySelector('.codex-browser-row')?.setAttribute('aria-expanded', 'true');
  if (group && taskView) group.appendChild(taskView);
  byId('codex-project-view').hidden = false;
  taskView.hidden = false;
  byId('codex-browser-title').textContent = displayName || projectId;
  if (byId('codex-task-search')) byId('codex-task-search').value = '';
  await loadTasks();
}

function collapseProject() {
  const taskView = byId('codex-task-view');
  if (taskView) {
    taskView.hidden = true;
    byId('codex-project-view')?.appendChild(taskView);
  }
  document.querySelectorAll('.codex-project-group .codex-browser-row[aria-expanded="true"]')
    .forEach(button => button.setAttribute('aria-expanded', 'false'));
  state.selectedProject = null;
  state.selectedProjectName = '';
  byId('codex-browser-title').textContent = `${state.identityLabel} projects`;
}

function showAction(task = null) {
  state.selectedTask = task;
  byId('codex-project-view').hidden = true;
  byId('codex-task-view').hidden = true;
  byId('codex-action-view').hidden = false;
  byId('codex-browser-title').textContent = task ? 'Resume Codex task' : 'Create Codex task';
  byId('codex-task-identity').textContent = task
    ? `${task.title} · ${state.selectedProjectName} · exact task ${task.taskId}`
    : `${state.selectedProjectName} · approved root workspace:${state.selectedProject}`;
  byId('codex-task-run').textContent = task ? 'Resume selected task' : 'Start read-only task';
  byId('codex-task-prompt').value = '';
  byId('codex-task-write-consent').checked = false;
  byId('codex-task-live').hidden = true;
  byId('codex-task-events').replaceChildren();
  byId('codex-task-prompt').focus();
}

function taskStatus(task, detail = '') {
  const status = String(task?.status || 'running').replaceAll('_', ' ');
  const suffix = task?.codex_thread_id ? ` · thread ${task.codex_thread_id}` : '';
  byId('codex-task-status').textContent = `${detail || status}${suffix}`;
  const terminal = ['completed', 'failed', 'cancelled', 'blocked'].includes(String(task?.status || ''));
  byId('codex-task-steer').disabled = terminal;
  byId('codex-task-cancel').disabled = terminal;
}

function renderTaskEvent(event) {
  const events = byId('codex-task-events');
  if (!events || events.querySelector(`[data-event-id="${CSS.escape(String(event.event_id || event.seq || ''))}"]`)) return;
  const row = document.createElement('p');
  row.dataset.eventId = String(event.event_id || event.seq || '');
  row.className = `is-${String(event.type || 'progress')}`;
  row.textContent = String(event.text || event.type || 'Task update');
  if (event.type === 'artifact' && event.metadata?.citation) {
    const citation = document.createElement('code');
    citation.textContent = String(event.metadata.citation);
    citation.title = event.metadata.review_mode === 'reversible_edit'
      ? 'Reversible edit ready for review'
      : 'Read-only workspace citation';
    row.append(' ', citation);
  }
  events.appendChild(row);
  events.scrollTop = events.scrollHeight;
}

function observeTask(task, detail = '') {
  state.activeTask = task;
  byId('codex-task-live').hidden = false;
  taskStatus(task, detail);
  window.jarvisVoice?.trackWorkerTask?.(task);
  state.eventSource?.close();
  if (['completed', 'failed', 'cancelled', 'blocked'].includes(String(task.status || ''))) return;
  const source = new EventSource(`/api/agent-tasks/${encodeURIComponent(task.task_id)}/events`);
  state.eventSource = source;
  source.onmessage = message => {
    try {
      const event = JSON.parse(message.data);
      renderTaskEvent(event);
      if (event.metadata?.codex_thread_id) state.activeTask.codex_thread_id = event.metadata.codex_thread_id;
      if (event.type === 'result') state.activeTask.status = 'completed';
      else if (event.type === 'error') state.activeTask.status = 'failed';
      else if (event.type === 'cancelled') state.activeTask.status = 'cancelled';
      else if (event.type === 'question') state.activeTask.status = 'waiting';
      else if (event.type === 'approval_required') state.activeTask.status = 'waiting_approval';
      else state.activeTask.status = 'running';
      taskStatus(state.activeTask);
      if (['result', 'error', 'cancelled'].includes(event.type)) {
        source.close();
        if (state.eventSource === source) state.eventSource = null;
      }
    } catch {}
  };
  source.onerror = () => taskStatus(state.activeTask, 'Waiting to reconnect');
}

function currentSessionId() {
  return String(window.sessionModule?.getCurrentSessionId?.() || '');
}

async function runSelectedTask() {
  const sessionId = currentSessionId();
  const prompt = byId('codex-task-prompt').value.trim();
  if (!sessionId) throw new Error('Open a chat before starting a Codex task.');
  if (!prompt) throw new Error('Enter a bounded request first.');
  const button = byId('codex-task-run');
  const writeApproved = byId('codex-task-write-consent').checked;
  button.disabled = true;
  try {
    let task = await requestJson('/api/agent-tasks', {
      method: 'POST',
      body: JSON.stringify({
        worker: 'pc-codex',
        session_id: sessionId,
        workspace: state.selectedProject,
        prompt,
        permission_mode: writeApproved ? 'workspace_write' : 'read_only',
        approved: writeApproved,
        persist_prompt: true,
        codex_thread_id: state.selectedTask?.taskId || null,
        thread_title: state.selectedTask ? null : prompt.slice(0, 120),
        request_id: globalThis.crypto?.randomUUID?.() || `browser-${Date.now()}`,
      }),
    });
    let detail = state.selectedTask ? 'Resume requested' : 'Task created';
    if (task.reused) {
      task = await requestJson(`/api/agent-tasks/${encodeURIComponent(task.task_id)}/steer`, {
        method: 'POST', body: JSON.stringify({ prompt }),
      });
      detail = 'Reconnected and steered existing task';
    }
    observeTask(task, detail);
    document.dispatchEvent(new CustomEvent('odysseus:codex-task-started', { detail: task }));
  } finally {
    button.disabled = false;
  }
}

async function steerActiveTask() {
  const prompt = byId('codex-task-steer-prompt').value.trim();
  if (!state.activeTask?.task_id || !prompt) throw new Error('Enter a steering request first.');
  const task = await requestJson(`/api/agent-tasks/${encodeURIComponent(state.activeTask.task_id)}/steer`, {
    method: 'POST', body: JSON.stringify({ prompt }),
  });
  byId('codex-task-steer-prompt').value = '';
  state.activeTask = task;
  taskStatus(task, 'Steering accepted');
}

async function cancelActiveTask() {
  if (!state.activeTask?.task_id) return;
  const task = await requestJson(`/api/agent-tasks/${encodeURIComponent(state.activeTask.task_id)}/cancel`, { method: 'POST' });
  state.activeTask = task;
  taskStatus(task, 'Cancellation requested');
}

function reportActionError(error) {
  const row = statusRow(error.message || 'Codex task request failed.', true);
  byId('codex-task-events').replaceChildren(row);
  byId('codex-task-live').hidden = false;
  byId('codex-task-status').textContent = 'Task request failed';
}

async function loadTasks({ append = false } = {}) {
  if (!state.selectedProject) return;
  const list = byId('codex-task-list');
  const more = byId('codex-task-more');
  const requestId = ++state.taskRequest;
  if (!append) {
    state.taskCursor = null;
    if (list) list.replaceChildren(statusRow('Loading tasks…'));
  }
  if (more) more.hidden = true;
  const params = new URLSearchParams({
    query: byId('codex-task-search')?.value.trim() || '',
    limit: String(PAGE_SIZE),
  });
  if (append && state.taskCursor) params.set('cursor', state.taskCursor);
  try {
    const page = await requestJson(`/api/codex/projects/${encodeURIComponent(state.selectedProject)}/tasks?${params}`);
    if (requestId !== state.taskRequest) return;
    renderTasks(Array.isArray(page.items) ? page.items : [], append);
    state.taskCursor = page.next_cursor || null;
    if (more) more.hidden = !state.taskCursor;
  } catch (error) {
    if (requestId !== state.taskRequest || !list) return;
    list.replaceChildren(statusRow(error.message || 'Codex task catalog is unavailable.', true));
  }
}

function open(detail = {}) {
  const browser = byId('codex-workspace-browser');
  if (!browser) return;
  const sessionsSection = byId('sessions-section');
  const sessionList = byId('session-list');
  const label = byId('chats-section-label');
  state.identityLabel = detail.label || 'Codex';
  if (sessionsSection) sessionsSection.classList.remove('hidden');
  if (sessionList) sessionList.hidden = true;
  if (label) label.textContent = `${state.identityLabel} Projects`;
  if (byId('chats-library-btn')) byId('chats-library-btn').hidden = true;
  if (byId('session-sort-btn')) byId('session-sort-btn').hidden = true;
  const bulkBar = byId('session-bulk-bar');
  if (bulkBar && !bulkBar.classList.contains('hidden')) byId('session-bulk-cancel')?.click();
  bulkBar?.classList.add('hidden');
  browser.hidden = false;
  byId('codex-project-view').hidden = false;
  byId('codex-action-view').hidden = true;
  collapseProject();
  const list = byId('codex-project-list');
  if (detail.available === false) {
    list?.replaceChildren(statusRow(detail.reason || 'Friday is not currently available.', true));
    byId('codex-project-more').hidden = true;
    return;
  }
  loadProjects();
}

function close(detail = {}) {
  const browser = byId('codex-workspace-browser');
  const sessionList = byId('session-list');
  const label = byId('chats-section-label');
  if (browser) browser.hidden = true;
  if (sessionList) sessionList.hidden = false;
  if (label) label.textContent = `${detail.label || 'Jarvis'} Chats`;
  if (byId('chats-library-btn')) byId('chats-library-btn').hidden = false;
  if (byId('session-sort-btn')) byId('session-sort-btn').hidden = false;
  state.eventSource?.close();
  state.eventSource = null;
  window.sessionModule?.renderSessionList?.();
}

function syncTarget(detail = {}) {
  if (detail.target === 'pc-codex') open(detail);
  else close(detail);
}

function debounce(fn, delay = 250) {
  let timer;
  return () => {
    clearTimeout(timer);
    timer = setTimeout(fn, delay);
  };
}

function bind() {
  if (document.documentElement.dataset.codexWorkspaceBound === '1') return;
  document.documentElement.dataset.codexWorkspaceBound = '1';
  byId('codex-project-return')?.addEventListener('click', () => {
    collapseProject();
    byId('codex-action-view').hidden = true;
  });
  byId('codex-project-list')?.addEventListener('click', event => {
    const button = event.target.closest('.codex-browser-row[data-project-id]');
    if (button && !button.dataset.taskId && !button.disabled) {
      selectProject(button.dataset.projectId, button.dataset.projectName);
    }
  });
  byId('codex-task-list')?.addEventListener('click', event => {
    const button = event.target.closest('.codex-browser-row[data-task-id]');
    if (!button) return;
    const task = {
      taskId: button.dataset.taskId,
      projectId: button.dataset.projectId,
      title: button.dataset.taskTitle || 'Codex task',
      status: button.dataset.taskStatus || 'unknown',
    };
    document.dispatchEvent(new CustomEvent('odysseus:codex-task-selected', { detail: task }));
    showAction(task);
  });
  byId('codex-new-task')?.addEventListener('click', () => showAction());
  byId('codex-task-return')?.addEventListener('click', () => {
    state.selectedTask = null;
    byId('codex-action-view').hidden = true;
    byId('codex-project-view').hidden = false;
    byId('codex-task-view').hidden = false;
    byId('codex-browser-title').textContent = state.selectedProjectName || state.selectedProject;
  });
  byId('codex-task-run')?.addEventListener('click', () => runSelectedTask().catch(reportActionError));
  byId('codex-task-write-consent')?.addEventListener('change', event => {
    byId('codex-task-run').textContent = event.target.checked
      ? (state.selectedTask ? 'Resume with approved edit access' : 'Start approved edit task')
      : (state.selectedTask ? 'Resume selected task' : 'Start read-only task');
  });
  byId('codex-task-steer')?.addEventListener('click', () => steerActiveTask().catch(reportActionError));
  byId('codex-task-cancel')?.addEventListener('click', () => cancelActiveTask().catch(reportActionError));
  byId('codex-project-more')?.addEventListener('click', () => loadProjects({ append: true }));
  byId('codex-task-more')?.addEventListener('click', () => loadTasks({ append: true }));
  byId('codex-project-search')?.addEventListener('input', debounce(() => loadProjects()));
  byId('codex-task-search')?.addEventListener('input', debounce(() => loadTasks()));
  document.addEventListener('odysseus:conversation-target-changed', event => syncTarget(event.detail || {}));
  const selected = getSelectedAgentSelection();
  if (selected) syncTarget(selected);
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind);
else bind();

window.codexWorkspaceBrowser = { open, close, syncTarget, loadProjects, loadTasks };
