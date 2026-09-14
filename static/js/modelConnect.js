// static/js/modelConnect.js
// Shared model-endpoint detection and connection for the chat /setup flow and
// the first-run setup wizard. Network only — no DOM access, no app imports.

export const PROVIDER_PATTERNS = [
  { re: /^sk-ant-/,          name: 'Anthropic',  url: 'https://api.anthropic.com/v1' },
  { re: /^sk-or-/,           name: 'OpenRouter', url: 'https://openrouter.ai/api/v1' },
  { re: /^sk-proj-/,         name: 'OpenAI',     url: 'https://api.openai.com/v1' },
  { re: /^gsk_/,             name: 'Groq',       url: 'https://api.groq.com/openai/v1' },
  { re: /^AIza/,             name: 'Gemini',     url: 'https://generativelanguage.googleapis.com/v1beta/openai' },
  { re: /^xai-/,             name: 'xAI',        url: 'https://api.x.ai/v1' },
  { re: /^nvapi-/,           name: 'NVIDIA',     url: 'https://integrate.api.nvidia.com/v1' },
];

const PROVIDER_URLS = {
  deepseek: { name: 'DeepSeek', url: 'https://api.deepseek.com/v1' },
  openai: { name: 'OpenAI', url: 'https://api.openai.com/v1' },
  openrouter: { name: 'OpenRouter', url: 'https://openrouter.ai/api/v1' },
  ollama: { name: 'Ollama Cloud', url: 'https://ollama.com/api' },
  xai: { name: 'xAI', url: 'https://api.x.ai/v1' },
  anthropic: { name: 'Anthropic', url: 'https://api.anthropic.com/v1' },
  groq: { name: 'Groq', url: 'https://api.groq.com/openai/v1' },
  gemini: { name: 'Gemini', url: 'https://generativelanguage.googleapis.com/v1beta/openai' },
  google: { name: 'Gemini', url: 'https://generativelanguage.googleapis.com/v1beta/openai' },
  'opencode-zen': { name: 'OpenCode Zen', url: 'https://opencode.ai/zen/v1' },
  'opencode-go': { name: 'OpenCode Go', url: 'https://opencode.ai/zen/go/v1' },
  nvidia: { name: 'NVIDIA', url: 'https://integrate.api.nvidia.com/v1' },
};

export function detectProvider(input) {
  const trimmed = input.trim();
  // URL or bare IP/hostname — self-hosted endpoint
  // Matches: http://..., https://..., llm-host:8080, localhost:8000, myserver:8080/v1
  if (/^https?:\/\//i.test(trimmed) || /^(\d{1,3}\.){1,3}\d{1,3}(:\d+)?/i.test(trimmed) || /^(localhost|[\w.-]+:\d{2,5})/i.test(trimmed)) {
    let url = trimmed.replace(/\/+$/, '');
    if (!/^https?:\/\//i.test(url)) url = 'http://' + url;
    // Strip trailing path segments to get a clean base
    for (const suffix of ['/models', '/chat/completions', '/completions', '/v1/messages']) {
      if (url.endsWith(suffix)) url = url.slice(0, -suffix.length).replace(/\/+$/, '');
    }
    url = url.replace(/\/api\/(chat|tags|generate)\/?$/i, '/api');
    try {
      const parsed = new URL(url);
      if (parsed.hostname.endsWith('ollama.com')) url = 'https://ollama.com/api';
    } catch(e) {}
    // Add /v1 if bare host:port
    if (/^https?:\/\/[^/]+$/.test(url) && !url.includes('api.') && !url.includes('ollama.com')) url += '/v1';
    return { base_url: url, api_key: '', name: '' };
  }
  // Known key patterns
  for (const p of PROVIDER_PATTERNS) {
    if (p.re.test(input)) {
      return { base_url: p.url, api_key: input, name: p.name };
    }
  }
  // Generic sk- keys are ambiguous (OpenAI legacy, DeepSeek, and others).
  // Never guess a provider for a secret: asking avoids sending the key to
  // OpenRouter/OpenAI/etc. by mistake during setup probing.
  if (/^sk-[a-zA-Z0-9_\-]{20,}$/.test(input)) {
    return { ambiguous: true, api_key: input };
  }
  return null;
}

export function extractProviderCredential(input) {
  const raw = (input || '').trim();
  if (!raw) return null;
  const providerAliases = [
    ['deepseek ai', 'deepseek'], ['deepseek', 'deepseek'],
    ['open router', 'openrouter'], ['openrouter', 'openrouter'],
    ['ollama cloud', 'ollama'], ['ollama', 'ollama'],
    ['open ai', 'openai'], ['openai', 'openai'], ['chatgpt', 'openai'],
    ['anthropic', 'anthropic'], ['claude', 'anthropic'],
    ['groq', 'groq'],
    ['google', 'gemini'], ['gemini', 'gemini'],
    ['x ai', 'xai'], ['xai', 'xai'], ['grok', 'xai'],
    ['nvidia', 'nvidia'],
    ['opencode zen', 'opencode-zen'], ['opencode-zen', 'opencode-zen'],
    ['opencode go', 'opencode-go'], ['opencode-go', 'opencode-go'],
  ];
  for (const [alias, key] of providerAliases) {
    const re = new RegExp('(^|\\s|[,;:])(' + alias.replace(/\s+/g, '\\s+') + ')(?=$|\\s|[,;:])', 'i');
    const match = raw.match(re);
    if (!match) continue;
    const provider = PROVIDER_URLS[key];
    const credential = raw.replace(match[0], match[1] || '').replace(/^[\s,;:]+|[\s,;:]+$/g, '');
    return { provider, credential };
  }
  return null;
}

export function chatUrlForEndpoint(detected) {
  const base = (detected.base_url || '').replace(/\/+$/, '');
  if (detected.name === 'Anthropic') return base.replace(/\/v1$/, '') + '/v1/messages';
  if (base.includes('ollama.com')) return 'https://ollama.com/api/chat';
  return base + '/chat/completions';
}

export function isLocalBaseUrl(baseUrl) {
  return /^https?:\/\/(localhost|127\.0\.0\.1|0\.0\.0\.0|10\.|172\.(1[6-9]|2\d|3[01])\.|192\.168\.)/i.test(baseUrl || '');
}

export async function connectDetectedEndpoint(detected, options = {}) {
  const apiBase = options.apiBase || '';
  const fetchImpl = options.fetchImpl || fetch;
  const timeoutMs = Number.isFinite(options.timeoutMs) ? options.timeoutMs : 30000;

  const fd = new FormData();
  fd.append('base_url', detected.base_url);
  if (detected.api_key) fd.append('api_key', detected.api_key);
  if (detected.name) fd.append('name', detected.name);
  fd.append('require_models', options.requireModels === false ? 'false' : 'true');
  const explicitSkipProbe = options.skipProbe;
  const skipProbe = typeof explicitSkipProbe === 'boolean'
    ? explicitSkipProbe
    : !isLocalBaseUrl(detected.base_url);
  if (skipProbe) fd.append('skip_probe', 'true');
  else if (explicitSkipProbe === false) fd.append('skip_probe', 'false');
  if (options.endpointKind) fd.append('endpoint_kind', options.endpointKind);
  if (options.refreshMode) fd.append('model_refresh_mode', options.refreshMode);

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetchImpl(`${apiBase}/api/model-endpoints`, {
      method: 'POST',
      body: fd,
      credentials: 'same-origin',
      signal: controller.signal,
    });
    let data = {};
    try { data = await res.json(); } catch (_) { data = {}; }
    const models = Array.isArray(data.models) ? data.models : [];
    // A saved endpoint with discovered models is not "ready": run the explicit
    // safe test (one minimal completion) and report its redacted category.
    let validation = null;
    if (res.ok && data.id && models.length && options.validate !== false) {
      validation = await validateDetectedEndpoint(data.id, models[0], {
        apiBase,
        fetchImpl,
        timeoutMs: Number.isFinite(options.validateTimeoutMs) ? options.validateTimeoutMs : 20000,
      });
    }
    return {
      ok: Boolean(res.ok),
      saved: Boolean(res.ok),
      status: res.status,
      data,
      models,
      validation,
      failure: res.ok ? null : 'http_error',
    };
  } catch (error) {
    return {
      ok: false,
      saved: false,
      status: 0,
      data: {},
      models: [],
      validation: null,
      failure: error?.name === 'AbortError' ? 'timeout' : 'unreachable',
    };
  } finally {
    clearTimeout(timer);
  }
}

