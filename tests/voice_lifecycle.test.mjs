import assert from 'node:assert/strict';

class TestCustomEvent extends Event {
  constructor(type, options = {}) {
    super(type);
    this.detail = options.detail;
  }
}

globalThis.CustomEvent = TestCustomEvent;
globalThis.window = new EventTarget();
globalThis.fetch = async url => ({
  ok: true,
  async json() {
    return String(url).includes('/api/auth/settings')
      ? { tts_enabled: true }
      : { available: true, ready: true, provider: 'browser', speed: 1, voice: '' };
  },
});

const lifecycle = await import('../static/js/voiceLifecycle.js');

const received = [];
const unsubscribe = lifecycle.subscribeVoiceLifecycle(payload => received.push(payload));
let browserPayload = null;
window.addEventListener('odysseus:voice-lifecycle', event => {
  browserPayload = event.detail;
}, { once: true });

const payload = lifecycle.emitVoiceLifecycle('stream-complete', {
  source: 'chat',
  reason: 'completed',
  sessionId: 'session-123',
});

assert.deepEqual(payload, {
  version: 1,
  type: 'stream-complete',
  source: 'chat',
  reason: 'completed',
  sessionId: 'session-123',
});
assert.equal(Object.isFrozen(payload), true);
assert.equal(received[0], payload);
assert.equal(browserPayload, payload);
assert.equal(unsubscribe(), true);

assert.throws(
  () => lifecycle.emitVoiceLifecycle('arbitrary-script', { source: 'chat', reason: 'user' }),
  /Unknown voice lifecycle event/,
);
assert.throws(
  () => lifecycle.emitVoiceLifecycle('tts-idle', { source: 'tts', reason: 'completed', html: '<script>' }),
  /Unknown voice lifecycle field/,
);
assert.throws(
  () => lifecycle.emitVoiceLifecycle('stream-complete', {
    source: 'chat',
    reason: 'completed',
    sessionId: 'x'.repeat(129),
  }),
  /at most 128/,
);

assert.deepEqual(lifecycle.listVoiceStaticModules(), ['recorder', 'tts']);
await assert.rejects(lifecycle.loadVoiceStaticModule('https://example.invalid/a.js'), /Unknown app-owned voice module/);

globalThis.document = {
  createElement() {
    return { innerHTML: '', querySelectorAll() { return []; }, textContent: '' };
  },
  querySelector() { return null; },
  getElementById(id) {
    return id === lifecycle.VOICE_SURFACE_ROOT_ID ? { id } : null;
  },
};
assert.equal(lifecycle.getVoiceSurfaceRoot().id, lifecycle.VOICE_SURFACE_ROOT_ID);

const coreEvents = [];
const stopCoreEvents = lifecycle.subscribeVoiceLifecycle(event => coreEvents.push(event));

window.isSecureContext = false;
const recorder = await lifecycle.loadVoiceStaticModule('recorder');
recorder.startRecording();
assert.equal(coreEvents.at(-1).type, 'capture-stopped');
assert.equal(coreEvents.at(-1).reason, 'unavailable');

class TestUtterance {
  constructor(text) {
    this.text = text;
    this.onend = null;
    this.onerror = null;
    this.rate = 1;
  }
}
globalThis.SpeechSynthesisUtterance = TestUtterance;
window.speechSynthesis = {
  cancel() {},
  getVoices() { return []; },
  speak(utterance) { queueMicrotask(() => utterance.onend()); },
};
const { AITTSManager } = await lifecycle.loadVoiceStaticModule('tts');
const manager = new AITTSManager();
manager.useBrowserTTS = true;
manager.available = true;
await manager._playBrowser('Lifecycle test');
assert.deepEqual(coreEvents.slice(-2).map(event => event.type), ['tts-started', 'tts-idle']);
assert.deepEqual(coreEvents.slice(-2).map(event => event.reason), ['started', 'completed']);
stopCoreEvents();

console.log('voice lifecycle contract: ok');
