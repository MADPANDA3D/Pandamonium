// Browser-owned camera and allowlisted clip controller for Odysseus Voice Orb.

const VIDEO_ID = 'odysseus-voice-orb-media';
const HOST_IDS = ['voice-orb-talk', 'voice-orb-panel'];
const MANIFEST_URL = '/static/voice-orb-media.json';
const VIDEO_EXTENSIONS = new Map([
  ['video/mp4', '.mp4'],
  ['video/webm', '.webm'],
  ['video/ogg', '.ogv'],
]);
const MEDIA_PATH = /^\/static\/media\/voice-orb\/([a-z0-9][a-z0-9-]{0,63})(\.[a-z0-9]+)$/;
const CAMERA_CONSTRAINTS = {
  video: {
    width: { ideal: 1024 },
    height: { ideal: 576 },
    frameRate: { ideal: 30 },
  },
  audio: false,
};
const MAX_FRAME_WIDTH = 1024;
const MAX_FRAME_HEIGHT = 576;
const MAX_FRAME_BYTES = 1024 * 1024;

let generation = 0;
let mode = 'idle';
let pending = false;
let stream = null;
let clipId = null;
let video = null;
let permissionStatus = null;
let permissionChangeHandler = null;

function getHost() {
  for (const id of HOST_IDS) {
    const host = document.getElementById(id);
    if (host) return host;
  }
  throw new Error('Voice orb media host is unavailable.');
}

function ensureVideo() {
  const host = getHost();
  video = document.getElementById(VIDEO_ID) || video;
  if (video && String(video.tagName).toLowerCase() !== 'video') {
    throw new Error('Voice orb media element is invalid.');
  }
  if (!video) {
    video = document.createElement('video');
    video.id = VIDEO_ID;
    video.className = 'odysseus-voice-orb-media';
    video.muted = true;
    video.playsInline = true;
    video.preload = 'metadata';
    video.setAttribute('aria-hidden', 'true');
    Object.assign(video.style, {
      position: 'absolute',
      inset: '0',
      zIndex: '0',
      width: '100%',
      height: '100%',
      borderRadius: '50%',
      objectFit: 'cover',
      background: 'transparent',
      display: 'none',
      pointerEvents: 'none',
    });
    video.addEventListener('ended', () => {
      if (mode === 'clip') stopMedia();
    });
  }
  const canvas = document.getElementById('voice-orb-canvas');
  if (canvas?.parentNode === host) {
    canvas.style.position = 'relative';
    canvas.style.zIndex = '1';
  }
  if (video.parentNode !== host) host.insertBefore(video, host.firstChild || null);
  return video;
}

function setHostMode(next) {
  for (const id of HOST_IDS) {
    const host = document.getElementById(id);
    if (!host) continue;
    host.dataset ||= {};
    host.dataset.voiceMedia = next;
    break;
  }
}

function detachPermissionWatcher() {
  if (permissionStatus && permissionChangeHandler) {
    permissionStatus.removeEventListener?.('change', permissionChangeHandler);
  }
  permissionStatus = null;
  permissionChangeHandler = null;
}

function stopTracks(mediaStream) {
  for (const track of mediaStream?.getTracks?.() || []) {
    try { track.stop(); } catch {}
  }
}

function clearCurrentMedia() {
  detachPermissionWatcher();
  stopTracks(stream);
  stream = null;
  pending = false;
  mode = 'idle';
  clipId = null;
  setHostMode('idle');
  if (!video) return;
  try { video.pause(); } catch {}
  video.loop = false;
  video.srcObject = null;
  video.removeAttribute('src');
  try { video.load(); } catch {}
  video.style.display = 'none';
}

function showVideo() {
  ensureVideo().style.display = 'block';
  setHostMode(mode);
}

async function watchCameraPermission(token) {
  if (!navigator.permissions?.query) return;
  try {
    const status = await navigator.permissions.query({ name: 'camera' });
    if (token !== generation || mode !== 'camera') return;
    permissionStatus = status;
    permissionChangeHandler = () => {
      if (status.state === 'denied' && token === generation) closeCamera();
    };
    status.addEventListener?.('change', permissionChangeHandler);
    permissionChangeHandler();
  } catch {}
}

async function requestCamera(token) {
  try {
    return await navigator.mediaDevices.getUserMedia(CAMERA_CONSTRAINTS);
  } catch (error) {
    if (token !== generation) return null;
    if (error?.name !== 'OverconstrainedError') throw error;
    return navigator.mediaDevices.getUserMedia({ video: true, audio: false });
  }
}

export async function openCamera() {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error('Camera access is unavailable in this browser.');
  }
  const target = ensureVideo();
  const token = ++generation;
  clearCurrentMedia();
  mode = 'camera';
  pending = true;
  setHostMode('camera-pending');

  let nextStream;
  try {
    nextStream = await requestCamera(token);
  } catch (error) {
    if (token === generation) clearCurrentMedia();
    throw error;
  }
  if (!nextStream || token !== generation) {
    stopTracks(nextStream);
    return getState();
  }

  stream = nextStream;
  pending = false;
  target.srcObject = stream;
  for (const track of stream.getVideoTracks?.() || stream.getTracks?.() || []) {
    track.addEventListener?.('ended', () => {
      if (token === generation && stream === nextStream) closeCamera();
    }, { once: true });
  }
  showVideo();
  try {
    await Promise.resolve(target.play());
  } catch (error) {
    if (token !== generation) return getState();
    clearCurrentMedia();
    throw error;
  }
  void watchCameraPermission(token);
  return getState();
}

