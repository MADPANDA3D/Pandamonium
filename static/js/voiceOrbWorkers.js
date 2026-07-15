// Fixed-worker activity rail. Worker execution continues when voice mode closes.

import sessionModule from './sessions.js';

const TERMINAL = new Set(['completed', 'failed', 'cancelled', 'blocked']);
const LABELS = { 'pc-codex': 'PC Codex', hermes: 'Hermes', 'vps-codex': 'VPS Codex' };
const tasks = new Map();
const streams = new Map();
const renderedEvents = new Set();
let visibleSessionId = '';
let restoreGeneration = 0;

const $ = id => document.getElementById(id);

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    credentials: 'same-origin',
    headers: options.body ? { 'Content-Type': 'application/json', ...(options.headers || {}) } : options.headers,
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || 'Worker request failed');
  return data;
}

function taskElement(taskId) {
  return Array.from(document.querySelectorAll('.voice-worker-task[data-task-id]'))
    .find(node => node.dataset.taskId === taskId) || null;
}

function setRailVisibility() {
  const rail = $('voice-worker-rail');
  if (rail) rail.hidden = !$('voice-worker-list')?.children.length;
}

function setTaskStatus(node, task) {
  if (!node) return;
  const status = String(task.status || 'running');
  node.dataset.status = status;
  node.querySelector('.voice-worker-state').textContent = status.replaceAll('_', ' ');
  const cancel = node.querySelector('.voice-worker-cancel');
  cancel.hidden = TERMINAL.has(status);
  cancel.disabled = TERMINAL.has(status);
  node.open = !TERMINAL.has(status) || ['failed', 'cancelled', 'blocked'].includes(status);
}

function renderEvent(task, event) {
  const eventId = String(event.event_id || `${event.task_id}:${event.seq}`);
  const key = `${task.task_id}:${eventId}`;
  if (renderedEvents.has(key) || event.type === 'accepted') return;
  const node = taskElement(task.task_id);
  const history = node?.querySelector('.voice-worker-events');
  if (!history) return;
  renderedEvents.add(key);
  const row = document.createElement('p');
  row.className = `voice-worker-event is-${event.type || 'progress'}`;
  row.textContent = String(event.text || '');
  history.appendChild(row);
  if (event.type === 'result') task.status = 'completed';
  else if (event.type === 'error') task.status = 'failed';
  else if (event.type === 'cancelled') task.status = 'cancelled';
  else if (event.type === 'question') task.status = 'waiting';
  else if (event.type === 'approval_required') task.status = 'waiting_approval';
  else task.status = 'running';
  setTaskStatus(node, task);
}

async function cancelTask(taskId) {
  if (!window.confirm('Cancel this worker task?')) return;
  const task = await fetchJson(`/api/agent-tasks/${encodeURIComponent(taskId)}/cancel`, { method: 'POST' });
  trackWorkerTask(task);
}

function ensureTask(task) {
  if (!task?.task_id || task.session_id !== visibleSessionId) return null;
  tasks.set(task.task_id, { ...(tasks.get(task.task_id) || {}), ...task });
  task = tasks.get(task.task_id);
  let node = taskElement(task.task_id);
  if (!node) {
    node = document.createElement('details');
    node.className = 'voice-worker-task';
    node.dataset.taskId = task.task_id;
    const summary = document.createElement('summary');
    const label = document.createElement('strong');
    label.textContent = LABELS[task.worker] || 'Worker';
    const workspace = document.createElement('span');
    workspace.className = 'voice-worker-workspace';
    workspace.textContent = task.workspace || '';
    const state = document.createElement('span');
    state.className = 'voice-worker-state';
    summary.append(label, workspace, state);
    const events = document.createElement('div');
    events.className = 'voice-worker-events';
    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.className = 'voice-worker-cancel';
    cancel.textContent = 'Cancel task';
    cancel.addEventListener('click', event => {
      event.preventDefault();
      cancelTask(task.task_id).catch(() => {});
    });
    node.append(summary, events, cancel);
    $('voice-worker-list')?.appendChild(node);
  }
  setTaskStatus(node, task);
  [...(task.events || [])]
    .sort((left, right) => Number(left.seq || 0) - Number(right.seq || 0))
    .forEach(event => renderEvent(task, event));
  setRailVisibility();
  return node;
}

function followTask(taskId) {
  if (!taskId || streams.has(taskId) || TERMINAL.has(tasks.get(taskId)?.status)) return;
  const source = new EventSource(`/api/agent-tasks/${encodeURIComponent(taskId)}/events`);
  streams.set(taskId, source);
  source.onmessage = message => {
    try {
      const event = JSON.parse(message.data);
      const task = tasks.get(taskId);
      if (!task) return;
      if (!Array.isArray(task.events)) task.events = [];
      if (!task.events.some(row => String(row.event_id) === String(event.event_id))) task.events.push(event);
      renderEvent(task, event);
      if (['result', 'error', 'cancelled'].includes(event.type)) {
        source.close();
        streams.delete(taskId);
      }
    } catch {}
  };
}

export function trackWorkerTask(task) {
  const node = ensureTask(task);
  if (node && !TERMINAL.has(task.status)) followTask(task.task_id);
}

async function restoreTasks(sessionId) {
  const generation = ++restoreGeneration;
  streams.forEach(source => source.close());
  streams.clear();
  tasks.clear();
  renderedEvents.clear();
  if ($('voice-worker-list')) $('voice-worker-list').replaceChildren();
  setRailVisibility();
  if (!sessionId) return;
  try {
    const data = await fetchJson(`/api/agent-tasks?session_id=${encodeURIComponent(sessionId)}`);
    if (generation !== restoreGeneration || sessionId !== visibleSessionId) return;
    [...(data.tasks || [])].reverse().forEach(trackWorkerTask);
  } catch {}
}

function watchSession() {
  const current = String(sessionModule.getCurrentSessionId?.() || '');
  if (current === visibleSessionId) return;
  visibleSessionId = current;
  restoreTasks(current);
}

function init() {
  watchSession();
  window.setInterval(watchSession, 1000);
  window.addEventListener('pagehide', () => streams.forEach(source => source.close()));
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
else init();

export default { trackWorkerTask };
