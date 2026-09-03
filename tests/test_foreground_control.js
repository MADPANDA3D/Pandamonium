const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const read = file => fs.readFileSync(path.join(__dirname, '..', file), 'utf8');
const calendar = read('static/js/calendar.js');
const modals = read('static/js/modalManager.js');
const chatStream = read('static/js/chatStream.js');
const foregroundActions = read('static/js/foregroundActions.js');
const app = read('static/app.js');
const voice = read('static/js/jarvisVoice.js');

assert.match(calendar, /function getViewState\(\)[\s\S]*?open: _open && !minimized[\s\S]*?view: _view[\s\S]*?date: _ds\(_currentDate\)/);
assert.ok(calendar.indexOf("Modals.isMinimized('calendar-modal')") < calendar.indexOf('if (_open) return;'));
assert.match(modals, /_REPORTABLE_VIEWS = \{\s*'calendar-modal': 'calendar',\s*'doc-panel': 'document'/);
assert.match(modals, /return \{ active_view: activeView, minimized_views: minimizedViews \}/);
assert.match(chatStream, /export function collectClientState\(\)/);
assert.match(chatStream, /dispatchForegroundAction\(uiData\)/);
assert.doesNotMatch(chatStream, /uiEvent === 'open_view'/);
assert.doesNotMatch(chatStream, /uiEvent === 'close_view'/);
assert.doesNotMatch(chatStream, /uiEvent === 'minimize_view'/);
assert.match(foregroundActions, /export function dispatchForegroundAction\(message\)/);
assert.match(app, /registerForegroundAction\(FOREGROUND_ACTIONS\.OPEN_CALENDAR/);
assert.match(app, /registerForegroundAction\(FOREGROUND_ACTIONS\.CLOSE_DOCUMENT/);
assert.match(app, /registerForegroundAction\(FOREGROUND_ACTIONS\.MINIMIZE_DOCUMENT/);
assert.match(voice, /VOICE_UI_CONTROL_ALLOWLIST = new Set\(\[\s*'open_view:calendar',\s*'close_view:document',\s*'minimize_view:document'/);
assert.match(voice, /function voiceRequestPayload\(text\)[\s\S]*?extensionBridgeClientState\(\)[\s\S]*?client_state: clientState/);
assert.equal((voice.match(/JSON\.stringify\(voiceRequestPayload\(text\)\)/g) || []).length, 2);
assert.match(voice, /function applyVoiceUIControl\(event\)[\s\S]*?VOICE_UI_CONTROL_ALLOWLIST\.has\(viewControl\)[\s\S]*?handleUIControl\(event\)/);
