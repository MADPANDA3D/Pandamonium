const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

class FakeEventTarget {
  constructor() { this.listeners = new Map(); }
  addEventListener(type, handler) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type).add(handler);
  }
  removeEventListener(type, handler) { this.listeners.get(type)?.delete(handler); }
  dispatch(type) {
    for (const handler of [...(this.listeners.get(type) || [])]) handler({ type });
  }
}

class FakeTrack extends FakeEventTarget {
  constructor() { super(); this.stopped = false; }
  stop() { this.stopped = true; }
}

class FakeStream {
  constructor(track = new FakeTrack()) { this.track = track; }
  getTracks() { return [this.track]; }
  getVideoTracks() { return [this.track]; }
}

class FakeVideo extends FakeEventTarget {
  constructor() {
    super();
    this.tagName = 'VIDEO';
    this.style = {};
    this.parentNode = null;
    this.attributes = new Map();
    this.videoWidth = 1920;
    this.videoHeight = 1080;
    this.playCount = 0;
  }
  setAttribute(name, value) { this.attributes.set(name, value); }
  removeAttribute(name) { this.attributes.delete(name); if (name === 'src') this.src = ''; }
  async play() { this.playCount += 1; }
  pause() {}
  load() {}
}

function makeEnvironment() {
  const page = new FakeEventTarget();
  page.location = new URL('https://odysseus.test/chat');
  const host = {
    id: 'jarvis-call-orb',
    dataset: {},
    children: [],
    get firstChild() { return this.children[0] || null; },
    insertBefore(child) {
      child.parentNode = this;
      this.children = [child, ...this.children.filter(item => item !== child)];
    },
  };
  const doc = new FakeEventTarget();
  doc.visibilityState = 'visible';
  doc.getElementById = id => {
    if (id === host.id) return host;
    return host.children.find(child => child.id === id) || null;
  };
  doc.createElement = tag => {
    if (tag === 'video') return new FakeVideo();
    if (tag === 'canvas') {
      return {
        width: 0,
        height: 0,
        getContext: () => ({ drawImage() {} }),
        toDataURL: () => `data:image/jpeg;base64,${Buffer.alloc(128).toString('base64')}`,
      };
    }
    throw new Error(`Unexpected element: ${tag}`);
  };
  const nav = { mediaDevices: {}, permissions: undefined };
  Object.defineProperty(globalThis, 'window', { value: page, configurable: true });
  Object.defineProperty(globalThis, 'document', { value: doc, configurable: true });
  Object.defineProperty(globalThis, 'navigator', { value: nav, configurable: true });
  return { page, doc, host, nav };
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

async function main() {
  const env = makeEnvironment();
  const source = fs.readFileSync(path.join(__dirname, '..', 'static/js/voiceOrbMedia.js'), 'utf8');
  const media = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

  assert.deepEqual(media.getState(), { mode: 'idle', cameraOpen: false, pending: false, clipId: null });

  const wait = deferred();
  const lateStream = new FakeStream();
  env.nav.mediaDevices.getUserMedia = () => wait.promise;
  const lateOpen = media.openCamera();
  assert.equal(media.getState().pending, true);
  media.stopMedia();
  wait.resolve(lateStream);
  assert.equal((await lateOpen).mode, 'idle');
  assert.equal(lateStream.track.stopped, true);

  let cameraCalls = 0;
  const retryStream = new FakeStream();
  env.nav.mediaDevices.getUserMedia = constraints => {
    cameraCalls += 1;
    if (cameraCalls === 1) {
      assert.deepEqual(constraints, {
        video: {
          width: { ideal: 1024 },
          height: { ideal: 576 },
          frameRate: { ideal: 30 },
        },
        audio: false,
      });
      const error = new Error('unsupported constraint');
      error.name = 'OverconstrainedError';
      return Promise.reject(error);
    }
    assert.deepEqual(constraints, { video: true, audio: false });
    return Promise.resolve(retryStream);
  };
  assert.equal((await media.openCamera()).cameraOpen, true);
  assert.equal(cameraCalls, 2);
  const frame = media.captureFrame();
  assert.equal(frame.mime, 'image/jpeg');
  assert.equal(frame.width, 1024);
  assert.equal(frame.height, 576);
  assert.ok(frame.data_base64.length > 0);

  env.page.dispatch('pagehide');
  assert.equal(retryStream.track.stopped, true);
  assert.equal(media.getState().mode, 'idle');

  const hiddenStream = new FakeStream();
  env.nav.mediaDevices.getUserMedia = async () => hiddenStream;
  await media.openCamera();
  env.doc.visibilityState = 'hidden';
  env.doc.dispatch('visibilitychange');
  assert.equal(hiddenStream.track.stopped, true);
  env.doc.visibilityState = 'visible';

  const endedStream = new FakeStream();
  env.nav.mediaDevices.getUserMedia = async () => endedStream;
  await media.openCamera();
  endedStream.track.dispatch('ended');
  assert.equal(endedStream.track.stopped, true);
  assert.equal(media.getState().mode, 'idle');

  const permission = new FakeEventTarget();
  permission.state = 'granted';
  env.nav.permissions = { query: async () => permission };
  const permissionStream = new FakeStream();
  env.nav.mediaDevices.getUserMedia = async () => permissionStream;
  await media.openCamera();
  await Promise.resolve();
  permission.state = 'denied';
  permission.dispatch('change');
  assert.equal(permissionStream.track.stopped, true);

  const checksum = `sha256:${'a'.repeat(64)}`;
  const bundledManifest = JSON.parse(
    fs.readFileSync(path.join(__dirname, '..', 'static/voice-orb-media.json'), 'utf8'),
  );
  assert.equal(bundledManifest.media[0].available, true);
  globalThis.fetch = async () => ({ ok: true, json: async () => bundledManifest });
  assert.equal((await media.playClip('motivational-abstract')).mode, 'clip');
  media.stopMedia();

  const lateManifest = deferred();
  globalThis.fetch = async () => lateManifest.promise;
  const lateClip = media.playClip('motivational-abstract');
  media.stopMedia();
  lateManifest.resolve({ ok: true, json: async () => bundledManifest });
  assert.equal((await lateClip).mode, 'idle');

  globalThis.fetch = async () => ({
    ok: true,
    json: async () => ({
      media: [{
        id: 'outside',
        title: 'Outside',
        type: 'video/webm',
        path: 'https://evil.test/outside.webm',
        tags: [],
        license: 'CC0-1.0',
        source: 'test',
        attribution: 'test',
        checksum,
        available: true,
      }],
    }),
  });
  await assert.rejects(media.playClip('outside'), /path is not allowed|same-origin/);
  await assert.rejects(media.playClip('not-listed'), /not allowlisted/);

  globalThis.fetch = async () => ({
    ok: true,
    json: async () => ({
      media: [{
        id: 'bad-type',
        title: 'Bad type',
        type: 'text/html',
        path: '/static/media/voice-orb/bad.html',
        tags: [],
        license: 'CC0-1.0',
        source: 'test',
        attribution: 'test',
        checksum,
        available: true,
      }],
    }),
  });
  await assert.rejects(media.playClip('bad-type'), /type is not allowed/);

  const clipCamera = new FakeStream();
  env.nav.permissions = undefined;
  env.nav.mediaDevices.getUserMedia = async () => clipCamera;
  await media.openCamera();
  globalThis.fetch = async () => ({
    ok: true,
    json: async () => ({
      media: [{
        id: 'motivational-abstract',
        title: 'Abstract',
        type: 'video/webm',
        path: '/static/media/voice-orb/motivational-abstract.webm',
        tags: ['motivational'],
        license: 'CC0-1.0',
        source: 'test',
        attribution: 'test',
        checksum,
        available: true,
      }],
    }),
  });
  const clipState = await media.playClip('motivational-abstract');
  assert.equal(clipCamera.track.stopped, true);
  assert.deepEqual(clipState, {
    mode: 'clip', cameraOpen: false, pending: false, clipId: 'motivational-abstract',
  });
  const videos = env.host.children.filter(child => child.tagName === 'VIDEO');
  assert.equal(videos.length, 1);
  media.stopMedia();
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
