import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const releasedUpdater = readFileSync('tests/fixtures/releases/v1.0.21/updater.js', 'utf8');
const releasedWorker = readFileSync('tests/fixtures/releases/v1.0.20/sw.js', 'utf8');
const currentUpdater = readFileSync('static/js/updater.js', 'utf8');
const currentWorker = readFileSync('static/sw.js', 'utf8');

assert.equal(
  createHash('sha256').update(releasedUpdater).digest('hex'),
  '894dbf8c356b2274a5c9af249c74a535879610fabcf4a45401a3e16da4568782',
);
assert.equal(
  createHash('sha256').update(releasedWorker).digest('hex'),
  'd8eb76b8e6e038aa38d07416933f01a1a3b457b8dac1d1fd6e059e500d379f84',
);
assert.doesNotMatch(releasedUpdater, /registration\.update\(\)/);
assert.doesNotMatch(releasedWorker, /\/api\/update\/status|pandamonium-update-reconcile/);

class FakeEventTarget {
  constructor() {
    this.listeners = new Map();
  }

  addEventListener(name, listener) {
    const listeners = this.listeners.get(name) || new Set();
    listeners.add(listener);
    this.listeners.set(name, listeners);
  }

  removeEventListener(name, listener) {
    this.listeners.get(name)?.delete(listener);
  }

  dispatch(name) {
    for (const listener of this.listeners.get(name) || []) listener();
  }
}

async function exerciseWorkerRefresh({ discoverReplacement, stallUpdate = false }) {
  const previousWorker = { state: 'activated' };
  const replacement = Object.assign(new FakeEventTarget(), { state: 'installing' });
  const registration = Object.assign(new FakeEventTarget(), {
    active: previousWorker,
    installing: null,
    waiting: null,
    updateCalls: 0,
    async update() {
      this.updateCalls += 1;
      if (stallUpdate) return new Promise(() => {});
      if (!discoverReplacement) return;
      this.installing = replacement;
      this.dispatch('updatefound');
      await Promise.resolve();
      replacement.state = 'activated';
      this.active = replacement;
      replacement.dispatch('statechange');
      serviceWorker.dispatch('controllerchange');
    },
  });
  let registrationUrl = null;
  let replacedUrl = null;
  const serviceWorker = Object.assign(new FakeEventTarget(), {
    getRegistration: async url => {
      registrationUrl = url;
      return registration;
    },
  });
  const timers = new Map();
  let nextTimer = 1;
  let reloads = 0;
  const previousNavigator = Object.getOwnPropertyDescriptor(globalThis, 'navigator');
  const previousWindow = Object.getOwnPropertyDescriptor(globalThis, 'window');
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { serviceWorker } });
  Object.defineProperty(globalThis, 'window', { configurable: true, value: {
    location: {
      href: 'https://pandamonium.test/',
      reload: () => { reloads += 1; },
      replace: url => { replacedUrl = url; },
    },
    setTimeout: callback => {
      const id = nextTimer;
      nextTimer += 1;
      timers.set(id, callback);
      return id;
    },
    clearTimeout: id => timers.delete(id),
  } });
  try {
    const moduleSource = `${currentUpdater}\nexport { refreshApplicationWorker, needsWorkerRefresh, waitForWorkerReplacement, WORKER_UPDATE_TIMEOUT_MS, WORKER_ACTIVATION_TIMEOUT_MS };`;
    const updater = await import(`data:text/javascript;base64,${Buffer.from(moduleSource).toString('base64')}#${discoverReplacement}`);
    assert.ok(updater.WORKER_UPDATE_TIMEOUT_MS > 0, 'the worker update request must be bounded');
    assert.ok(
      updater.WORKER_ACTIVATION_TIMEOUT_MS >= ((8 * 5000) + (7 * 650) + 750),
      'the page must outwait the worker reconciliation budget',
    );
    const activatingWorker = Object.assign(new FakeEventTarget(), { state: 'activating' });
    const activatingRegistration = Object.assign(new FakeEventTarget(), {
      active: activatingWorker,
      installing: activatingWorker,
      waiting: null,
    });
    let activationSettled = false;
    const activation = updater.waitForWorkerReplacement(activatingRegistration, previousWorker);
    void activation.then(() => { activationSettled = true; });
    await Promise.resolve();
    assert.equal(activationSettled, false, 'an activating worker is not a completed replacement');
    activatingWorker.state = 'activated';
    activatingWorker.dispatch('statechange');
    assert.equal(await activation, true);
    timers.clear();
    const redundantWorker = Object.assign(new FakeEventTarget(), { state: 'installing' });
    const redundantRegistration = Object.assign(new FakeEventTarget(), {
      active: previousWorker,
      installing: redundantWorker,
      waiting: null,
    });
    const rejected = updater.waitForWorkerReplacement(redundantRegistration, previousWorker);
    redundantWorker.state = 'redundant';
    redundantWorker.dispatch('statechange');
    assert.equal(await rejected, false, 'a redundant replacement must fail without waiting');
    timers.clear();
    assert.equal(
      updater.needsWorkerRefresh('old', 'new', 'new', true, true),
      false,
      'a worker-marked navigation must not schedule a redundant fallback reload',
    );
    assert.equal(updater.needsWorkerRefresh('old', 'new', 'new', false, false), true);
    assert.equal(updater.needsWorkerRefresh(undefined, 'new', 'new', true, false), true);
    assert.equal(updater.needsWorkerRefresh('new', 'new', 'new', true, false), false);
    const refresh = updater.refreshApplicationWorker();
    await Promise.resolve();
    await Promise.resolve();
    if (stallUpdate) {
      assert.equal(timers.size, 1, 'a stalled worker update must have one timeout');
      const [timerId, timeout] = [...timers.entries()][0];
      timers.delete(timerId);
      timeout();
    }
    await refresh;
    if (!stallUpdate && !discoverReplacement) {
      assert.equal(timers.size, 0, 'an unchanged worker must not wait on activation');
    }
    return { registrationUrl, reloads, replacedUrl, updateCalls: registration.updateCalls };
  } finally {
    if (previousNavigator) Object.defineProperty(globalThis, 'navigator', previousNavigator);
    else delete globalThis.navigator;
    if (previousWindow) Object.defineProperty(globalThis, 'window', previousWindow);
    else delete globalThis.window;
  }
}

