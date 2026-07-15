const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const read = file => fs.readFileSync(path.join(__dirname, '..', file), 'utf8');
const calendar = read('static/js/calendar.js');
const modals = read('static/js/modalManager.js');
const chatStream = read('static/js/chatStream.js');
const voice = read('static/js/jarvisVoice.js');

assert.match(calendar, /function getViewState\(\)[\s\S]*?open: _open && !minimized[\s\S]*?view: _view[\s\S]*?date: _ds\(_currentDate\)/);
assert.ok(calendar.indexOf("Modals.isMinimized('calendar-modal')") < calendar.indexOf('if (_open) return;'));
assert.match(modals, /_REPORTABLE_VIEWS = \{\s*'calendar-modal': 'calendar',\s*'doc-panel': 'document'/);
assert.match(modals, /return \{ active_view: activeView, minimized_views: minimizedViews \}/);
assert.match(chatStream, /export function collectClientState\(\)/);
assert.match(chatStream, /uiEvent === 'open_view' && uiData\.view === 'calendar'/);
assert.match(chatStream, /uiEvent === 'close_view' && uiData\.view === 'document'/);
assert.match(chatStream, /uiEvent === 'minimize_view' && uiData\.view === 'document'/);
assert.match(voice, /VOICE_UI_CONTROL_ALLOWLIST = new Set\(\[\s*'open_view:calendar',\s*'close_view:document',\s*'minimize_view:document'/);
assert.match(voice, /function voiceRequestPayload\(text\)[\s\S]*?client_state: collectClientState\(\)/);
assert.equal((voice.match(/JSON\.stringify\(voiceRequestPayload\(text\)\)/g) || []).length, 2);
assert.match(voice, /function applyVoiceUIControl\(event\)[\s\S]*?VOICE_UI_CONTROL_ALLOWLIST\.has\(viewControl\)[\s\S]*?handleUIControl\(event\)/);
