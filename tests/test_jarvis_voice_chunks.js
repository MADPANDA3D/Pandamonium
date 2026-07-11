const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '../static/js/jarvisVoice.js'), 'utf8');

assert.match(source, /turns\/\$\{encodeURIComponent\(turnId\)\}\/audio/);
assert.match(source, /consumePcmResponse/);
assert.match(source, /STREAM_EDGE_CROSSFADE_SECONDS = 0\.008/);
assert.match(source, /scheduler_underruns/);
assert.doesNotMatch(source, /flushLiveSpeech|takeLiveSpeechChunk|LIVE_SPEECH_MAX_CHARS/);
assert.doesNotMatch(source, /STREAM_INITIAL_BUFFER_SECONDS = 2\.4/);
assert.match(source, /jarvis-agent-chip/);
assert.match(source, /openWorkerArtifact/);
assert.match(source, /codex:\/\/threads\//);
assert.match(source, /approval_required/);
