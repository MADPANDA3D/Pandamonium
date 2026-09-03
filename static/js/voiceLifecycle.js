// Core-owned lifecycle hooks for in-tree voice surfaces.
//
// This is deliberately not a plugin loader. The only loadable modules are
// fixed same-origin imports owned by the application source tree.

export const VOICE_LIFECYCLE_VERSION = 1;
export const VOICE_SURFACE_ROOT_ID = 'pandamonium-voice-surface-root';

const EVENT_TYPES = new Set([
  'capture-started',
  'capture-stopped',
  'stream-complete',
  'stream-interrupted',
  'tts-started',
  'tts-idle',
]);
const SOURCES = new Set(['chat', 'recorder', 'tts']);
const REASONS = new Set([
  'completed',
  'denied',
  'error',
  'navigation',
  'started',
  'stopped',
  'unavailable',
  'user',
]);
const DETAIL_FIELDS = new Set(['reason', 'sessionId', 'source']);
const MAX_SESSION_ID_LENGTH = 128;
const listeners = new Set();

// Keep the module list source-controlled and statically analyzable. A caller
// can select only one of these IDs; it cannot provide a URL or module path.
const STATIC_MODULE_LOADERS = Object.freeze({
  recorder: () => import('./voiceRecorder.js'),
  tts: () => import('./tts-ai.js'),
});

function boundedSessionId(value) {
  if (value === undefined) return undefined;
  if (typeof value !== 'string' || !value || value.length > MAX_SESSION_ID_LENGTH) {
    throw new TypeError('sessionId must be a non-empty string of at most 128 characters');
  }
  if (!/^[A-Za-z0-9._:-]+$/.test(value)) {
    throw new TypeError('sessionId contains unsupported characters');
  }
  return value;
}

function normalizeDetail(type, detail) {
  if (!EVENT_TYPES.has(type)) throw new TypeError(`Unknown voice lifecycle event: ${type}`);
  if (detail === null || typeof detail !== 'object' || Array.isArray(detail)) {
    throw new TypeError('Voice lifecycle detail must be an object');
  }
  for (const key of Object.keys(detail)) {
    if (!DETAIL_FIELDS.has(key)) throw new TypeError(`Unknown voice lifecycle field: ${key}`);
  }
  if (!SOURCES.has(detail.source)) throw new TypeError('Unknown voice lifecycle source');
  if (!REASONS.has(detail.reason)) throw new TypeError('Unknown voice lifecycle reason');

  const payload = {
    version: VOICE_LIFECYCLE_VERSION,
    type,
    source: detail.source,
    reason: detail.reason,
  };
  const sessionId = boundedSessionId(detail.sessionId);
  if (sessionId !== undefined) payload.sessionId = sessionId;
  return Object.freeze(payload);
}

export function emitVoiceLifecycle(type, detail) {
  const payload = normalizeDetail(type, detail);
  for (const listener of listeners) {
    try {
      listener(payload);
    } catch (error) {
      console.error('Voice lifecycle listener failed:', error);
    }
  }
  if (typeof window !== 'undefined'
      && typeof window.dispatchEvent === 'function'
      && typeof CustomEvent === 'function') {
    window.dispatchEvent(new CustomEvent('pandamonium:voice-lifecycle', { detail: payload }));
  }
  return payload;
}

export function subscribeVoiceLifecycle(listener) {
  if (typeof listener !== 'function') throw new TypeError('listener must be a function');
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function getVoiceSurfaceRoot() {
  if (typeof document === 'undefined') throw new Error('Voice surface root requires a document');
  const root = document.getElementById(VOICE_SURFACE_ROOT_ID);
  if (!root) throw new Error('App-owned voice surface root is unavailable');
  return root;
}

export function listVoiceStaticModules() {
  return Object.freeze(Object.keys(STATIC_MODULE_LOADERS));
}

export async function loadVoiceStaticModule(moduleId) {
  if (typeof moduleId !== 'string'
      || !Object.prototype.hasOwnProperty.call(STATIC_MODULE_LOADERS, moduleId)) {
    throw new TypeError('Unknown app-owned voice module');
  }
  return STATIC_MODULE_LOADERS[moduleId]();
}
