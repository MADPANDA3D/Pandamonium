const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

function fakeResponse(data, ok = true, status = 200) {
  return { ok, status, json: async () => data };
}

async function loadModule() {
  const source = fs.readFileSync(
    path.join(__dirname, '..', 'static/js/modelConnect.js'),
    'utf8',
  );
  return import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
}

async function main() {
  const mod = await loadModule();

  // ── provider detection from a pasted key ──────────────────────────────
  assert.equal(mod.detectProvider('sk-or-v1-000000000000').name, 'OpenRouter');
  assert.equal(mod.detectProvider('sk-ant-api03-0000000000').name, 'Anthropic');
  assert.equal(mod.detectProvider('sk-proj-0000000000000000').name, 'OpenAI');
  assert.equal(mod.detectProvider('gsk_0000000000000000').name, 'Groq');
  assert.equal(mod.detectProvider('AIza0000000000000000').name, 'Gemini');
  assert.equal(mod.detectProvider('xai-0000000000000000').name, 'xAI');
  assert.equal(mod.detectProvider('nvapi-0000000000000000').name, 'NVIDIA');

  // ── provider detection from a pasted URL ──────────────────────────────
  const local = mod.detectProvider('http://localhost:11434');
  assert.equal(local.base_url, 'http://localhost:11434/v1');
  assert.equal(local.name, '');

  const bareHost = mod.detectProvider('192.168.1.50:8080');
  assert.equal(bareHost.base_url, 'http://192.168.1.50:8080/v1');

  const chatPath = mod.detectProvider('https://api.deepseek.com/v1/chat/completions');
  assert.equal(chatPath.base_url, 'https://api.deepseek.com/v1');

  const ollamaCloud = mod.detectProvider('https://ollama.com/api/chat');
  assert.equal(ollamaCloud.base_url, 'https://ollama.com/api');

  // Ambiguous generic keys are never guessed — that avoids leaking a key to
  // the wrong provider during setup probing.
  assert.equal(mod.detectProvider('sk-00000000000000000000').ambiguous, true);
  assert.equal(mod.detectProvider('not a key or url'), null);

  // Provider-prefixed credentials reuse the chat flow's pairing helper.
  const paired = mod.extractProviderCredential('deepseek sk-000000000000');
  assert.equal(paired.provider.name, 'DeepSeek');
  assert.equal(paired.credential, 'sk-000000000000');

  // ── chat URL derivation stays identical to the /setup flow ────────────
  assert.equal(
    mod.chatUrlForEndpoint({ base_url: 'https://api.anthropic.com/v1', name: 'Anthropic' }),
    'https://api.anthropic.com/v1/messages',
  );
  assert.equal(
    mod.chatUrlForEndpoint({ base_url: 'https://ollama.com/api', name: 'Ollama Cloud' }),
    'https://ollama.com/api/chat',
  );
  assert.equal(
    mod.chatUrlForEndpoint({ base_url: 'http://localhost:11434/v1' }),
    'http://localhost:11434/v1/chat/completions',
  );

  // ── local detection drives skip_probe exactly like the chat flow ──────
  assert.equal(mod.isLocalBaseUrl('http://localhost:11434/v1'), true);
  assert.equal(mod.isLocalBaseUrl('http://127.0.0.1:8000/v1'), true);
  assert.equal(mod.isLocalBaseUrl('http://192.168.1.10:8080/v1'), true);
  assert.equal(mod.isLocalBaseUrl('http://10.0.0.5:8000/v1'), true);
  assert.equal(mod.isLocalBaseUrl('http://172.16.0.9:8000/v1'), true);
  assert.equal(mod.isLocalBaseUrl('https://openrouter.ai/api/v1'), false);

  // ── success path posts the same form the endpoint-create route expects ─
  const calls = [];
  global.fetch = async (url, options) => {
    calls.push({ url, options });
    if (String(url).includes('/api/probe-selected')) {
      return fakeResponse({
        results: [{ status: 'ok', category: 'ok', action: 'none', guidance: '', model: 'model-a' }],
      });
    }
    return fakeResponse({ id: 'ep-1', models: ['model-a', 'model-b'] });
  };
  const success = await mod.connectDetectedEndpoint({
    base_url: 'https://openrouter.ai/api/v1',
    api_key: 'sk-or-v1-000000000000',
    name: 'OpenRouter',
  });
  assert.equal(success.ok, true);
  assert.equal(success.saved, true);
  assert.deepEqual(success.models, ['model-a', 'model-b']);
  assert.equal(success.validation.status, 'ok');
  assert.equal(calls.length, 2);
  assert.equal(calls[0].url, '/api/model-endpoints');
  assert.equal(calls[0].options.credentials, 'same-origin');
  const form = calls[0].options.body;
  assert.equal(form.get('base_url'), 'https://openrouter.ai/api/v1');
  assert.equal(form.get('api_key'), 'sk-or-v1-000000000000');
  assert.equal(form.get('name'), 'OpenRouter');
  assert.equal(form.get('require_models'), 'true');
  assert.equal(form.get('skip_probe'), 'true');
  assert.equal(calls[1].url, '/api/probe-selected');
  const probeBody = JSON.parse(calls[1].options.body);
  assert.equal(probeBody.models[0].endpoint_id, 'ep-1');
  assert.equal(probeBody.models[0].model, 'model-a');

  const localSuccess = await mod.connectDetectedEndpoint({
    base_url: 'http://localhost:11434/v1',
  });
  assert.equal(localSuccess.ok, true);
  assert.equal(calls[2].options.body.get('skip_probe'), null);
  assert.equal(calls[3].url, '/api/probe-selected');

  // ── the settings scan pattern registers local servers the same way ────
  const scanResult = await mod.connectDetectedEndpoint(
    { base_url: 'http://127.0.0.1:11434/v1', name: 'Ollama (127.0.0.1:11434)' },
    { requireModels: false, skipProbe: false, endpointKind: 'local', refreshMode: 'auto' },
  );
  assert.equal(scanResult.ok, true);
  const scanForm = calls[4].options.body;
  assert.equal(scanForm.get('require_models'), 'false');
  assert.equal(scanForm.get('skip_probe'), 'false');
  assert.equal(scanForm.get('endpoint_kind'), 'local');
  assert.equal(scanForm.get('model_refresh_mode'), 'auto');

  // ── success confirmation never overstates what was found ──────────────
  const successCopy = mod.connectResultMessage(success, 'OpenRouter');
  assert.equal(successCopy.level, 'success');
  assert.match(successCopy.message, /Found 2 models on OpenRouter/);
  assert.match(successCopy.message, /model test passed/);

  // ── a saved endpoint is not "ready" until the minimal completion passes ─
  global.fetch = async (url, options) => {
    if (String(url).includes('/api/probe-selected')) {
      return fakeResponse({
        results: [{
          status: 'fail',
          category: 'authentication',
          action: 'validate_settings',
          guidance: 'Validate settings — the endpoint rejected the credential.',
        }],
      });
    }
    return fakeResponse({ id: 'ep-3', models: ['model-a'] });
  };
  const unvalidated = await mod.connectDetectedEndpoint({
    base_url: 'https://openrouter.ai/api/v1',
    api_key: 'sk-or-v1-bad',
    name: 'OpenRouter',
  });
  assert.equal(unvalidated.ok, true);
  assert.equal(unvalidated.validation.category, 'authentication');
  const unvalidatedCopy = mod.connectResultMessage(unvalidated, 'OpenRouter');
  assert.equal(unvalidatedCopy.level, 'error');
  assert.match(unvalidatedCopy.message, /Validate settings/);
  assert.doesNotMatch(unvalidatedCopy.message, /ready to chat/);

  // ── HTTP failure copy is human, with next steps, no raw backend code ──
  global.fetch = async () => fakeResponse({ detail: 'provider_key_invalid' }, false, 400);
  const failed = await mod.connectDetectedEndpoint({
    base_url: 'https://openrouter.ai/api/v1',
    api_key: 'sk-or-v1-bad',
    name: 'OpenRouter',
  });
  assert.equal(failed.ok, false);
  assert.equal(failed.saved, false);
  assert.equal(failed.failure, 'http_error');
  const failureCopy = mod.connectResultMessage(failed, 'OpenRouter');
  assert.equal(failureCopy.level, 'error');
  assert.match(failureCopy.message, /We couldn't connect to OpenRouter/);
  assert.match(failureCopy.message, /try again/i);
  assert.doesNotMatch(failureCopy.message, /provider_key_invalid|400|detail/);

  // ── unreachable/timeout copy stays actionable ─────────────────────────
  global.fetch = async () => {
    const error = new Error('aborted');
    error.name = 'AbortError';
    throw error;
  };
  const timedOut = await mod.connectDetectedEndpoint({ base_url: 'http://10.0.0.9:8000/v1' });
  assert.equal(timedOut.failure, 'timeout');
  const timeoutCopy = mod.connectResultMessage(timedOut, 'the local server');
  assert.match(timeoutCopy.message, /couldn't reach/i);
  assert.match(timeoutCopy.message, /try again/i);

  // ── saved-but-empty is an error, never a success claim ────────────────
  global.fetch = async () => fakeResponse({ id: 'ep-2', models: [] });
  const empty = await mod.connectDetectedEndpoint({ base_url: 'http://localhost:11434/v1' });
  assert.equal(empty.ok, true);
  assert.deepEqual(empty.models, []);
  const emptyCopy = mod.connectResultMessage(empty, 'Ollama');
  assert.equal(emptyCopy.level, 'error');
  assert.match(emptyCopy.message, /any chat models/i);
  assert.match(emptyCopy.message, /try again/i);
  assert.doesNotMatch(emptyCopy.message, /Found|ready to chat/);
}

main()
  .then(() => {
    console.log('model connect helper: ok');
  })
  .catch(error => {
    console.error(error);
    process.exitCode = 1;
  });
