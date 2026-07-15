const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const read = file => fs.readFileSync(path.join(__dirname, '..', file), 'utf8');
const calendar = read('static/js/calendar.js');
const modals = read('static/js/modalManager.js');
const chatStream = read('static/js/chatStream.js');

assert.match(calendar, /function getViewState\(\)[\s\S]*?open: _open && !minimized[\s\S]*?view: _view[\s\S]*?date: _ds\(_currentDate\)/);
assert.ok(calendar.indexOf("Modals.isMinimized('calendar-modal')") < calendar.indexOf('if (_open) return;'));
assert.match(modals, /_REPORTABLE_VIEWS = \{\s*'calendar-modal': 'calendar',\s*'doc-panel': 'document'/);
assert.match(modals, /return \{ active_view: activeView, minimized_views: minimizedViews \}/);
assert.match(chatStream, /export function collectClientState\(\)/);
assert.match(chatStream, /uiEvent === 'open_view' && uiData\.view === 'calendar'/);
assert.match(chatStream, /uiEvent === 'close_view' && uiData\.view === 'document'/);
assert.match(chatStream, /uiEvent === 'minimize_view' && uiData\.view === 'document'/);
assert.doesNotMatch(chatStream, /uiEvent === 'open_view' && uiData\.view !==/);
