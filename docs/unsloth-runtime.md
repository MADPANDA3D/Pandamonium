# Unsloth Studio External Training Runtime

**Linear:** `MAD-796` (adapter) · `MAD-797` (datasets + observable cancellable jobs)

Pandamonium does not bundle a trainer. Training is an optional external runtime:
the operator runs [Unsloth Studio](https://unsloth.ai/docs/new/studio/start) on a
machine they own and points Pandamonium at its HTTP API. The adapter in
`src/unsloth_runtime.py` owns exactly four jobs — store the connection (with the
access token encrypted at rest), test it, discover what the runtime can do, and
fail closed with honest copy when it cannot. It never submits a training job.

## Versioned adapter contract

| Field | Value |
| --- | --- |
| Adapter contract id | `pandamonium-unsloth-runtime-v1` |
| Adapter version | `1` |
| Integration row | `integrations.type = "unsloth_runtime"`, `name = "primary"` |
| Storage owner | installation-level (`owner IS NULL`, admin-configured) |
| Credential | bearer token, Fernet-encrypted at rest via `src/secret_storage.py` |

Every discovery payload carries `contract` and `adapter_version` so a caller can
tell which adapter produced it. A future breaking change to the payload or the
probed route set must publish a new contract id rather than mutate `v1`.

## Supported runtime and API assumptions

The adapter targets the Studio HTTP surface documented as of 2026-09
(`https://unsloth.ai/docs/new/studio/start`). It reads only the routes below;
anything not listed here is out of contract.

| Method | Studio route | Use in this adapter |
| --- | --- | --- |
| `GET` | `/api/health` | liveness, version string (no auth) |
| `GET` | `/api/system` | GPU/CPU/memory resource summary (bearer auth) |
| `GET` | `/openapi.json` | capability discovery from the FastAPI schema |
| `GET` | `/api/train/status` | current job state (bearer auth) |
| `GET` | `/api/models/` | bounded model-identity sample (bearer auth) |

Assumptions and honest limits:

* The access token is a bearer JWT issued by Studio (`POST /api/auth/login`).
  The adapter does not perform the login flow and does not refresh tokens; an
  expired token reports `unauthorized` and the operator saves a fresh one.
* Studio's documented HTTP API has **no export/convert route** — export is
  CLI-only. Capability discovery therefore reports `conversion: unsupported`
  when the OpenAPI schema has no `/export`, `/convert`, or `/merge-model` path.
  A runtime that exposes its own conversion route is classified `supported`.
  If the schema is unavailable, conversion stays `unknown` (never guessed).
* Version strings are recorded (`runtime.version`) but not trusted for
  compatibility. Compatibility is decided by whether the endpoint exposes the
  Studio surface above; a mismatched endpoint reports `incompatible`.
* Studio binds `127.0.0.1:8888` by default. No host, port, GPU device, dataset
  path, or deployment-specific value is compiled into the adapter or its
  defaults — the operator always supplies the URL.

## Configuration, test, disable, remove

Admin Settings → **Training Runtime** (or the admin API):

| Route | Purpose |
| --- | --- |
| `GET /api/unsloth/connection` | redacted status (never returns the token) |
| `PUT /api/unsloth/connection` | save `base_url`, `token`, `enabled`, `model_endpoint_id` |
| `POST /api/unsloth/connection/test` | read-only health + capability discovery |
| `GET /api/unsloth/capabilities` | last recorded discovery, no dial |
| `DELETE /api/unsloth/connection` | remove the connection |

Notes:

* `PUT` is partial: omitted fields are unchanged; passing `token: ""` is
  rejected. Changing the URL or the token resets cached status and
  capabilities, so a new target never inherits old trust.
* `enabled: false` disables the runtime. Disabled runtimes fail closed: Test
  reports `disabled` and makes no HTTP request.
* `model_endpoint_id` optionally binds the runtime to an existing added model
  (`model_endpoints.id`). The binding is validated and shown back; it is an
  identity reference only.
* The token is write-only in the UI and is never logged or returned in any
  payload. Only `token_configured: true|false` is exposed.

## Capability discovery

`Test` performs, in order: `GET /api/health` (no token), `GET /api/system`,
`GET /openapi.json`, `GET /api/train/status`, `GET /api/models/`. Capability
states:

| State | Meaning |
| --- | --- |
| `supported` | the runtime advertises the function (OpenAPI path or read-only probe) |
| `unsupported` | the runtime does not expose it (e.g. conversion without an export route) |
| `unavailable` | advertised but currently blocked — carries `reason: busy` or `reason: insufficient_resources` |
| `unknown` | could not be verified (e.g. no OpenAPI schema); never guessed |

Training, conversion, and inference are reported separately, and every
`unsupported` name is listed in `unsupported[]`.

### No-accidental-training guarantee

Every request the adapter can make goes through one helper that is
structurally `GET`-only. `POST /api/train/start` is only ever *observed* in the
OpenAPI schema; it is never called. No route in `routes/unsloth_routes.py`
starts, stops, or resets a job, and `POST /api/unsloth/connection/test` is the
only POST route under `/api/unsloth`. Training job ownership is deferred to
MAD-797, which must add its own explicit, audited submission path.
`tests/test_unsloth_runtime.py` records every mock request and asserts the
method set is exactly `{"GET"}`, that `/api/train/start` is never called, and
that request bodies are empty.

## Error taxonomy

| State | Trigger | Copy intent |
| --- | --- | --- |
| `offline` | connect/timeout failure | check the address and that Studio is running |
| `unauthorized` | 401/403, or no token stored | issue a fresh token in Studio |
| `incompatible` | `/api/health` 404/non-JSON; no Studio surface reachable | point at a supported Studio build |
| `busy` | `GET /api/train/status` reports a running job | new training must wait |
| `insufficient_resources` | `/api/system` explicitly reports no usable accelerator | no trainable GPU/memory |
| `invalid_response` | unsafe redirect, oversized body, unreadable JSON | fail closed, no guessing |
| `error` | 5xx from a read-only route | the runtime reported an internal error |
| `disabled` / `unconfigured` / `untested` | local connection state | enable/configure/test |

`test_connection` persists the last state, reason, message, version, job state,
and capabilities on the integration row so the UI can render an honest status
without re-dialing.

## Limits and security

* Response bodies are capped at 512 KiB; requests time out at 8 s; redirects
  are refused so a bearer token cannot be bounced to another origin.
* HTTP(S) only; URLs cannot embed credentials, queries, or fragments.
  Loopback/LAN targets are allowed (the normal self-hosted case); link-local
  metadata addresses remain blocked by `src/url_safety.py`.
* Model samples are bounded to 10 identifiers of ≤200 chars.
* The adapter performs no filesystem access on the runtime host and knows
  nothing about datasets or checkpoint paths; no dataset or GPU job is
  authorized in this lane.

## Isolated operator-side verification procedure

**This lane ships fixture-proven adapter behavior only. No real GPU job is
authorized.** To verify against an operator-owned runtime, do this on an
isolated machine you own — never the shared CT103 service:

1. Install/start Studio per its docs (`unsloth studio -H 127.0.0.1 -p 8888`),
   create the Studio password, and log in to mint a bearer token.
2. From a shell with network access to the runtime, confirm the contract
   surface read-only:
   `curl -s http://127.0.0.1:8888/api/health` and
   `curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8888/api/system`.
3. In Pandamonium Settings → Training Runtime, save the URL and token, then
   press **Test** once. Confirm the chip reads `Online` and the capability
   chips match what Studio advertises (training/inference supported,
   conversion unsupported on the documented build).
4. Observe the runtime's own request log to confirm the test issued only
   `GET` requests and no training job appeared.
5. Optional negative checks: stop Studio → `Offline`; use a wrong token →
   `Token rejected`; start any Studio job → `Busy`.
6. Remove or disable the connection when finished. Rollback is
   `DELETE /api/unsloth/connection` or `enabled: false`; existing models,
   datasets, and Pandamonium functionality are untouched.

## Related

* `src/unsloth_runtime.py` — adapter.
* `routes/unsloth_routes.py` — admin routes.
* `static/js/unslothRuntime.js` — Settings tab.
* `tests/test_unsloth_runtime.py`, `tests/browser/mad-796-unsloth-runtime.spec.js`.