# Read-only workers

Workers are optional and disabled by default. A clean installation with no workers retains normal Voice Orb conversation.

## Fixed adapters

| ID | Purpose | Beta default |
|---|---|---|
| `pc-codex` | Codex bridge on an explicitly configured workstation | Disabled, read-only |
| `hermes` | Hermes task service that advertises enforced read-only capability | Disabled, fail-closed |
| `vps-codex` | Codex bridge on an explicitly configured server | Disabled, read-only |

The beta does not load arbitrary Python modules or accept caller-selected adapters. Labels, workspace allowlists, and capabilities come from neutral configuration and the worker's bounded health response.

## Configuration

Each worker needs an explicit enable flag, a private endpoint, and a mounted token file. The supported variables are listed in `voice-orb.env.example`. Keep worker services on loopback, a private container network, or a private authenticated overlay; do not expose them directly to the public internet.

Token files should be readable only by the service account. With Docker, mount each token read-only and point the matching `*_TOKEN_FILE` variable to the in-container path. Never put token values in Compose YAML, command history, health responses, or issue reports.

### Bundled Codex bridge

The bridge requires a token file and an explicit JSON map from logical workspace names to existing directories. It refuses wildcard bind addresses and starts Codex app-server with `sandbox=read-only` and `approvalPolicy=never`.

```bash
export PANDAMONIUM_CODEX_WORKER_ID=pc-codex
export PANDAMONIUM_CODEX_BRIDGE_HOST=127.0.0.1
export PANDAMONIUM_CODEX_BRIDGE_TOKEN_FILE=/run/secrets/odysseus_pc_codex_token
export PANDAMONIUM_CODEX_WORKSPACES_JSON='{"demo":"/srv/projects/demo"}'
PYTHONPATH=. python services/codex-bridge/pandamonium_codex_bridge.py
```

Mount the same token file into Pandamonium and set the matching `PANDAMONIUM_PC_CODEX_*` variables. Use `PANDAMONIUM_CODEX_WORKER_ID=vps-codex` with the VPS-prefixed settings for the optional remote bridge. The bridge uses Codex defaults unless `PANDAMONIUM_CODEX_MODEL` or `PANDAMONIUM_CODEX_REASONING_EFFORT` is explicitly set.

### Hermes compatibility gate

Hermes is ready only when `/v1/capabilities` reports run submission, SSE events, cancellation, workspaces, and an enforced read-only profile. A prompt asking Hermes not to write is not sufficient. Idless Hermes event frames are ignored; terminal state is recovered through bounded status reconciliation.

## Read-only contract

- Requests with a permission mode other than `read_only` are rejected.
- Caller-supplied approval flags do not upgrade permission.
- `pc-codex` and `vps-codex` must invoke their bridge in a read-only sandbox/profile.
- Hermes remains unavailable unless its health/capability response proves that the service enforces read-only execution. A prompt telling Hermes not to write is not enforcement.
- Approval prompts in the public beta may only be denied or cancelled. Workspace-write approval is intentionally deferred.

## Lifecycle

The broker records stable event IDs, accepts only the first terminal worker event, retries a broken event stream twice from the last event ID, and performs one bounded status reconciliation. End Voice stops microphone and playback but does not silently cancel an already delegated task. A user may cancel one worker while another continues.

Only an interactive Pandamonium user session may invoke voice orchestration. Bearer API tokens cannot start or steer Voice Orb workers, and task/session ownership is enforced inside broker helpers as well as routes.

## Health output

Normal status may expose only `configured`, `ready`, adapter ID, bounded capabilities, neutral workspace identifiers, and connection state. Endpoint URLs, IP addresses, token paths, token contents, and raw upstream errors must not be returned.

The Voice Orb setup summary is narrower: it includes only each fixed worker ID,
`configured`, `ready`, a normalized status, and at most 16 logical capability
names. It omits workspace names and connection reasons entirely.

## Voice commands

With one approved workspace, use exact commands such as “Ask PC Codex to inspect the failing tests,” “Ask Hermes to summarize the current project state,” or “Cancel PC Codex.” When a worker has several workspaces, name one: “Ask PC Codex in demo to inspect the failing tests.”

The activity rail attributes progress to each worker, exposes cancellation, and reconstructs the current chat after reload. Ending Voice closes microphone and playback only; worker tasks continue.
