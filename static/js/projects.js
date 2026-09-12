// static/js/projects.js
//
// Pandamonium agent workstation projects (MAD-902). Each project is a real
// directory on the machine Pandamonium runs on: create a new working folder or
// import an existing one, then start a session bound to it. The project list
// is server-backed (DATA_DIR/projects.json), so it survives reloads and is
// shared across browsers for the installation.

import uiModule from './ui.js';
import { setWorkspace, pickFolder } from './workspace.js';

const API_BASE = window.location.origin;
let _projects = [];

const _FOLDER_SVG = '<svg class="workspace-row-icon" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>';
const _PLUS_SVG = '<svg class="list-item-plus-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="width:11px;height:11px;"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>';

function _projectPath(project) {
  return project.resolved_path || project.path || '';
}

function _renderRow(project) {
  const row = document.createElement('div');
  row.className = 'list-item project-item';
  row.dataset.projectId = project.id;
  row.setAttribute('role', 'button');
  row.tabIndex = 0;
  row.title = project.available === false
    ? `${project.path} — ${project.reason || 'unavailable'}`
    : project.path;

  const icon = document.createElement('span');
  icon.className = 'project-item-icon';
  icon.innerHTML = _FOLDER_SVG;
  const name = document.createElement('span');
  name.className = 'grow';
  name.textContent = project.name;
  const add = document.createElement('button');
  add.type = 'button';
  add.className = 'list-item-plus-btn project-session-btn';
  add.title = `New session in ${project.name}`;
  add.setAttribute('aria-label', `New session in ${project.name}`);
  add.innerHTML = _PLUS_SVG;
  add.addEventListener('click', event => {
    event.stopPropagation();
    _startSession(project);
  });

  if (project.available === false) {
    row.classList.add('project-unavailable');
    row.setAttribute('aria-disabled', 'true');
  }
  row.append(icon, name, add);
  row.addEventListener('click', () => _selectProject(project));
  row.addEventListener('keydown', event => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      _selectProject(project);
    }
  });
  return row;
}

function _render() {
  const list = document.getElementById('projects-list');
  if (!list) return;
  list.replaceChildren(..._projects.map(_renderRow));
}

function _guardAvailable(project) {
  if (project.available !== false) return true;
  uiModule.showError(`${project.name}: ${project.reason || 'folder is unavailable'}`);
  return false;
}

function _selectProject(project) {
  if (!_guardAvailable(project)) return;
  setWorkspace(_projectPath(project));
  uiModule.showToast(`Project: ${project.name}`);
}

function _startSession(project) {
  if (!_guardAvailable(project)) return;
  setWorkspace(_projectPath(project));
  const newChat = document.getElementById('sidebar-new-chat-btn');
  if (newChat) newChat.click();
  uiModule.showToast(`New session in ${project.name}`);
}

async function _addProject(payload) {
  const response = await fetch(`${API_BASE}/api/projects`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'same-origin',
    body: JSON.stringify(payload),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || 'Could not add the project');
  _projects = Array.isArray(data.projects) ? data.projects : [];
  _render();
  return data.project || null;
}

async function _createProject() {
  let name = '';
  try {
    name = await uiModule.styledPrompt('Create a new project folder for Pandamonium.', {
      title: 'New project',
      placeholder: 'Project name',
      confirmText: 'Create',
    });
  } catch (_) { name = ''; }
  name = (name || '').trim();
  if (!name) return;
  try {
    const project = await _addProject({ name });
    if (project) uiModule.showToast(`Project created: ${project.name}`);
  } catch (error) {
    uiModule.showError(error.message || 'Could not create the project');
  }
}

async function _importProject() {
  const path = await pickFolder();
  if (!path) return;
  try {
    const project = await _addProject({ path });
    if (project) uiModule.showToast(`Project added: ${project.name}`);
  } catch (error) {
    uiModule.showError(error.message || 'Could not add the project');
  }
}

function _bindAddMenu() {
  const button = document.getElementById('projects-add-btn');
  const menu = document.getElementById('projects-add-menu');
  if (!button || !menu) return;
  const closeMenu = () => {
    menu.classList.remove('show');
    button.setAttribute('aria-expanded', 'false');
  };
  button.addEventListener('click', event => {
    event.stopPropagation();
    const open = !menu.classList.contains('show');
    menu.classList.toggle('show', open);
    button.setAttribute('aria-expanded', String(open));
  });
  document.getElementById('project-create-option')?.addEventListener('click', () => {
    closeMenu();
    _createProject();
  });
  document.getElementById('project-import-option')?.addEventListener('click', () => {
    closeMenu();
    _importProject();
  });
  document.addEventListener('click', event => {
    if (menu.classList.contains('show') && !menu.contains(event.target) && !button.contains(event.target)) {
      closeMenu();
    }
  });
}

export async function refreshProjects() {
  try {
    const response = await fetch(`${API_BASE}/api/projects`, { credentials: 'same-origin' });
    if (!response.ok) throw new Error(`projects_${response.status}`);
    const data = await response.json();
    _projects = Array.isArray(data.projects) ? data.projects : [];
    _render();
  } catch (_) {
    _projects = [];
    _render();
  }
}

export function initProjects() {
  if (!document.getElementById('projects-section')) return;
  _bindAddMenu();
  refreshProjects();
}

export default { initProjects, refreshProjects };
