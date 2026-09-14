const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

async function loadModule() {
  const source = fs.readFileSync(
    path.join(__dirname, '..', 'static/js/voicePreview.js'),
    'utf8',
  );
  return import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
}

async function main() {
  const mod = await loadModule();

  // ── server TTS preview posts the same body the Settings preview posts ──
  const calls = [];
  global.fetch = async (url, options) => {
    calls.push({ url, options });
    return { ok: true, status: 200, blob: async () => ({ size: 4 }) };
  };
  const urls = [];
  global.URL.createObjectURL = () => 'blob:preview';
  global.URL.revokeObjectURL = (url) => urls.push(url);
  const played = [];
  global.Audio = class {
    constructor(url) { this.url = url; }
    play() {
      played.push(this.url);
      setTimeout(() => this.onended && this.onended(), 0);
      return Promise.resolve();
    }
    pause() {}
  };

  const preview = mod.startVoicePreview({
    provider: 'configured',
    model: 'model-a',
    voice: 'Friday',
    speed: 1.25,
    text: 'Hello there.',
  });
  await preview.done;
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/api/tts/synthesize');
  assert.equal(calls[0].options.method, 'POST');
  const body = JSON.parse(calls[0].options.body);
  assert.equal(body.text, 'Hello there.');
  assert.equal(body.format, 'audio');
  assert.equal(body.model, 'model-a');
  assert.equal(body.voice, 'Friday');
  assert.equal(body.speed, '1.25');
  assert.equal(body.use_cache, false);
  assert.deepEqual(played, ['blob:preview']);
  assert.deepEqual(urls, ['blob:preview']);

  // ── synthesis failure is plain copy with a next step, never a code ────
  global.fetch = async () => ({ ok: false, status: 500, blob: async () => ({}) });
  const failed = mod.startVoicePreview({ provider: 'configured', text: 'Hello.' });
  await assert.rejects(failed.done, (error) => {
    assert.match(error.message, /couldn't be generated/i);
    assert.match(error.message, /try again/i);
    assert.doesNotMatch(error.message, /500|detail|synthesis_failed/);
    return true;
  });

  // ── browser TTS uses speechSynthesis with the matching voice ──────────
  const spoken = [];
  global.window = {
    speechSynthesis: {
      getVoices: () => [{ name: 'Friday' }, { name: 'Other' }],
      speak: (utterance) => {
        spoken.push(utterance);
        setTimeout(() => utterance.onend && utterance.onend(), 0);
      },
      cancel: () => {},
    },
  };
  global.SpeechSynthesisUtterance = class {
    constructor(text) { this.text = text; }
  };
  const browserPreview = mod.startVoicePreview({
    provider: 'browser',
    voice: 'friday',
    speed: 1,
    text: 'Browser sample.',
  });
  await browserPreview.done;
  assert.equal(spoken.length, 1);
  assert.equal(spoken[0].text, 'Browser sample.');
  assert.equal(spoken[0].voice.name, 'Friday');

  // ── stop() cancels playback instead of leaking it ─────────────────────
  let cancelled = 0;
  global.window.speechSynthesis.cancel = () => { cancelled += 1; };
  global.window.speechSynthesis.speak = () => {};
  const stopped = mod.startVoicePreview({ provider: 'browser', text: 'Stop me.' });
  stopped.stop();
  assert.equal(cancelled, 1);
  stopped.stop();
}

main()
  .then(() => {
    console.log('voice preview helper: ok');
  })
  .catch(error => {
    console.error(error);
    process.exitCode = 1;
  });
