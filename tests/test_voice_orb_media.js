const assert = require('node:assert/strict');
const crypto = require('node:crypto');
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
  constructor() { super(); this.stopCount = 0; }
  stop() { this.stopCount += 1; }
  get stopped() { return this.stopCount > 0; }
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
    this.pauseCount = 0;
    this.loadCount = 0;
    this.playImpl = null;
    this.src = '';
  }
  setAttribute(name, value) { this.attributes.set(name, value); }
  removeAttribute(name) {
    this.attributes.delete(name);
    if (name === 'src') this.src = '';
  }
  async play() {
    this.playCount += 1;
    return this.playImpl?.();
  }
  pause() { this.pauseCount += 1; }
  load() { this.loadCount += 1; }
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function makeEnvironment() {
  const env = {
    frameBase64: Buffer.alloc(128).toString('base64'),
    framePrefix: 'data:image/jpeg;base64,',
    contextAvailable: true,
    captureCalls: [],
  };
  const page = new FakeEventTarget();
  page.location = new URL('https://odysseus.test/chat');
  const canvas = { id: 'voice-orb-canvas', tagName: 'CANVAS', style: {}, parentNode: null };
  const host = {
    id: 'voice-orb-talk',
    dataset: {},
    children: [canvas],
    get firstChild() { return this.children[0] || null; },
    insertBefore(child, before) {
      child.parentNode = this;
      this.children = this.children.filter(item => item !== child);
      const index = before ? this.children.indexOf(before) : -1;
      if (index < 0) this.children.push(child);
      else this.children.splice(index, 0, child);
    },
  };
  canvas.parentNode = host;
  const panel = { id: 'voice-orb-panel', dataset: {} };
  const doc = new FakeEventTarget();
  doc.visibilityState = 'visible';
  doc.getElementById = id => {
    if (id === host.id) return host;
    if (id === panel.id) return panel;
    return host.children.find(child => child.id === id) || null;
  };
  doc.createElement = tag => {
    if (tag === 'video') return new FakeVideo();
    if (tag === 'canvas') {
      return {
        width: 0,
        height: 0,
        getContext: () => env.contextAvailable ? {
          drawImage: (...args) => env.captureCalls.push(args),
        } : null,
        toDataURL: (_type, quality) => {
          env.captureCalls.push(quality);
          return `${env.framePrefix}${env.frameBase64}`;
        },
      };
    }
    throw new Error(`Unexpected element: ${tag}`);
  };
  const nav = { mediaDevices: {}, permissions: undefined };
  Object.defineProperty(globalThis, 'window', { value: page, configurable: true });
  Object.defineProperty(globalThis, 'document', { value: doc, configurable: true });
  Object.defineProperty(globalThis, 'navigator', { value: nav, configurable: true });
  Object.assign(env, { page, doc, host, panel, canvas, nav });
  return env;
}

const CHECKSUM = `sha256:${'a'.repeat(64)}`;

function mediaEntry(overrides = {}) {
  return {
    id: 'motivational-abstract',
    title: 'First-party silent abstract loop',
    type: 'video/webm',
    path: '/static/media/voice-orb/motivational-abstract.webm',
    tags: ['motivational', 'abstract', 'silent'],
    license: 'CC0-1.0',
    source: 'Odysseus Voice Orb contributors',
    attribution: 'Original first-party asset',
    checksum: CHECKSUM,
    available: true,
    ...overrides,
  };
}

function manifestResponse(entry = mediaEntry()) {
  return { ok: true, json: async () => ({ version: 1, media: [entry] }) };
}

