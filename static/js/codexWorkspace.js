import { getSelectedAgentSelection } from './modelPicker.js';

const PAGE_SIZE = 50;

const state = {
  projectCursor: null,
  taskCursor: null,
  selectedProject: null,
  selectedProjectName: '',
  selectedTask: null,
  projectRequest: 0,
  taskRequest: 0,
};

function byId(id) { return document.getElementById(id); }

async function requestJson(path) {
  const response = await fetch(path, { credentials: 'same-origin' });
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
  if (!list.children.length) list.appendChild(statusRow('No projects are available for Friday.'));
}

function renderTasks(items, append = false) {
  const list = byId('codex-task-list');
  if (!list) return;
  if (!append) list.replaceChildren();
  items.forEach(task => {
    const taskId = String(task.task_id || '');
    const status = String(task.status || 'unknown');
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
    if (['running', 'queued', 'waiting', 'waiting_approval'].includes(status)) {
      button.append(activityDot('active', status.replaceAll('_', ' ')));
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
    const page = await requestJson(`/api/codex/projects/${encodeURIComponent(state.selectedProject)}/tasks?${params}`);
    if (requestId !== state.taskRequest) return;
    renderTasks(Array.isArray(page.items) ? page.items : [], append);
    state.taskCursor = page.next_cursor || null;
    if (more) more.hidden = !state.taskCursor;
  } catch (error) {
    if (requestId !== state.taskRequest || !list) return;
    list.replaceChildren(statusRow(error.message || 'Friday task list is unavailable.', true));
  }
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
  const subject = state.selectedTask?.title || `a new task in ${state.selectedProjectName}`;
  composer.placeholder = `Message Friday about ${subject}`;
  composer.dataset.codexWorkspaceHint = '1';
}

function clearSelection() {
  state.selectedTask = null;
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
  await loadTasks();
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
  document.dispatchEvent(new CustomEvent('odysseus:codex-task-selected', { detail: state.selectedTask }));
}

function open(detail = {}) {
  const browser = byId('codex-workspace-browser');
  if (!browser) return;
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
  const list = byId('codex-project-list');
  if (detail.available === false) {
    list?.replaceChildren(statusRow(detail.reason || 'Friday is not currently available.', true));
    if (byId('codex-project-more')) byId('codex-project-more').hidden = true;
    return;
  }
  loadProjects();
}

function close() {
  if (byId('codex-workspace-browser')) byId('codex-workspace-browser').hidden = true;
  if (byId('session-list')) byId('session-list').hidden = false;
  if (byId('chats-section-label')) byId('chats-section-label').textContent = 'Chats';
  if (byId('chats-library-btn')) byId('chats-library-btn').hidden = false;
  if (byId('session-sort-btn')) byId('session-sort-btn').hidden = false;
  clearSelection();
  window.sessionModule?.renderSessionList?.();
}

function syncTarget(detail = {}) {
  if (detail.target === 'pc-codex') open(detail);
  else close();
}

function getSelectedContext() {
  if (!state.selectedProject) return null;
  return {
    workspace: state.selectedProject,
    codexThreadId: state.selectedTask?.taskId || null,
  };
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
  const selected = getSelectedAgentSelection();
  if (selected) syncTarget(selected);
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind);
else bind();

window.codexWorkspaceBrowser = { open, close, syncTarget, loadProjects, loadTasks, getSelectedContext };
