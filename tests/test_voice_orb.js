const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const read = file => fs.readFileSync(path.join(__dirname, '..', file), 'utf8');
const voice = read('static/js/jarvisVoice.js');
const media = read('static/js/voiceOrbMedia.js');
const html = read('static/index.html');
const sw = read('static/sw.js');

assert.match(voice, /VOICE_UI_CONTROL_ALLOWLIST = new Set\(\[\s*'open_view:calendar',\s*'close_view:document',\s*'minimize_view:document'/);
assert.match(voice, /VOICE_MEDIA_CONTROL_ALLOWLIST = new Set\(\[\s*'camera_open',\s*'camera_close',\s*'media_play:motivational-abstract'/);
assert.match(voice, /import voiceOrbMedia from '\.\/voiceOrbMedia\.js'/);
assert.match(voice, /function extensionBridgeClientState\(\)[\s\S]*?collectClientState\(\)/);
assert.match(voice, /function voiceRequestPayload\(text\)[\s\S]*?client_state: clientState/);
assert.match(voice, /mediaVoiceCommand\(text\) === 'camera_describe'[\s\S]*?voiceOrbMedia\.captureFrame\(\)/);
assert.equal((voice.match(/JSON\.stringify\(voiceRequestPayload\(text\)\)/g) || []).length, 2);
assert.match(voice, /'X-Tz-Offset': String\(-new Date\(\)\.getTimezoneOffset\(\)\)/);
assert.match(voice, /'X-Tz-Name': name/);
assert.match(voice, /function applyVoiceUIControl\(event\)[\s\S]*?VOICE_UI_CONTROL_ALLOWLIST\.has\(viewControl\)[\s\S]*?handleUIControl\(event\)/);
assert.match(voice, /navigator\.mediaDevices\.getUserMedia/);
assert.match(voice, /\/api\/stt\/transcribe/);
assert.match(voice, /\/api\/voice\/sessions\/\$\{encodeURIComponent\(turnSessionId\)\}\/respond\/stream/);
assert.match(voice, /voiceOrbMedia\.stopMedia\(\)/);
assert.doesNotMatch(voice, /eval\(|new Function/);

assert.match(html, /id="jarvis-call-panel"/);
assert.match(html, /id="jarvis-call-orb"/);
assert.match(html, /id="voice-worker-rail"/);
assert.match(html, /src="\/static\/js\/jarvisVoice\.js/);

assert.match(sw, /const CACHE_NAME = 'pandamonium-v372'/);
assert.match(sw, /'\/static\/js\/jarvisVoice\.js'/);
assert.match(sw, /'\/static\/js\/voiceOrbMedia\.js'/);
assert.match(sw, /'\/static\/voice-orb-media\.json'/);
assert.doesNotMatch(sw, /motivational-abstract\.webm/);

assert.match(media, /host\.insertBefore\(video, host\.firstChild \|\| null\)/);
assert.match(media, /const CAMERA_CONSTRAINTS = \{[\s\S]*width: \{ ideal: 1024 \}[\s\S]*height: \{ ideal: 576 \}[\s\S]*frameRate: \{ ideal: 30 \}[\s\S]*audio: false/);
