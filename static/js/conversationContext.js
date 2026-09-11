import { getSelectedAgentSelection } from './modelPicker.js';

const byId = id => document.getElementById(id);
const levels = ['low', 'medium', 'high', 'xhigh', 'max'];
const rounds = [20, 40, 80, 120, 200];
const names = { low: 'Low', medium: 'Medium', high: 'High', xhigh: 'Very high', max: 'Maximum', ultra: 'Ultra', minimal: 'Minimal', none: 'Off' };
let agentEffort = '';
let details = null;
let activity = { sources: [], tools: [], outputs: [] };
let request = 0;
let activityCursor = null;
let activityOffset = null;
let activityLoading = false;
let drawerSection = '';
let environment = [];
const activityCursors = new Set();
const array = value => Array.isArray(value) ? value : [];

function sessionId() { return window.sessionModule?.getCurrentSessionId?.() || ''; }
function currentSession() { return window.sessionModule?.getSessions?.().find(session => session.id === sessionId()) || {}; }
function target() { return getSelectedAgentSelection()?.target || currentSession().agent_target || 'jarvis'; }

function renderEffort() {
  const native = target() === 'pc-codex';
  const card = byId('conversation-effort-card');
  card.hidden = !native && target() !== 'jarvis';
  card.classList.toggle('is-native', native);
  byId('codex-model-controls').hidden = !native;
  byId('codex-reasoning').hidden = !native;
  const range = byId('conversation-effort');
  const options = native ? [...byId('codex-reasoning').options].map(option => option.value).filter(Boolean) : levels;
  const chosen = native ? byId('codex-reasoning').value : agentEffort;
  const index = Math.max(0, options.indexOf(chosen));
  range.max = String(Math.max(0, options.length - 1));
  range.value = String(chosen ? index : native ? 0 : 2);
  range.disabled = !options.length;
  range.style.setProperty('--effort-fill', `${Number(range.max) ? Number(range.value) / Number(range.max) * 100 : 0}%`);
  const label = names[chosen] || chosen || 'Default';
  byId('conversation-effort-label').textContent = native ? 'Reasoning effort' : 'Agent work budget';
  byId('conversation-effort-value').textContent = label;
  range.setAttribute('aria-valuetext', native ? label : chosen ? `${label}, up to ${rounds[index]} rounds` : 'Installation default');
  byId('conversation-effort-help').textContent = native
    ? options.length ? 'Codex reasoning for the next turn.' : byId('codex-model-status').textContent.includes('unavailable') ? byId('codex-model-status').textContent : 'Choose a Codex model to adjust reasoning. Default keeps the task or node setting.'
    : `${chosen ? `Up to ${rounds[index]} rounds` : 'Uses the installation default'}. A round asks the model, runs its requested tools, and returns their results. Stops when finished. Applies to the next text turn.`;
  renderPanel();
}

function list(id, values, empty) {
  const items = [...new Set(values.filter(value => typeof value === 'string' && value.trim()))];
  byId(id).replaceChildren(...(items.length ? items.slice(0, id.endsWith('sources') ? 3 : 1) : [empty]).map(text => {
    const item = document.createElement('li');
    item.textContent = id.endsWith('sources') || id.endsWith('outputs') ? text.split(/[\\/]/).pop() : text;
    item.title = text;
    return item;
  }));
  const button = document.querySelector(`[data-context-view="${id.replace('session-context-', '')}"]`);
  button.hidden = !items.length && !activityCursor && !activityOffset;
}

function renderPanel() {
  const session = currentSession();
  const context = window.codexWorkspaceBrowser?.getSelectedContext?.();
  const native = target() === 'pc-codex';
  const model = native ? byId('codex-model').selectedOptions[0]?.textContent : session.model || window.sessionModule?.getCurrentModel?.();
  const agent = getSelectedAgentSelection();
  const catalogTask = native || agent?.external;
  const pairs = [
    ['Agent', agent?.label || target()],
    ['Runtime', agent?.runtime || 'Not reported'],
    ['Runs on', agent?.location || 'Not reported'],
    ['Project', catalogTask ? details?.cwd || context?.projectName || context?.workspace || 'Choose a project' : session.folder || 'No project selected'],
    ['Task', catalogTask ? details?.title || context?.title || 'New task' : session.name || 'New task'],
    ['Next turn model', model || 'Default'],
  ];
  if (native) {
    pairs.push(['Next turn reasoning', names[byId('codex-reasoning').value] || 'Task / node default']);
    if (details?.model) pairs.push(['Recorded model', details.model]);
    if (details?.recorded_branch) pairs.push(['Recorded branch', details.recorded_branch]);
  } else if (target() === 'jarvis') {
    pairs.push(['Next text turn budget', agentEffort ? `${names[agentEffort]} · up to ${rounds[levels.indexOf(agentEffort)]} rounds` : 'Installation default']);
  }
  environment = pairs;
  const compact = pairs.filter(([label]) => !['Runtime', 'Task', 'Recorded model', 'Recorded branch'].includes(label));
  byId('session-context-environment').replaceChildren(...compact.flatMap(([label, value]) => {
    const term = document.createElement('dt'); term.textContent = { 'Next turn model': 'Next model', 'Next turn reasoning': 'Reasoning', 'Next text turn budget': 'Work budget' }[label] || label;
    const text = document.createElement('dd'); text.textContent = String(label === 'Project' && catalogTask ? context?.projectName || value : value); text.title = String(value);
    return [term, text];
  }));
  list('session-context-sources', activity.sources, 'No sources recorded');
  list('session-context-tools', activity.tools, 'No tools recorded');
  list('session-context-outputs', activity.outputs, 'No outputs recorded');
  renderDrawer();
}

