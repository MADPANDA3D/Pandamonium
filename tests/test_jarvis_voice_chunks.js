const assert = require('node:assert/strict');

global.window = { aiTTSManager: { extractPlainText: text => text }, addEventListener() {} };
global.document = {
  readyState: 'loading',
  addEventListener() {},
  getElementById() { return null; },
  querySelectorAll() { return []; },
};
global.navigator = {};

require('../static/js/jarvisVoice.js');

const response = 'It is good, Leo. All systems are stable; no alerts have surfaced since 08:19 this morning. Runtime telemetry remains normal across Project Nimbus and the local voice stack.\n\nI am holding off on new work—unless you ask me to route something to PC Codex, Hermes, or the VPS worker.\n\nI am ready for the next task whenever you are.';
const chunks = window.jarvisVoice._splitSpeechChunks(response);
assert.ok(chunks.length > 1);
assert.ok(chunks[0].length <= 180);
assert.ok(chunks.slice(1).every(chunk => chunk.length <= 220));
assert.equal(chunks.join('').replace(/\s+/g, ''), response.replace(/\s+/g, ''));