export async function validateDetectedEndpoint(endpointId, modelId, options = {}) {
  const apiBase = options.apiBase || '';
  const fetchImpl = options.fetchImpl || fetch;
  const timeoutMs = Number.isFinite(options.timeoutMs) ? options.timeoutMs : 20000;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetchImpl(`${apiBase}/api/probe-selected`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ models: [{ endpoint_id: endpointId, model: modelId }] }),
      signal: controller.signal,
    });
    let payload = {};
    try { payload = await res.json(); } catch (_) { payload = {}; }
    const result = Array.isArray(payload.results) ? payload.results[0] : null;
    if (!result || typeof result !== 'object') {
      return {
        status: 'fail',
        category: 'url_http',
        action: 'validate_settings',
        guidance: "We couldn't run the model test. Check the endpoint settings, then try again.",
      };
    }
    return result;
  } catch (error) {
    const timedOut = error?.name === 'AbortError';
    return {
      status: 'fail',
      category: timedOut ? 'timeout' : 'url_http',
      action: timedOut ? 'retry' : 'validate_settings',
      guidance: timedOut
        ? 'The model test timed out. Send it again; if it repeats, check the server load.'
        : "We couldn't run the model test. Check the connection and try again.",
    };
  } finally {
    clearTimeout(timer);
  }
}

export function connectResultMessage(result, label) {
  const providerLabel = label || 'the model engine';
  const count = result.models.length;
  if (result.ok && count > 0) {
    const validation = result.validation;
    if (validation && validation.status === 'ok') {
      return {
        level: 'success',
        message: `Found ${count} model${count === 1 ? '' : 's'} on ${providerLabel} and the model test passed — you're ready to chat.`,
      };
    }
    if (validation) {
      return {
        level: 'error',
        message: validation.guidance
          || `We saved ${providerLabel}, but the model test did not pass. Check the endpoint settings, then try again.`,
      };
    }
    return {
      level: 'error',
      message: `We saved ${providerLabel}, but the model test could not run. Open Settings → Models and run the model test.`,
    };
  }
  if (result.ok) {
    return {
      level: 'error',
      message: `The endpoint was saved, but ${providerLabel} didn't report any chat models yet. Check the server or load a model, then try again.`,
    };
  }
  if (result.failure === 'timeout') {
    return {
      level: 'error',
      message: `We couldn't reach ${providerLabel} in time. Make sure the server is running and this machine can reach it, then try again.`,
    };
  }
  if (result.failure === 'unreachable') {
    return {
      level: 'error',
      message: `We couldn't reach ${providerLabel}. Check the address and that the server is running, then try again.`,
    };
  }
  return {
    level: 'error',
    message: `We couldn't connect to ${providerLabel}. Check that the API key is correct and the account can list models, then try again.`,
  };
}
