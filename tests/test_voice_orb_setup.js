const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

class FakeElement {
  constructor(tagName = 'div', id = '') {
    this.tagName = tagName.toUpperCase();
    this.id = id;
    this.children = [];
    this.dataset = {};
    this.listeners = new Map();
    this.parentNode = null;
    this.disabled = false;
    this.hidden = false;
    this.checked = false;
    this.open = false;
    this.type = '';
    this.value = '';
    this.htmlFor = '';
    this.className = '';
    this._text = '';
  }

  get textContent() {
    return `${this._text}${this.children.map(child => child.textContent).join('')}`;
  }

  set textContent(value) {
    this._text = String(value ?? '');
    this.children = [];
  }

  append(...children) {
    children.forEach(child => {
      child.parentNode = this;
      this.children.push(child);
    });
  }

  appendChild(child) {
    this.append(child);
    return child;
  }

  replaceChildren(...children) {
    this._text = '';
    this.children = [];
    this.append(...children);
  }

  addEventListener(type, handler) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(handler);
  }

  async dispatch(type) {
    for (const handler of this.listeners.get(type) || []) {
      await handler({ type, target: this, preventDefault() {} });
    }
  }
}

function descendants(node) {
  return [node, ...node.children.flatMap(descendants)];
}

function makeDocument() {
  const ids = [
    'voice-orb-setup',
    'voice-orb-setup-summary',
    'voice-orb-setup-text',
    'voice-orb-setup-model',
    'voice-orb-setup-stt',
    'voice-orb-setup-tts',
    'voice-orb-setup-workers-summary',
    'voice-orb-setup-workers',
    'voice-orb-tailnet-list',
    'voice-orb-tailnet-probe',
    'voice-orb-tailnet-status',
    'voice-orb-tailnet-peers',
    'voice-orb-tailnet-results',
  ];
  const nodes = new Map(ids.map(id => [id, new FakeElement('div', id)]));
  nodes.get('voice-orb-setup').tagName = 'DETAILS';
  nodes.get('voice-orb-tailnet-list').tagName = 'BUTTON';
  nodes.get('voice-orb-tailnet-probe').tagName = 'BUTTON';
  nodes.get('voice-orb-tailnet-probe').disabled = true;
  return {
    readyState: 'complete',
    createElement: tagName => new FakeElement(tagName),
    getElementById: id => nodes.get(id) || null,
    addEventListener() {},
    nodes,
  };
}

function response(data, ok = true) {
  return { ok, json: async () => data };
}

async function main() {
  const document = makeDocument();
  global.document = document;
  const peerId = 'a'.repeat(32);
  const calls = [];
  let failPeerList = false;
  global.fetch = async (url, options) => {
    calls.push([url, options]);
    if (url === '/api/discover?mode=tailnet_peers') {
      if (failPeerList) {
        return response({ detail: 'http://192.0.2.10 private failure' }, false);
      }
      return response({
        peers: [
          { id: peerId, os: 'linux', status: 'online', address: '192.0.2.10' },
          { id: 'not-opaque', os: 'https://private.test', status: 'online' },
        ],
        raw_url: 'https://private.test',
      });
    }
    if (url === `/api/discover?mode=tailnet_probe&peer_id=${peerId}`) {
      return response({
        candidates: [{
          peer_id: peerId,
          provider: 'ollama',
          models: ['safe-model:latest', 'https://private.test/model'],
          capabilities: ['model-list', 'https://private.test/capability'],
          url: 'http://192.0.2.10:11434',
          error: 'private failure',
        }],
      });
    }
    throw new Error(`Unexpected fetch: ${url}`);
  };

  const source = fs.readFileSync(
    path.join(__dirname, '..', 'static/js/voiceOrbSetup.js'),
    'utf8',
  );
  const setupModule = await import(
    `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`
  );

  assert.equal(calls.length, 0, 'Tailnet inspection is never automatic');

  const setup = {
    version: 1,
    core_ready: true,
    text: 'Core ready. <Credentials stay text.>',
    model: { configured: true, selection: 'default' },
    speech_to_text: { available: true, provider: 'local' },
    text_to_speech: { available: true, provider: 'browser', voice: 'System Voice' },
    workers: {
      items: [
        { id: 'pc-codex', configured: true, ready: true, capabilities: ['read_only', 'https://private.test'] },
        { id: 'hermes', configured: true, ready: true, capabilities: ['read_only'] },
        { id: 'discovered-agent', configured: true, ready: true, capabilities: ['write'] },
      ],
    },
  };
  setupModule.renderVoiceSetup(setup, { reveal: true });

  assert.equal(document.nodes.get('voice-orb-setup-text').textContent, setup.text);
  assert.equal(document.nodes.get('voice-orb-setup').open, true);
  assert.match(document.nodes.get('voice-orb-setup-workers-summary').textContent, /Fixed worker cluster/);
  assert.doesNotMatch(document.nodes.get('voice-orb-setup-workers').textContent, /discovered-agent|private\.test/);

  setupModule.renderVoiceSetup({
    ...setup,
    workers: { items: setup.workers.items.slice(0, 1) },
  });
  assert.doesNotMatch(document.nodes.get('voice-orb-setup-workers-summary').textContent, /cluster/i);

  await document.nodes.get('voice-orb-tailnet-list').dispatch('click');
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], '/api/discover?mode=tailnet_peers');
  assert.equal(calls[0][1].credentials, 'same-origin');
  const peerList = document.nodes.get('voice-orb-tailnet-peers');
  assert.equal(peerList.children.length, 1, 'invalid opaque peer IDs are not rendered');
  assert.match(peerList.textContent, new RegExp(peerId));
  assert.match(peerList.textContent, /linux.*online/);
  assert.doesNotMatch(peerList.textContent, /192\.0\.2|private\.test/);

  const checkbox = descendants(peerList).find(node => node.tagName === 'INPUT');
  checkbox.checked = true;
  await checkbox.dispatch('change');
  assert.equal(document.nodes.get('voice-orb-tailnet-probe').disabled, false);

  await document.nodes.get('voice-orb-tailnet-probe').dispatch('click');
  assert.equal(calls.length, 2);
  assert.equal(calls[1][0], `/api/discover?mode=tailnet_probe&peer_id=${peerId}`);
  assert.equal(calls[1][1].credentials, 'same-origin');
  const results = document.nodes.get('voice-orb-tailnet-results').textContent;
  assert.match(results, /ollama.*safe-model:latest/);
  assert.doesNotMatch(results, /192\.0\.2|private\.test|private failure|https?:\/\//);

  failPeerList = true;
  await document.nodes.get('voice-orb-tailnet-list').dispatch('click');
  const failure = document.nodes.get('voice-orb-tailnet-status').textContent;
  assert.equal(failure, 'Tailnet inspection could not be completed.');
  assert.doesNotMatch(failure, /192\.0\.2|private failure|https?:\/\//);
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
