const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const read = file => fs.readFileSync(path.join(__dirname, '..', file), 'utf8');
const voice = read('static/js/voiceOrb.js');
const html = read('static/index.html');
const sw = read('static/sw.js');

assert.match(voice, /UI_CONTROL_ALLOWLIST = new Set\(\[\s*'open_view:calendar',\s*'close_view:document',\s*'minimize_view:document'/);
assert.match(voice, /client_state: collectClientState\(\)/);
assert.match(voice, /handleUIControl\(event\)/);
assert.match(voice, /navigator\.mediaDevices\.getUserMedia/);
assert.match(voice, /\/api\/stt\/transcribe/);
assert.match(voice, /\/api\/tts\/synthesize/);
assert.match(voice, /createOscillator\(\)/);
assert.doesNotMatch(voice, /createElement\(['"]iframe['"]\)|postMessage\(|camera|media manifest|agent-workers/iu);
assert.match(html, /<canvas id="voice-orb-canvas"/);
assert.doesNotMatch(html, /organic-sphere/);
assert.match(sw, /const CACHE_NAME = 'odysseus-v345'/);
assert.match(sw, /'\/static\/js\/voiceOrb\.js'/);
