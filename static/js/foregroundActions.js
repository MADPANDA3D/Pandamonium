// Strict browser-owned foreground action registry.
//
// Transport envelope:
//   { ui_event: 'foreground_action', version: 1, action: '<allowlisted id>', payload: {} }
//
// Action IDs include their only valid target. V1 payloads are therefore empty
// objects: callers cannot smuggle selectors, URLs, markup, or executable text
// through this seam.

export const FOREGROUND_ACTION_VERSION = 1;
export const MAX_FOREGROUND_ACTION_BYTES = 1024;
export const FOREGROUND_ACTIONS = Object.freeze({
  OPEN_CALENDAR: 'open_view:calendar',
  CLOSE_DOCUMENT: 'close_view:document',
  MINIMIZE_DOCUMENT: 'minimize_view:document',
});

const _allowedActions = new Set(Object.values(FOREGROUND_ACTIONS));
const _handlers = new Map();
const _envelopeKeys = new Set(['action', 'payload', 'version']);
const _messageKeys = new Set(['action', 'payload', 'ui_event', 'version']);

function _fail(code, message) {
  const error = new Error(message);
  error.name = 'ForegroundActionError';
  error.code = code;
  throw error;
}

function _isPlainObject(value) {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return false;
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

function _requireExactKeys(value, allowed, code) {
  for (const key of Object.keys(value)) {
    if (!allowed.has(key)) _fail(code, 'Foreground action fields do not match the contract');
  }
  for (const key of allowed) {
    if (!Object.prototype.hasOwnProperty.call(value, key)) {
      _fail(code, 'Foreground action fields do not match the contract');
    }
  }
}

function _serializedBytes(value) {
  let encoded;
  try {
    encoded = JSON.stringify(value);
  } catch (_) {
    _fail('invalid_envelope', 'Foreground action must be JSON serializable');
  }
  if (typeof encoded !== 'string') {
    _fail('invalid_envelope', 'Foreground action must be JSON serializable');
  }
  return new TextEncoder().encode(encoded).byteLength;
}

function _validateEnvelope(envelope) {
  if (!_isPlainObject(envelope)) {
    _fail('invalid_envelope', 'Foreground action envelope must be an object');
  }
  _requireExactKeys(envelope, _envelopeKeys, 'invalid_envelope');
  if (_serializedBytes(envelope) > MAX_FOREGROUND_ACTION_BYTES) {
    _fail('payload_too_large', 'Foreground action envelope is too large');
  }
  if (envelope.version !== FOREGROUND_ACTION_VERSION) {
    _fail('unsupported_version', 'Unsupported foreground action version');
  }
  if (typeof envelope.action !== 'string' || !_allowedActions.has(envelope.action)) {
    _fail('unknown_action', 'Unknown foreground action');
  }
  if (!_isPlainObject(envelope.payload) || Object.keys(envelope.payload).length !== 0) {
    _fail('invalid_payload', 'Foreground action payload must be an empty object');
  }
}

export function registerForegroundAction(action, handler) {
  if (typeof action !== 'string' || !_allowedActions.has(action)) {
    _fail('unknown_action', 'Cannot register an unknown foreground action');
  }
  if (typeof handler !== 'function') {
    _fail('invalid_handler', 'Foreground action handler must be a function');
  }
  if (_handlers.has(action)) {
    _fail('duplicate_action', `Foreground action already registered: ${action}`);
  }

  _handlers.set(action, handler);
  let active = true;
  return () => {
    if (!active) return false;
    active = false;
    if (_handlers.get(action) !== handler) return false;
    return _handlers.delete(action);
  };
}

export function invokeForegroundAction(envelope) {
  _validateEnvelope(envelope);
  const handler = _handlers.get(envelope.action);
  if (!handler) {
    _fail('unregistered_action', `Foreground action is not registered: ${envelope.action}`);
  }
  return handler(Object.freeze({}));
}

// Returns false for every legacy ui_control event so existing dispatch can
// continue. A message that declares itself as a foreground action is either
// invoked successfully or rejected; it never falls through to another path.
export function dispatchForegroundAction(message) {
  if (!_isPlainObject(message) || message.ui_event !== 'foreground_action') return false;
  _requireExactKeys(message, _messageKeys, 'invalid_envelope');
  if (_serializedBytes(message) > MAX_FOREGROUND_ACTION_BYTES) {
    _fail('payload_too_large', 'Foreground action message is too large');
  }
  invokeForegroundAction({
    version: message.version,
    action: message.action,
    payload: message.payload,
  });
  return true;
}