async function main() {
  const env = makeEnvironment();
  const source = fs.readFileSync(path.join(__dirname, '..', 'static/js/voiceOrbMedia.js'), 'utf8');
  const media = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
  const idle = { mode: 'idle', cameraOpen: false, pending: false, clipId: null };

  assert.deepEqual(media.getState(), idle);
  assert.deepEqual(Object.keys(media.default).sort(), [
    'captureFrame', 'closeCamera', 'getState', 'openCamera', 'playClip', 'stopMedia',
  ]);
  assert.throws(() => media.captureFrame(), /Camera is not open/);
  await assert.rejects(media.openCamera(), /unavailable/);

  let deniedCalls = 0;
  env.nav.mediaDevices.getUserMedia = async () => {
    deniedCalls += 1;
    const error = new Error('permission denied');
    error.name = 'NotAllowedError';
    throw error;
  };
  await assert.rejects(media.openCamera(), /permission denied/);
  assert.equal(deniedCalls, 1, 'only an overconstraint error may retry');
  assert.deepEqual(media.getState(), idle);

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
  assert.deepEqual(await media.openCamera(), {
    mode: 'camera', cameraOpen: true, pending: false, clipId: null,
  });
  assert.equal(cameraCalls, 2);
  const videos = env.host.children.filter(child => child.tagName === 'VIDEO');
  assert.equal(videos.length, 1, 'the orb owns exactly one native video');
  const video = videos[0];
  assert.equal(env.host.children[0], video, 'video is inserted beneath the canvas');
  assert.equal(env.host.children[1], env.canvas);
  assert.equal(video.style.zIndex, '0');
  assert.equal(env.canvas.style.zIndex, '1');
  assert.equal(video.muted, true);
  assert.equal(video.playsInline, true);

  const frame = media.captureFrame();
  assert.equal(frame.mime, 'image/jpeg');
  assert.equal(frame.width, 1024);
  assert.equal(frame.height, 576);
  assert.ok(Buffer.from(frame.data_base64, 'base64').length <= 1024 * 1024);

  video.videoWidth = 0;
  assert.throws(() => media.captureFrame(), /not ready/);
  video.videoWidth = 1920;
  env.contextAvailable = false;
  assert.throws(() => media.captureFrame(), /unavailable/);
  env.contextAvailable = true;
  env.frameBase64 = Buffer.alloc(1024 * 1024 + 1).toString('base64');
  assert.throws(() => media.captureFrame(), /1 MiB/);
  env.frameBase64 = Buffer.alloc(128).toString('base64');
  env.framePrefix = 'data:image/png;base64,';
  assert.throws(() => media.captureFrame(), /1 MiB/);
  env.framePrefix = 'data:image/jpeg;base64,';

  assert.deepEqual(media.closeCamera(), idle);
  assert.equal(retryStream.track.stopped, true);
  assert.deepEqual(media.closeCamera(), idle);

  const wait = deferred();
  const lateStream = new FakeStream();
  env.nav.mediaDevices.getUserMedia = () => wait.promise;
  const lateOpen = media.openCamera();
  assert.equal(media.getState().pending, true);
  media.stopMedia();
  wait.resolve(lateStream);
  assert.deepEqual(await lateOpen, idle);
  assert.equal(lateStream.track.stopped, true);

  const firstWait = deferred();
  const secondWait = deferred();
  const firstStream = new FakeStream();
  const secondStream = new FakeStream();
  let concurrentCalls = 0;
  env.nav.mediaDevices.getUserMedia = () => (++concurrentCalls === 1 ? firstWait.promise : secondWait.promise);
  const firstOpen = media.openCamera();
  const secondOpen = media.openCamera();
  secondWait.resolve(secondStream);
  assert.equal((await secondOpen).cameraOpen, true);
  firstWait.resolve(firstStream);
  assert.equal((await firstOpen).cameraOpen, true, 'late request reports current state only');
  assert.equal(firstStream.track.stopped, true);
  assert.equal(secondStream.track.stopped, false);
  media.stopMedia();

  const playWait = deferred();
  const playRaceStream = new FakeStream();
  env.nav.mediaDevices.getUserMedia = async () => playRaceStream;
  video.playImpl = () => playWait.promise;
  const playRace = media.openCamera();
  await Promise.resolve();
  media.stopMedia();
  playWait.resolve();
  assert.deepEqual(await playRace, idle);
  assert.equal(playRaceStream.track.stopped, true);
  video.playImpl = null;

  const playFailureStream = new FakeStream();
  env.nav.mediaDevices.getUserMedia = async () => playFailureStream;
  video.playImpl = async () => { throw new Error('camera playback failed'); };
  await assert.rejects(media.openCamera(), /camera playback failed/);
  assert.equal(playFailureStream.track.stopped, true);
  assert.deepEqual(media.getState(), idle);
  video.playImpl = null;

  const pagehideStream = new FakeStream();
  env.nav.mediaDevices.getUserMedia = async () => pagehideStream;
  await media.openCamera();
  env.page.dispatch('pagehide');
  assert.equal(pagehideStream.track.stopped, true);
  assert.deepEqual(media.getState(), idle);

  const hiddenStream = new FakeStream();
  env.nav.mediaDevices.getUserMedia = async () => hiddenStream;
  await media.openCamera();
  env.doc.visibilityState = 'hidden';
  env.doc.dispatch('visibilitychange');
  assert.equal(hiddenStream.track.stopped, true);
  assert.deepEqual(media.getState(), idle);
  env.doc.visibilityState = 'visible';

  const endedStream = new FakeStream();
  env.nav.mediaDevices.getUserMedia = async () => endedStream;
  await media.openCamera();
  endedStream.track.dispatch('ended');
  assert.equal(endedStream.track.stopped, true);
  assert.deepEqual(media.getState(), idle);

  const permission = new FakeEventTarget();
  permission.state = 'granted';
  env.nav.permissions = { query: async request => {
    assert.deepEqual(request, { name: 'camera' });
    return permission;
  } };
  const permissionStream = new FakeStream();
  env.nav.mediaDevices.getUserMedia = async () => permissionStream;
  await media.openCamera();
  await Promise.resolve();
  permission.state = 'denied';
  permission.dispatch('change');
  assert.equal(permissionStream.track.stopped, true);
  assert.equal(permission.listeners.get('change')?.size || 0, 0);

  const permissionWait = deferred();
  const latePermission = new FakeEventTarget();
  latePermission.state = 'granted';
  env.nav.permissions = { query: () => permissionWait.promise };
  const latePermissionStream = new FakeStream();
  env.nav.mediaDevices.getUserMedia = async () => latePermissionStream;
  await media.openCamera();
  media.stopMedia();
  permissionWait.resolve(latePermission);
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(latePermission.listeners.get('change')?.size || 0, 0);
  assert.deepEqual(media.getState(), idle);
  env.nav.permissions = undefined;

  let fetchCalls = [];
  globalThis.fetch = async (...args) => {
    fetchCalls.push(args);
    return { ok: false, json: async () => ({}) };
  };
  await assert.rejects(media.playClip('motivational-abstract'), /manifest is unavailable/);
  await assert.rejects(media.playClip('https://evil.test/clip.webm'), /ID is invalid/);
  assert.equal(fetchCalls.length, 1, 'arbitrary URLs are rejected before fetch');
  assert.equal(fetchCalls[0][0], '/static/voice-orb-media.json');
  assert.deepEqual(fetchCalls[0][1], {
    cache: 'no-store',
    credentials: 'same-origin',
    headers: { Accept: 'application/json' },
  });

  globalThis.fetch = async () => ({ ok: true, json: async () => ({ media: [] }) });
  await assert.rejects(media.playClip('not-listed'), /not allowlisted/);

  for (const [overrides, message] of [
    [{ available: false }, /not available/],
    [{ type: 'text/html', path: '/static/media/voice-orb/motivational-abstract.html' }, /type is not allowed/],
    [{ path: 'https://evil.test/motivational-abstract.webm' }, /path is not allowed/],
    [{ path: '/static/media/voice-orb/%2e%2e/evil.webm' }, /path is not allowed/],
    [{ path: '/static/media/voice-orb/other.webm' }, /path is not allowed/],
    [{ path: '/static/media/voice-orb/motivational-abstract.mp4' }, /path is not allowed/],
    [{ checksum: 'sha256:bad' }, /checksum is invalid/],
    [{ source: '' }, /source is missing/],
    [{ attribution: '' }, /attribution is missing/],
    [{ license: '' }, /license is missing/],
    [{ tags: ['ok', 4] }, /tags are invalid/],
  ]) {
    globalThis.fetch = async () => manifestResponse(mediaEntry(overrides));
    await assert.rejects(media.playClip('motivational-abstract'), message);
    assert.deepEqual(media.getState(), idle);
  }

  const bundledManifest = JSON.parse(
    fs.readFileSync(path.join(__dirname, '..', 'static/voice-orb-media.json'), 'utf8'),
  );
  assert.equal(bundledManifest.media[0].available, true);
  assert.equal(bundledManifest.media[0].checksum,
    'sha256:b315386d0a3beee8d4ab70dc950fe9dedc332c857059a973aa855e573799f9e0');
  const asset = fs.readFileSync(path.join(
    __dirname, '..', 'static/media/voice-orb/motivational-abstract.webm',
  ));
  assert.equal(`sha256:${crypto.createHash('sha256').update(asset).digest('hex')}`,
    bundledManifest.media[0].checksum);
  assert.equal(bundledManifest.media[0].license, 'CC0-1.0');
  assert.ok(bundledManifest.media[0].tags.includes('silent'));
  globalThis.fetch = async () => ({ ok: true, json: async () => bundledManifest });

  const clipCamera = new FakeStream();
  env.nav.mediaDevices.getUserMedia = async () => clipCamera;
  await media.openCamera();
  const clipState = await media.playClip('motivational-abstract');
  assert.equal(clipCamera.track.stopped, true, 'clip mode stops camera tracks');
  assert.deepEqual(clipState, {
    mode: 'clip', cameraOpen: false, pending: false, clipId: 'motivational-abstract',
  });
  assert.equal(video.src, 'https://odysseus.test/static/media/voice-orb/motivational-abstract.webm');
  assert.equal(video.loop, true);
  assert.equal(video.style.display, 'block');
  assert.equal(env.host.children.filter(child => child.tagName === 'VIDEO').length, 1);
  video.dispatch('ended');
  assert.deepEqual(media.getState(), idle);
  assert.equal(video.src, '');
  assert.equal(video.style.display, 'none');

  await media.playClip('motivational-abstract');
  const cameraAfterClip = new FakeStream();
  env.nav.mediaDevices.getUserMedia = async () => cameraAfterClip;
  assert.equal((await media.openCamera()).cameraOpen, true, 'camera mode stops clip playback');
  assert.equal(video.src, '');
  media.stopMedia();

  const lateManifest = deferred();
  globalThis.fetch = async () => lateManifest.promise;
  const lateClip = media.playClip('motivational-abstract');
  media.stopMedia();
  lateManifest.resolve({ ok: true, json: async () => bundledManifest });
  assert.deepEqual(await lateClip, idle);

  globalThis.fetch = async () => ({ ok: true, json: async () => bundledManifest });
  video.playImpl = async () => { throw new Error('clip playback failed'); };
  await assert.rejects(media.playClip('motivational-abstract'), /clip playback failed/);
  assert.deepEqual(media.getState(), idle);
  assert.equal(video.src, '');
  video.playImpl = null;

  await media.playClip('motivational-abstract');
  env.page.dispatch('pagehide');
  assert.deepEqual(media.getState(), idle);
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
