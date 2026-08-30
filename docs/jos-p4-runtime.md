# JOS-P4 Runtime Baseline

Odysseus now records one canonical action lifecycle around its existing native
runners. The implementation does not add a second dispatcher.

## Runtime contract

- `src/action_protocol.py` composes the live catalog, produces its stable
  fingerprint, normalizes provider-native and text calls, applies bounded
  schema validation, and normalizes native results.
- `src/agent_loop.py` assigns one request ID per agent turn and one call ID per
  action. The call and result are persisted together in the existing tool-event
  history and surfaced on existing SSE diagnostics.
- Built-in, MCP, UI, worker, and engaged extension tools invoked through the
  agent loop pass through the same envelope. Each dynamic capability retains
  its actual `extension:<extension_id>` target. ORACLE continues to execute
  through its existing native executor as the reference adapter.
- Direct voice worker dispatch uses a bounded server-owned `start_agent_task`
  catalog entry and the same P4 validation envelope before calling the existing
  task runner. It does not add a second dispatcher.
- Unknown, conflicting, malformed, oversized, disabled, and policy-blocked
  actions fail closed before dispatch.
- Results retain `succeeded`, `failed`, `denied`, `cancelled`, `timed_out`, and
  `unknown` as distinct states. Unknown outcomes are never marked retry-safe.
- Tool output remains untrusted result data. It cannot supply authority or
  upgrade its own verification status.

Existing path confinement, owner checks, tool policy, MCP controls, worker task
durability, ORACLE correlation, tool-round bounds, and independent verifier
bounds remain the enforcement mechanisms behind the envelope.

## Verification

Focused coverage is in `tests/test_action_protocol.py` and
`tests/test_jarvis_mark6.py`. It checks native/text normalization, two distinct
extension IDs plus no-extension state, catalog engagement/disengagement and
conflicts, fail-closed validation, all result states, retry classification,
worker evidence, untrusted tool output, streamed/persisted correlation, direct
worker dispatch, and ordered ORACLE partial failure.