function resetActivity() {
  activity = { sources: [], tools: [], outputs: [] };
  activityCursor = null; activityOffset = null; activityLoading = false; activityCursors.clear();
  if (byId('session-context-drawer').open) byId('session-context-drawer').close();
}

function mergeActivity(data) {
  for (const key of ['sources', 'tools', 'outputs']) activity[key] = [...new Set([...activity[key], ...array(data[key])])];
}

function historyActivity(history) {
  const data = { sources: [], tools: [], outputs: [] };
  for (const message of array(history)) {
    const meta = message.metadata || {};
    for (const item of [...array(meta.attachments), ...array(meta.web_sources), ...array(meta.research_sources), ...array(meta.rag_sources)]) {
      data.sources.push(item?.name || item?.title || item?.filename || item?.url);
    }
    for (const event of array(meta.tool_events).filter(Boolean)) {
      data.tools.push(event.tool || event.name);
      if (event.doc_id) data.outputs.push(event.doc_title || event.title || `Document ${event.doc_id}`);
    }
  }
  return data;
}

function renderDrawer() {
  if (!drawerSection) return;
  const body = byId('session-context-drawer-list');
  const values = drawerSection === 'environment' ? environment.map(([label, value]) => `${label}: ${value}`) : activity[drawerSection];
  body.replaceChildren(...[...new Set(values.filter(Boolean))].map(value => {
    const item = document.createElement('li'); item.textContent = value; return item;
  }));
  const more = byId('session-context-drawer-more');
  more.hidden = drawerSection === 'environment' || (!activityCursor && !activityOffset);
  more.disabled = activityLoading;
  more.textContent = activityLoading ? 'Loading earlier activity…' : 'Load earlier activity';
  byId('session-context-drawer-status').textContent = drawerSection === 'environment' ? ''
    : `${body.children.length} items${activityCursor || activityOffset ? ' loaded; earlier activity available' : '; all available history loaded'}.`;
}

async function loadEarlierActivity() {
  if (activityLoading || (!activityCursor && !activityOffset)) return;
  const generation = request;
  activityLoading = true;
  renderDrawer();
  try {
    const context = window.codexWorkspaceBrowser?.getSelectedContext?.();
    let path;
    if (activityCursor && context?.codexThreadId) {
      path = `/api/codex/projects/${encodeURIComponent(context.workspace)}/tasks/${encodeURIComponent(context.codexThreadId)}/history?${new URLSearchParams({ cursor: activityCursor, limit: '5' })}`;
    } else {
      const offset = Math.max(0, activityOffset - 50);
      path = `/api/history/${encodeURIComponent(sessionId())}?offset=${offset}&limit=${activityOffset - offset}`;
    }
    const response = await fetch(path, { credentials: 'same-origin' });
    if (!response.ok) throw new Error('Earlier activity could not be loaded. Retry to see the remaining items.');
    const data = await response.json();
    if (generation !== request) return;
    if (data.next_cursor && activityCursors.has(data.next_cursor)) throw new Error('Activity pagination stopped making progress.');
    if (data.next_cursor) activityCursors.add(data.next_cursor);
    mergeActivity(data.activity || historyActivity(data.history));
    activityCursor = data.next_cursor || null;
    activityOffset = data.has_more_before ? data.offset : null;
    activityLoading = false;
    renderPanel();
    if (byId('session-context-drawer').open && drawerSection !== 'environment' && (activityCursor || activityOffset)) await loadEarlierActivity();
  } catch (error) {
    if (generation === request) {
      activityLoading = false; renderDrawer();
      byId('session-context-drawer-status').textContent = error.message;
    }
  }
}

async function loadDetails(task) {
  const generation = ++request;
  details = null;
  resetActivity();
  renderPanel();
  byId('session-context-status').textContent = 'Loading session details…';
  try {
    const response = await fetch(`/api/codex/projects/${encodeURIComponent(task.projectId)}/tasks/${encodeURIComponent(task.taskId)}/history?limit=5`, { credentials: 'same-origin' });
    if (!response.ok) throw new Error('Session details are unavailable.');
    const data = await response.json();
    if (generation !== request) return;
    if (data.task?.task_id !== task.taskId || data.task?.project_id !== task.projectId) throw new Error('Session details do not match the selected task.');
    details = data.task;
    mergeActivity(data.activity || {});
    activityCursor = data.next_cursor || null;
    if (activityCursor) activityCursors.add(activityCursor);
    byId('session-context-status').textContent = 'Recorded session activity';
    renderPanel();
  } catch (error) {
    if (generation === request) byId('session-context-status').textContent = error.message;
  }
}