assert.deepEqual(
  await exerciseWorkerRefresh({ discoverReplacement: true }),
  { registrationUrl: 'https://pandamonium.test/static/', reloads: 0, replacedUrl: null, updateCalls: 1 },
);
assert.deepEqual(
  await exerciseWorkerRefresh({ discoverReplacement: false }),
  { registrationUrl: 'https://pandamonium.test/static/', reloads: 1, replacedUrl: null, updateCalls: 1 },
);
const stalledRefresh = await exerciseWorkerRefresh({ discoverReplacement: false, stallUpdate: true });
assert.equal(stalledRefresh.registrationUrl, 'https://pandamonium.test/static/');
assert.equal(stalledRefresh.reloads, 0);
assert.equal(stalledRefresh.updateCalls, 1);
assert.equal(
  new URL(stalledRefresh.replacedUrl).searchParams.get('pandamonium-update-reconcile'),
  'pending-worker-update',
);

async function activate(
  workerSource,
  statusResponses,
  {
    clientUrl = 'https://pandamonium.test/',
    holdNavigation = false,
    includeClosedClient = false,
    manualNavigationGrace = false,
  } = {},
) {
  const listeners = {};
  const deleted = [];
  const navigated = [];
  const messages = [];
  let activationPendingForNavigation = null;
  let announceNavigation = null;
  let releaseNavigation = null;
  const navigationStarted = new Promise(resolve => { announceNavigation = resolve; });
  const navigationGraceTimers = [];
  let claimed = 0;
  let statusRequests = 0;
  const responses = [...statusResponses];
  const context = {
    URL,
    Set,
    Promise,
    AbortController,
    console,
    clearTimeout: () => {},
    setTimeout: (callback, delay) => {
      if (manualNavigationGrace && delay === 750) {
        navigationGraceTimers.push(callback);
        return navigationGraceTimers.length;
      }
      callback();
      return 0;
    },
    caches: {
      keys: async () => ['pandamonium-v390'],
      delete: async key => {
        deleted.push(key);
        return true;
      },
      open: async () => ({ add: async () => {}, match: async () => null, put: async () => {} }),
      match: async () => null,
    },
    fetch: async (url, options = {}) => {
      if (url !== '/api/update/status') throw new Error(`Unexpected fetch: ${url}`);
      statusRequests += 1;
      const response = responses.shift();
      if (response instanceof Error) throw response;
      if (response.hang) {
        await new Promise((resolve, reject) => {
          const abort = () => reject(Object.assign(new Error('timed out'), { name: 'AbortError' }));
          if (options.signal?.aborted) abort();
          else options.signal?.addEventListener('abort', abort, { once: true });
        });
      }
      return {
        ok: response.status >= 200 && response.status < 300,
        status: response.status,
        redirected: Boolean(response.redirected),
        json: async () => {
          if (response.malformed) throw new SyntaxError('malformed JSON');
          return response.body;
        },
      };
    },
    self: {
      addEventListener: (name, handler) => { listeners[name] = handler; },
      skipWaiting: () => {},
      clients: {
        claim: async () => { claimed += 1; },
        matchAll: async () => [
          ...(includeClosedClient ? [{
            url: 'https://pandamonium.test/closed',
            navigate: async () => { throw new Error('client closed'); },
          }] : []),
          {
            url: clientUrl,
            postMessage: message => { messages.push(message); },
            navigate: async url => {
              navigated.push(url);
              announceNavigation();
              if (holdNavigation) {
                await new Promise(resolve => { releaseNavigation = resolve; });
              }
            },
          },
        ],
      },
    },
  };
  vm.runInNewContext(workerSource, context, { filename: 'static/sw.js' });
  let activation;
  listeners.activate({ waitUntil: promise => { activation = promise; } });
  let activationSettled = false;
  void activation.then(() => { activationSettled = true; });
  if (holdNavigation) {
    await navigationStarted;
    assert.ok(releaseNavigation, 'replacement worker did not start client navigation');
    activationPendingForNavigation = !activationSettled;
    if (manualNavigationGrace) {
      assert.equal(navigationGraceTimers.length, 1);
      navigationGraceTimers[0]();
      await activation;
    }
    releaseNavigation();
  }
  await activation;
  return { activationPendingForNavigation, claimed, deleted, messages, navigated, statusRequests };
}

