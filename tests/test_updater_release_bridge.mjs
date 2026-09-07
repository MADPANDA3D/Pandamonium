import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const releasedUpdater = readFileSync('tests/fixtures/releases/v1.0.21/updater.js', 'utf8');
const releasedWorker = readFileSync('tests/fixtures/releases/v1.0.20/sw.js', 'utf8');
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

async function activate(workerSource, statusResponses) {
  const listeners = {};
  const deleted = [];
  const navigated = [];
  let claimed = 0;
  let statusRequests = 0;
  const responses = [...statusResponses];
  const context = {
    URL,
    Set,
    Promise,
    console,
    setTimeout: callback => {
      callback();
      return 0;
    },
    caches: {
      keys: async () => ['pandamonium-v388'],
      delete: async key => {
        deleted.push(key);
        return true;
      },
      open: async () => ({ add: async () => {}, match: async () => null, put: async () => {} }),
      match: async () => null,
    },
    fetch: async url => {
      if (url !== '/api/update/status') throw new Error(`Unexpected fetch: ${url}`);
      statusRequests += 1;
      const response = responses.shift();
      if (response instanceof Error) throw response;
      return {
        ok: response.status >= 200 && response.status < 300,
        status: response.status,
        redirected: Boolean(response.redirected),
        json: async () => response.body,
      };
    },
    self: {
      addEventListener: (name, handler) => { listeners[name] = handler; },
      skipWaiting: () => {},
      clients: {
        claim: async () => { claimed += 1; },
        matchAll: async () => [{
          url: 'https://pandamonium.test/',
          navigate: async url => { navigated.push(url); },
        }],
      },
    },
  };
  vm.runInNewContext(workerSource, context, { filename: 'static/sw.js' });
  let activation;
  listeners.activate({ waitUntil: promise => { activation = promise; } });
  await activation;
  return { claimed, deleted, navigated, statusRequests };
}

const futureWorker = currentWorker.replace('pandamonium-v388', 'pandamonium-v389');
const recovered = await activate(futureWorker, [
  new Error('restart gap'),
  { status: 503 },
  { status: 200, body: { status: 'running' } },
  { status: 200, body: { status: 'succeeded' } },
]);
assert.equal(recovered.claimed, 1);
assert.deepEqual(recovered.deleted, ['pandamonium-v388']);
assert.equal(recovered.statusRequests, 4);
assert.equal(recovered.navigated.length, 1);
assert.equal(
  new URL(recovered.navigated[0]).searchParams.get('pandamonium-update-reconcile'),
  'pandamonium-v389',
);

const bounded = await activate(futureWorker, Array.from({ length: 8 }, () => new Error('offline')));
assert.equal(bounded.statusRequests, 8);
assert.deepEqual(bounded.navigated, []);

for (const status of [401, 403]) {
  const authExpired = await activate(futureWorker, [{ status }]);
  assert.equal(authExpired.statusRequests, 1);
  assert.deepEqual(authExpired.navigated, []);
}

for (const terminal of ['failed', 'recovered', 'rolled_back']) {
  const result = await activate(futureWorker, [{ status: 200, body: { status: terminal } }]);
  assert.equal(result.statusRequests, 1);
  assert.equal(result.navigated.length, 1);
}

console.log('MAD-839 release bridge: PASS (v1.0.21 manual bridge; v1.0.22 future recovery bounded)');
