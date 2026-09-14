# Provider Audit — Authentication, Discovery, Health, and Switching

> MAD-787. Every claim below was read from the live code on this branch
> (based on `origin/main` `83ca5f6a`) and pinned by the focused tests in
> `tests/test_provider_audit.py` and `tests/test_model_attribution.py`.

## 1. Supported providers and transports

| Family | Examples | Transport | Detection |
|---|---|---|---|
| Local OpenAI-compatible | vLLM, SGLang, LM Studio, llama.cpp, Cookbook-served models, FreeToken, APFEL | `http://host:port/v1/...` (Ollama also native `/api`) | `_classify_endpoint` → `local`; probes in `src/model_discovery.py`, `routes/model_routes.py` |
| Hosted API-key providers | OpenAI, Anthropic, OpenRouter, Groq, Gemini, xAI, NVIDIA, DeepSeek, Mistral, Moonshot/Kimi, Z.AI, Together, Perplexity, OpenCode Zen/Go, Cerebras | OpenAI-compatible `/chat/completions`, except Anthropic `/v1/messages` and Ollama `/api/chat` | `_detect_provider` + `build_chat_url` / `build_models_url` (`src/llm_core.py`, `src/endpoint_resolver.py`) |
| Subscription device-flow | GitHub Copilot (incl. GitHub Enterprise), ChatGPT Subscription (Codex backend) | Provider-specific signed-in APIs | `_is_copilot_base`, `is_chatgpt_subscription_base` |
| Node agents | Codex/Hermes bridges registered as endpoints | bridge protocol, not a model transport | `endpoint_kind == "agent"` (`agent_meta`, pairing token) |

`_detect_provider` is a **transport** classifier: unknown hosts deliberately
fall back to `"openai"` (OpenAI-compatible wire format). It never claims a
vendor name for an unknown host; user-facing identity comes from the endpoint
name and `providerLabel()`/`providerLogo()` in `static/js/providers.js`, which
match hostnames, not model strings.

## 2. Authentication

### Static API keys
- Stored on the canonical endpoint row: `ModelEndpoint.api_key`
  (`EncryptedText`, Fernet `enc:` at rest via `src/secret_storage.py`).
- Admin-only CRUD (`require_admin` on every `/api/model-endpoints` route).
- The browser never receives the key: list/create responses expose only
  `has_key` and `api_key_fingerprint` (SHA-256 prefix, 8 chars).

### Refresh-aware OAuth sessions
- `ProviderAuthSession` stores `access_token`/`refresh_token` as
  `EncryptedText`; the endpoint references it through `provider_auth_id`.
- ChatGPT Subscription: device auth + PKCE (`src/chatgpt_subscription.py`),
  refresh on use, 401/429 mapped to actionable reconnect/rate-limit messages
  via `to_http_exception`.
- GitHub Copilot: GitHub device flow (`routes/copilot_routes.py`); the
  provisioning call stores the access token as the endpoint's encrypted
  `api_key`; Copilot request headers are injected centrally
  (`src/copilot.py`, `build_headers`).

### Device-flow secret containment
- `PendingDeviceFlowStore` (`routes/device_flow.py`) is process-local; the
  `device_code` / `device_auth_id` lives only there.
- `/device/start` returns only public fields (`user_code`,
  `verification_uri[_complete]`, `poll_id`, `interval`, `expires_in`).
- `/device/poll` returns `pending` / `slow_down` / `failed` / `authorized`;
  the authorized payload is the provisioned endpoint summary
  (`id`, `name`, `base_url`, `models`) with no token.
- Poll sessions are single-use and expire (`expires_in`), and are throttled to
  the provider interval; `cancel` drops them.

### Failure handling and redaction
- Setup errors surface message + next step (`_model_endpoint_error_message`
  has Ollama/LM Studio-specific remediation) and never carry credentials.
- `_probe_single_model` and `_ping_endpoint` run provider error text through
  `_redact_api_key`, which removes the exact configured key and then applies
  the shared secret-pattern redactor (`src/authority_protocol.py`).
- Probe/refresh logs redact URLs (`core/log_safety.redact_url`) and keyed
  failures log a redacted message.

### Local endpoints are first-class
- Local endpoints need no credential and are the primary discovery target;
  discovery never attaches a key that was not explicitly supplied.
- Keyed probes fail closed: a 401/403 with a key returns no models instead of
  falling back to a curated vendor list (`_probe_endpoint`).

## 3. Discovery

All discovery is **review-before-add**: scans return candidates only. An
endpoint row is created only when the operator clicks Add, which calls
`POST /api/model-endpoints`.

| Path | Behavior |
|---|---|
| `GET /api/discover?mode=configured` (default) | Port scan of configured hosts + `LLM_HOSTS`/`OLLAMA_*`/`LM_STUDIO_URL` env hosts. Ports: 1919, 8000–8020, 8080, 1234, 11434, 11435. Returns host/port/url/models/provider fingerprint. |
| `GET /api/discover?mode=tailnet_peers` | Lists online tailnet peers as opaque HMAC IDs + OS + status. Addresses never leave the server; IDs expire after 120 s. |
| `GET /api/discover?mode=tailnet_probe&peer_id=...` | Probes up to 5 explicitly selected peers against 8000/8080/1234/11434. Responses keep the opaque ID and drop network identity. |
| `POST /api/model-endpoints` (`tailnet_peer_id`, `tailnet_port`) | Server-side `resolve_tailnet_candidate` re-resolves the selection into a base URL; the browser never holds the address. |
| Background refresh | Cached-first `/api/models`; per-endpoint refresh mode (`auto`/`manual`/`disabled`), TTL, backoff, and separate local/API timeouts. |

