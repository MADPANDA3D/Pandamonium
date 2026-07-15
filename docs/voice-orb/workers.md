# Read-only workers

Workers are optional and disabled by default. A clean installation with no workers retains normal Voice Orb conversation.

## Fixed adapters

| ID | Purpose | Alpha default |
|---|---|---|
| `pc-codex` | Codex bridge on an explicitly configured workstation | Disabled, read-only |
| `hermes` | Hermes task service that advertises enforced read-only capability | Disabled, fail-closed |
| `vps-codex` | Codex bridge on an explicitly configured server | Disabled, read-only |

The alpha does not load arbitrary Python modules or accept caller-selected adapters. Labels, workspace allowlists, and capabilities come from neutral configuration and the worker's bounded health response.

## Configuration

Each worker needs an explicit enable flag, a private endpoint, and a mounted token file. The supported variables are listed in `voice-orb.env.example`. Keep worker services on loopback, a private container network, or a private authenticated overlay; do not expose them directly to the public internet.

Token files should be readable only by the service account. With Docker, mount each token read-only and point the matching `*_TOKEN_FILE` variable to the in-container path. Never put token values in Compose YAML, command history, health responses, or issue reports.

## Read-only contract

- Requests with a permission mode other than `read_only` are rejected.
- Caller-supplied approval flags do not upgrade permission.
- `pc-codex` and `vps-codex` must invoke their bridge in a read-only sandbox/profile.
- Hermes remains unavailable unless its health/capability response proves that the service enforces read-only execution. A prompt telling Hermes not to write is not enforcement.
- Approval prompts in the public alpha may only be denied or cancelled. Workspace-write approval is intentionally deferred.

## Lifecycle

The broker records stable event IDs, accepts only the first terminal worker event, retries a broken event stream twice from the last event ID, and performs one bounded status reconciliation. End Voice stops microphone and playback but does not silently cancel an already delegated task. A user may cancel one worker while another continues.

Only an interactive Odysseus user session may invoke voice orchestration. Bearer API tokens cannot start or steer v0.1 voice workers, and task/session ownership is enforced inside broker helpers as well as routes.

## Health output

Normal status may expose only `configured`, `ready`, adapter ID, bounded capabilities, neutral workspace identifiers, and connection state. Endpoint URLs, IP addresses, token paths, token contents, and raw upstream errors must not be returned.