async function loadHistory() {
  const context = window.codexWorkspaceBrowser?.getSelectedContext?.();
  if (target() === 'pc-codex' && context?.codexThreadId) {
    return loadDetails({ projectId: context.workspace, taskId: context.codexThreadId });
  }
  const generation = ++request;
  details = null;
  resetActivity();
  byId('session-context-status').textContent = 'Recent session activity';
  renderPanel();
  if (!sessionId() || target() === 'pc-codex') return;
  try {
    const response = await fetch(`/api/history/${encodeURIComponent(sessionId())}?limit=50`, { credentials: 'same-origin' });
    if (!response.ok) throw new Error('History is unavailable.');
    const payload = await response.json();
    if (generation !== request) return;
    mergeActivity(historyActivity(payload.history));
    activityOffset = payload.has_more_before ? payload.offset : null;
    renderPanel();
  } catch (error) {
    if (generation === request) byId('session-context-status').textContent = error.message;
  }
}

function setOpen(open) {
  byId('session-context-panel').hidden = !open;
  byId('chat-container').classList.toggle('context-open', open);
  byId('session-context-toggle').setAttribute('aria-expanded', String(open));
}

function bind() {
  document.querySelectorAll('[data-context-view]').forEach(button => button.addEventListener('click', () => {
    drawerSection = button.dataset.contextView;
    byId('session-context-drawer-title').textContent = { environment: 'Environment', sources: 'Sources', tools: 'Tools used', outputs: 'Outputs' }[drawerSection];
    renderDrawer();
    byId('session-context-drawer').showModal();
    if (drawerSection !== 'environment') loadEarlierActivity();
  }));
  byId('session-context-drawer-close').addEventListener('click', () => byId('session-context-drawer').close());
  byId('session-context-drawer-more').addEventListener('click', loadEarlierActivity);
  byId('session-context-toggle').addEventListener('click', () => setOpen(byId('session-context-panel').hidden));
  byId('session-context-close').addEventListener('click', () => { setOpen(false); byId('session-context-toggle').focus(); });
  byId('session-context-panel').addEventListener('keydown', event => { if (event.key === 'Escape') byId('session-context-close').click(); });
  byId('model-picker-btn').addEventListener('click', () => { if (window.innerWidth < 1250) setOpen(false); });
  byId('conversation-effort-card').addEventListener('click', event => event.stopPropagation());
  byId('conversation-effort').addEventListener('input', event => {
    const index = Number(event.target.value);
    if (target() === 'pc-codex') {
      const select = byId('codex-reasoning');
      select.selectedIndex = index;
      select.dispatchEvent(new Event('change'));
    } else agentEffort = levels[index] || '';
    renderEffort();
  });
  byId('conversation-effort-reset').addEventListener('click', () => {
    if (target() === 'pc-codex') { byId('codex-model').value = ''; byId('codex-model').dispatchEvent(new Event('change')); }
    else agentEffort = '';
    renderEffort();
  });
  byId('codex-reasoning').addEventListener('change', renderEffort);
  document.addEventListener('odysseus:effort-options-changed', renderEffort);
  document.addEventListener('odysseus:conversation-target-changed', () => { loadHistory(); renderEffort(); });
  document.addEventListener('odysseus:codex-task-selected', () => {
    request += 1; details = null; resetActivity(); renderPanel();
    byId('session-context-status').textContent = 'Loading session details…';
  });
  document.addEventListener('odysseus:codex-history-loaded', event => {
    const data = event.detail;
    if (!data.earlier) {
      request += 1; resetActivity(); details = data.task;
      activityCursor = data.next_cursor || null;
      if (activityCursor) activityCursors.add(activityCursor);
    }
    mergeActivity(data.activity || {});
    byId('session-context-status').textContent = 'Recorded session activity';
    renderPanel();
  });
  document.addEventListener('odysseus:workspace-context-changed', loadHistory);
  document.addEventListener('odysseus:codex-history-failed', event => { byId('session-context-status').textContent = event.detail.message; });
  window.addEventListener('odysseus:session-rendered', loadHistory);
  window.addEventListener('odysseus:session-activity', event => {
    if (event.detail.sessionId !== sessionId()) return;
    if (event.detail.tool) activity.tools.push(event.detail.tool);
    if (event.detail.sources) activity.sources.push(...event.detail.sources.map(item => item.name));
    renderPanel();
  });
  window.addEventListener('odysseus:turn-completed', event => { if (event.detail.sessionId === sessionId()) loadHistory(); });
  setOpen(window.matchMedia('(min-width: 1250px)').matches);
  renderEffort();
  loadHistory();
}

window.conversationContext = { getAgentEffort: () => target() === 'jarvis' ? agentEffort : '' };
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind);
else bind();