Safety properties pinned by tests:
- Model IDs are sanitized (`_public_model_ids` / `_openai_model_ids`): URLs,
  paths, and IP literals are rejected.
- Discovery never invents provider identity: `_fingerprint_provider` returns
  `lmstudio` or `llamacpp` only on their native API markers, otherwise `None`.
- The 30 s `/api/providers` cache still exists as a **legacy** surface fed by
  `ModelDiscovery.get_providers()`; see §8.

## 4. Health

| Route | Scope | Semantics |
|---|---|---|
| `GET /api/ping` | admin, all endpoints | Reachability + latency; a cached model list keeps a slow endpoint `online`. |
| `GET /api/model-endpoints/probe-local` | admin, local endpoints | Parallel 3.5 s reachability probe, 8 s server cache. Cloud endpoints are assumed up. |
| `GET /api/model-endpoints/{id}/probe` | admin | SSE per-model completion probe; updates `hidden_models`/`cached_models`; never-hidden providers (ChatGPT subscription) are skipped, not hidden. |
| `GET /api/probe` | admin | SSE probe of every endpoint. |
| `POST /api/model-endpoints/test` | admin | Resolve + probe without persisting. |
| Agent endpoints | admin | Bridge-specific health (`_agent_endpoint_health`) with cached redacted state. |

## 5. Model switching and message attribution

- Switching is `PATCH /api/session/{sid}` with `model` + `endpoint_url`
  (+ `endpoint_id`). The route re-reads the endpoint, rebuilds headers from its
  stored credential, and persists both to the session row. Raw endpoint URLs
  are rejected for non-admins.
- Attribution is per message: `save_assistant_response` stamps
  `requested_model` and the actual `model` into the assistant message
  metadata when the response is persisted. Later model switches only rewrite
  `Session.model`.
- The Stop handler previously backfilled `session.model` onto the last
  assistant message when it had no `model` key, rewriting history after a
  switch. It now writes only `stopped` (MAD-787 fix).
- The renderer may fall back to the session's current model for display when a
  message has no attribution, but that fallback is never persisted.

## 6. Provider-specific capabilities

Advertised only when the code can prove them:

- `supports_tools` is tri-state on the endpoint (`None` = unknown). Copilot
  sets it from the picker (`tool_calls`); subscription Codex explicitly `False`.
- `reasoning_levels(provider, model)` emits a map entry only for models with a
  known reasoning contract; the picker hides the selector otherwise.
- `model_type` is `llm` or `image`; only `llm` endpoints feed the chat picker.
- Speech endpoints are excluded from conversation model lanes
  (`_is_chat_capable` in setup status; non-chat filters in discovery).

## 7. Canonical model record

Local, LAN, tailnet, cloud, subscription, and (non-agent) node endpoints all
live in the single `ModelEndpoint` table. `category` (`local`/`tailnet`/`api`)
and `endpoint_kind` are **derived** from the URL/kind, not stored as separate
records, and `/api/models` projects every endpoint into the same item shape
(`endpoint_id`, `url`, `models`, `models_display`, `category`,
`endpoint_kind`, `model_type`, `reasoning_levels`). Owner scoping
(`owner`/NULL shared) and per-user caching apply uniformly to local and remote
endpoints.

## 8. Defects fixed under MAD-787

1. **Historical attribution rewrite** — `routes/history/history_routes.py`
   (`mark_stopped`) no longer writes `session.model` into stored message
   metadata. Guard: `tests/test_model_attribution.py`.
2. **Credential echo in setup errors** — `routes/model_routes.py`
   (`_probe_single_model`, `_ping_endpoint`, `_probe_endpoint` logging) now
   redact the configured key from provider-echoed error text. Guard:
   `tests/test_provider_audit.py`.

## 9. Known gaps (not fixed in this issue)

- `GET /api/providers` → `ModelDiscovery.get_providers()` still fabricates a
  fixed OpenAI model list when `OPENAI_API_KEY` is set, and reports local
  discovery as provider `"vllm"`. The only caller,
  `modelsModule.refreshProviders()`, no-ops when `#openai-model` is absent
  (it is absent from the current UI), so this is dead-but-reachable legacy.
  Recommend deleting the route and `get_providers()` in a follow-up.
- ChatGPT Subscription provisioning hides every discovered model except the
  first (`hidden_models = models[1:]`). Confirm this is the intended
  "one default Codex model" behavior before changing it.
- Keyed Anthropic probes return `[]` on any 4xx instead of the curated list
  (intentional fail-closed, but the setup copy could say so more precisely).
- Tailnet discovery only probes the four well-known targets; arbitrary
  operator ports still need manual URLs.
- Device-flow UI is covered by DOM-free helper tests and Python route tests;
  there is no browser spec that drives a mocked `/device/start|poll` round
  trip end to end.

## Verification

```bash
python -m pytest tests/test_provider_audit.py tests/test_model_attribution.py
python -m pytest tests/test_model_routes.py tests/test_copilot.py \
  tests/test_copilot_routes.py tests/test_chatgpt_subscription_routes.py \
  tests/test_device_flow_routes.py tests/test_safe_tailnet_discovery.py \
  tests/test_model_discovery_status.py tests/test_provider_classification.py
```
