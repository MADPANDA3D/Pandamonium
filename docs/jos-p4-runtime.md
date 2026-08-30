# JOS-P4 Runtime Baseline

Odysseus now records one canonical action lifecycle around its existing native
runners. The implementation does not add a second dispatcher.

## Runtime contract

- `src/action_protocol.py` composes the live catalog, produces its stable
  fingerprint, normalizes provider-native and text calls, applies bounded
  schema validation, and normalizes native results.
- `src/agent_loop.py` assigns one request ID per Jarvis turn and one call ID per
  action. The call and result are persisted together in the existing tool-event
  history and surfaced on existing SSE diagnostics.
- Built-in, MCP, UI, worker, and engaged extension tools pass through the same
  envelope. ORACLE continues to execute through its existing native executor.
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

Focused coverage is in `tests/test_action_protocol.py`. It checks native/text
normalization, catalog engagement/disengagement and conflicts, fail-closed
validation, all result states, retry classification, worker evidence, untrusted
tool output, streamed/persisted correlation, and ordered ORACLE partial failure.
