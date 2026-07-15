// static/js/clientState.js
/**
 * Bounded, read-only client state for in-tree consumers.
 *
 * Providers expose logical state only. The host owns the fixed schemas below,
 * drops unknown fields, and never serializes a provider object directly.
 * `open` means visibly open; minimized state is reported separately. The
 * foreground is resolved globally, so a scoped snapshot reports `chat` when
 * the actual foreground slice was omitted rather than promoting a background.
 */

export const CLIENT_STATE_VERSION = 1;
export const CLIENT_STATE_MAX_BYTES = 1024;
export const CLIENT_STATE_SLICES = Object.freeze(['calendar', 'document']);

const _providers = new Map();
const _activeOrder = [];

const _closed = {
  calendar: () => ({ version: CLIENT_STATE_VERSION, open: false, minimized: false, view: null, date: null }),
  document: () => ({ version: CLIENT_STATE_VERSION, open: false, minimized: false, id: null }),
};

function _sliceName(name) {
  if (typeof name !== 'string' || !CLIENT_STATE_SLICES.includes(name)) {
    throw new TypeError('Unknown client-state slice');
  }
  return name;
}

function _ownValue(record, key) {
  const descriptor = Object.getOwnPropertyDescriptor(record, key);
  if (!descriptor || !Object.prototype.hasOwnProperty.call(descriptor, 'value')) {
    throw new TypeError(`Invalid client-state field: ${key}`);
  }
  return descriptor.value;
}

function _record(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new TypeError('Client-state provider must return an object');
  }
  const prototype = Object.getPrototypeOf(value);
  if (prototype !== Object.prototype && prototype !== null) {
    throw new TypeError('Client-state provider must return a plain object');
  }
  if (_ownValue(value, 'version') !== CLIENT_STATE_VERSION) {
    throw new TypeError('Unsupported client-state version');
  }
  return value;
}

function _boolean(record, key) {
  const value = _ownValue(record, key);
  if (typeof value !== 'boolean') throw new TypeError(`Invalid client-state field: ${key}`);
  return value;
}

function _nullableString(record, key, maxLength, pattern) {
  const value = _ownValue(record, key);
  if (value === null) return null;
  if (typeof value !== 'string' || value.length < 1 || value.length > maxLength || !pattern.test(value)) {
    throw new TypeError(`Invalid client-state field: ${key}`);
  }
  return value;
}

function _calendarState(raw) {
  const record = _record(raw);
  const open = _boolean(record, 'open');
  const minimized = _boolean(record, 'minimized');
  if (open && minimized) throw new TypeError('Client state cannot be open and minimized');

  const view = _nullableString(record, 'view', 8, /^(?:month|week|year|agenda)$/);
  const date = _nullableString(record, 'date', 10, /^\d{4}-\d{2}-\d{2}$/);
  if (date) {
    const [year, month, day] = date.split('-').map(Number);
    const parsed = new Date(Date.UTC(year, month - 1, day));
    if (parsed.getUTCFullYear() !== year || parsed.getUTCMonth() !== month - 1 || parsed.getUTCDate() !== day) {
      throw new TypeError('Invalid client-state field: date');
    }
  }
  if (open && (!view || !date)) throw new TypeError('Open Calendar state requires view and date');
  return { version: CLIENT_STATE_VERSION, open, minimized, view, date };
}

function _documentState(raw) {
  const record = _record(raw);
  const open = _boolean(record, 'open');
  const minimized = _boolean(record, 'minimized');
  if (open && minimized) throw new TypeError('Client state cannot be open and minimized');
  const id = _nullableString(record, 'id', 128, /^[A-Za-z0-9_][A-Za-z0-9_.:-]{0,127}$/);
  return { version: CLIENT_STATE_VERSION, open, minimized, id };
}

const _sanitize = {
  calendar: _calendarState,
  document: _documentState,
};

export function registerClientStateProvider(name, provider) {
  name = _sliceName(name);
  if (typeof provider !== 'function') throw new TypeError('Client-state provider must be a function');
  if (_providers.has(name)) throw new Error(`Client-state provider already registered: ${name}`);
  _providers.set(name, provider);
  return () => {
    if (_providers.get(name) !== provider) return;
    _providers.delete(name);
    markClientStateView(name, false);
  };
}

export function markClientStateView(name, active = true) {
  name = _sliceName(name);
  if (typeof active !== 'boolean') throw new TypeError('Client-state active flag must be boolean');
  const index = _activeOrder.indexOf(name);
  if (index >= 0) _activeOrder.splice(index, 1);
  if (active) {
    if (!_providers.has(name)) throw new Error(`Client-state provider is not registered: ${name}`);
    _activeOrder.push(name);
  }
}

export function getClientStateSnapshot(slices = CLIENT_STATE_SLICES) {
  if (!Array.isArray(slices) || slices.length < 1 || slices.length > CLIENT_STATE_SLICES.length) {
    throw new TypeError('Client-state slices must be a non-empty bounded array');
  }
  const requested = slices.map(_sliceName);
  if (new Set(requested).size !== requested.length) throw new TypeError('Duplicate client-state slice');

  // Resolve every known slice once before applying request scope. Otherwise a
  // request that omits the foreground could incorrectly promote a visible but
  // background slice to `active_view`.
  const globalState = {};
  const globallyUnavailable = new Set();
  for (const name of CLIENT_STATE_SLICES) {
    try {
      const provider = _providers.get(name);
      if (!provider) throw new Error('provider unavailable');
      globalState[name] = _sanitize[name](provider());
    } catch (_) {
      globalState[name] = _closed[name]();
      globallyUnavailable.add(name);
    }
  }

  const foreground = [..._activeOrder].reverse()
    .find(name => globalState[name].open && !globalState[name].minimized);
  const activeView = foreground && requested.includes(foreground) ? foreground : 'chat';
  const state = Object.fromEntries(requested.map(name => [name, globalState[name]]));
  const unavailable = requested.filter(name => globallyUnavailable.has(name));
  const snapshot = {
    version: CLIENT_STATE_VERSION,
    active_view: activeView,
    slices: state,
    unavailable,
  };
  if (new TextEncoder().encode(JSON.stringify(snapshot)).byteLength > CLIENT_STATE_MAX_BYTES) {
    throw new RangeError('Client-state snapshot exceeds size limit');
  }
  return snapshot;
}