export function closeCamera() {
  if (mode !== 'camera') return getState();
  return stopMedia();
}

function decodedBase64Size(value) {
  const padding = value.endsWith('==') ? 2 : value.endsWith('=') ? 1 : 0;
  return Math.floor((value.length * 3) / 4) - padding;
}

export function captureFrame() {
  if (mode !== 'camera' || !stream || !video) {
    throw new Error('Camera is not open.');
  }
  const sourceWidth = Number(video.videoWidth) || 0;
  const sourceHeight = Number(video.videoHeight) || 0;
  if (!sourceWidth || !sourceHeight) throw new Error('Camera frame is not ready.');

  const scale = Math.min(1, MAX_FRAME_WIDTH / sourceWidth, MAX_FRAME_HEIGHT / sourceHeight);
  const width = Math.max(1, Math.round(sourceWidth * scale));
  const height = Math.max(1, Math.round(sourceHeight * scale));
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext('2d', { alpha: false });
  if (!context) throw new Error('Camera frame capture is unavailable.');
  context.drawImage(video, 0, 0, width, height);

  for (const quality of [0.82, 0.62, 0.45]) {
    const dataUrl = canvas.toDataURL('image/jpeg', quality);
    const prefix = 'data:image/jpeg;base64,';
    if (!dataUrl.startsWith(prefix)) continue;
    const base64 = dataUrl.slice(prefix.length);
    if (base64 && decodedBase64Size(base64) <= MAX_FRAME_BYTES) {
      return { mime: 'image/jpeg', data_base64: base64, width, height };
    }
  }
  throw new RangeError('Camera frame exceeds the 1 MiB limit.');
}

function validateClip(entry) {
  if (entry?.available !== true) throw new Error('Requested media is not available.');
  const expectedExtension = VIDEO_EXTENSIONS.get(entry.type);
  if (!expectedExtension) throw new Error('Requested media type is not allowed.');
  if (typeof entry.path !== 'string') throw new Error('Requested media path is not allowed.');
  const pathMatch = entry.path.match(MEDIA_PATH);
  if (!pathMatch || pathMatch[1] !== entry.id || pathMatch[2] !== expectedExtension) {
    throw new Error('Requested media path is not allowed.');
  }
  const url = new URL(entry.path, window.location.href);
  if (
    url.origin !== window.location.origin
    || url.pathname !== entry.path
    || url.search
    || url.hash
  ) {
    throw new Error('Requested media must be a same-origin static asset.');
  }
  if (!/^sha256:[a-f0-9]{64}$/i.test(entry.checksum || '')) {
    throw new Error('Requested media checksum is invalid.');
  }
  for (const field of ['title', 'license', 'source', 'attribution']) {
    if (typeof entry[field] !== 'string' || !entry[field].trim()) {
      throw new Error(`Requested media ${field} is missing.`);
    }
  }
  if (!Array.isArray(entry.tags) || entry.tags.some(tag => typeof tag !== 'string')) {
    throw new Error('Requested media tags are invalid.');
  }
  return { id: entry.id, url: url.href };
}

async function getClip(id) {
  if (typeof id !== 'string' || !/^[a-z0-9][a-z0-9-]{0,63}$/.test(id)) {
    throw new Error('Requested media ID is invalid.');
  }
  const response = await fetch(MANIFEST_URL, {
    cache: 'no-store',
    credentials: 'same-origin',
    headers: { Accept: 'application/json' },
  });
  if (!response.ok) throw new Error('Voice orb media manifest is unavailable.');
  const manifest = await response.json();
  const entry = Array.isArray(manifest?.media)
    ? manifest.media.find(item => item?.id === id)
    : null;
  if (!entry) throw new Error('Requested media ID is not allowlisted.');
  return validateClip(entry);
}

export async function playClip(id) {
  const token = ++generation;
  clearCurrentMedia();
  const clip = await getClip(id);
  if (token !== generation) return getState();
  const target = ensureVideo();
  mode = 'clip';
  clipId = clip.id;
  target.src = clip.url;
  target.loop = true;
  showVideo();
  try {
    await Promise.resolve(target.play());
  } catch (error) {
    if (token !== generation) return getState();
    stopMedia();
    throw error;
  }
  return getState();
}

export function stopMedia() {
  generation += 1;
  clearCurrentMedia();
  return getState();
}

export function getState() {
  return {
    mode,
    cameraOpen: mode === 'camera' && Boolean(stream),
    pending,
    clipId,
  };
}

if (typeof window !== 'undefined') {
  window.addEventListener('pagehide', stopMedia);
}
if (typeof document !== 'undefined') {
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') stopMedia();
  });
}

const voiceOrbMedia = Object.freeze({
  openCamera,
  closeCamera,
  captureFrame,
  playClip,
  stopMedia,
  getState,
});

export default voiceOrbMedia;