const futureWorker = currentWorker.replace('pandamonium-v390', 'pandamonium-v391');
const recovered = await activate(futureWorker, [
  new Error('restart gap'),
  { status: 503 },
  { status: 200, body: { status: 'running' } },
  { status: 200, body: { status: 'succeeded' } },
], { includeClosedClient: true });
assert.equal(recovered.activationPendingForNavigation, null);
assert.equal(recovered.claimed, 1);
assert.deepEqual(recovered.deleted, ['pandamonium-v390']);
assert.equal(recovered.statusRequests, 4);
assert.equal(recovered.navigated.length, 1);
assert.equal(
  new URL(recovered.navigated[0]).searchParams.get('pandamonium-update-reconcile'),
  'pandamonium-v391',
);

const lateReplacement = await activate(
  futureWorker,
  [{ status: 200, body: { status: 'succeeded' } }],
  { clientUrl: stalledRefresh.replacedUrl },
);
assert.deepEqual(lateReplacement.navigated, []);
assert.equal(lateReplacement.messages.length, 1);
assert.equal(lateReplacement.messages[0].type, 'pandamonium-update-reconciled');

const boundedNavigation = await activate(
  futureWorker,
  [{ status: 200, body: { status: 'succeeded' } }],
  { holdNavigation: true, manualNavigationGrace: true },
);
assert.equal(boundedNavigation.activationPendingForNavigation, true);
assert.equal(boundedNavigation.navigated.length, 1);

const bounded = await activate(futureWorker, Array.from({ length: 8 }, () => new Error('offline')));
assert.equal(bounded.statusRequests, 8);
assert.deepEqual(bounded.navigated, []);

const timedOut = await activate(futureWorker, [
  { hang: true },
  { status: 200, body: { status: 'succeeded' } },
]);
assert.equal(timedOut.statusRequests, 2);
assert.equal(timedOut.navigated.length, 1);

const malformed = await activate(futureWorker, [
  { status: 200, malformed: true },
  { status: 200, body: { status: 'succeeded' } },
]);
assert.equal(malformed.statusRequests, 2);
assert.equal(malformed.navigated.length, 1);

for (const status of [401, 403]) {
  const authExpired = await activate(futureWorker, [{ status }]);
  assert.equal(authExpired.statusRequests, 1);
  assert.deepEqual(authExpired.navigated, []);
}

for (const status of [408, 425, 429]) {
  const retried = await activate(futureWorker, [
    { status },
    { status: 200, body: { status: 'succeeded' } },
  ]);
  assert.equal(retried.statusRequests, 2);
  assert.equal(retried.navigated.length, 1);
}

const nonRetryable = await activate(futureWorker, [{ status: 400 }]);
assert.equal(nonRetryable.statusRequests, 1);
assert.deepEqual(nonRetryable.navigated, []);

for (const terminal of ['failed', 'recovered', 'rolled_back']) {
  const result = await activate(futureWorker, [{ status: 200, body: { status: terminal } }]);
  assert.equal(result.statusRequests, 1);
  assert.equal(result.navigated.length, 1);
}

console.log('MAD-839 release bridge: PASS (v1.0.21 manual bridge; v1.0.24 future recovery bounded)');
