# Voice Orb workers

Voice Orb workers are optional and disabled by default. The private compatibility layer supports the `pc-codex`, `hermes`, and `vps-codex` adapter slots, but public discovery exposes only installations that are explicitly configured and currently reachable. An unconfigured or unreachable VPS Codex slot is absent. Every task is read-only; there is no public write approval mode.

## Security contract

- Worker routes require an interactive Pandamonium login. Bearer API tokens are rejected.
- The linked chat owner owns the task and every status, event, reply, approval, steer, and cancel operation.
- Caller-supplied `approved=true` and every permission mode except `read_only` are rejected by the route, broker, adapter, and bundled Codex bridge.
- Read-only approval requests can only be denied.
- Status responses contain logical workspace names and capability labels, never endpoint URLs, token values, token paths, filesystem paths, or network addresses.
- Task prompts are forwarded in memory and are not stored in the broker or bridge state files.
- The first terminal event wins. Stable event IDs deduplicate stream replay; interrupted streams reconnect twice from the last remote event ID and then use bounded status reconciliation.

## Bundled Codex bridge

The bridge requires a token file and an explicit JSON map from logical workspace names to existing directories. It refuses wildcard bind addresses and starts Codex app-server with `sandbox=read-only` and `approvalPolicy=never`.

From a repository checkout:

```bash
export PANDAMONIUM_CODEX_WORKER_ID=pc-codex
export PANDAMONIUM_CODEX_BRIDGE_HOST=127.0.0.1
export PANDAMONIUM_CODEX_BRIDGE_TOKEN_FILE=/run/secrets/odysseus_pc_codex_token
export PANDAMONIUM_CODEX_WORKSPACES_JSON='{"demo":"/srv/projects/demo"}'
PYTHONPATH=. python services/codex-bridge/pandamonium_codex_bridge.py
```

Mount the same token file into the Pandamonium process and set the matching `PANDAMONIUM_PC_CODEX_*` variables from `.env.example`. Use `PANDAMONIUM_CODEX_WORKER_ID=vps-codex` plus the VPS-prefixed Pandamonium settings for the optional remote bridge. Keep `vps-codex` disabled until its transport and workspace map are intentionally configured.

The bridge does not select or prewarm a model. `PANDAMONIUM_CODEX_MODEL` and `PANDAMONIUM_CODEX_REASONING_EFFORT` are optional overrides; otherwise Codex uses its current defaults.

## Hermes compatibility gate

Hermes is not considered ready merely because its health endpoint responds. `/v1/capabilities` must report:

```json
{
  "permission_profile": "read_only_enforced",
  "features": {
    "run_submission": true,
    "run_events_sse": true,
    "run_stop": true
  },
  "workspaces": ["demo"]
}
```

`features.read_only_enforced=true` is also accepted in place of the top-level profile. Without one of those explicit signals the adapter fails closed. Hermes event frames need SSE `id:` values; idless frames are ignored and terminal state is recovered through status reconciliation.

## Using the voice commands

With one approved workspace, say:

- “Ask PC Codex to inspect the failing tests.”
- “Ask Hermes to summarize the current project state.”
- “Cancel PC Codex.”

When a worker has multiple workspaces, name one: “Ask PC Codex in demo to inspect the failing tests.” Workspace names are matched only against the worker’s advertised/configured logical allowlist.

The activity rail labels each worker, streams progress, exposes task cancellation, and reconstructs the current chat’s tasks after a reload. Ending voice closes microphone/playback only; worker tasks continue.
