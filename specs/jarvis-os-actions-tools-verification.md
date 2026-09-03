# Jarvis OS Actions, Tools, and Verification

**Protocol ID:** `JOS-P4`

**Version:** `0.2`

**Status:** Generic source implementation; live acceptance pending

**Runtime record:** [JOS-P4 runtime baseline](../docs/jos-p4-runtime.md)

**Execution owner:** Pandamonium

## Purpose

A model may propose an action. Only Pandamonium may turn that proposal into an
authorized execution and a verified result.

`JOS-P4` defines the lifecycle shared by built-in tools, MCP tools, UI controls,
workers, and extension-native actions. `JOS-P5` separately decides whether an
action is authorized.

## Action lifecycle

Every action follows this path:

1. **Discover** — Pandamonium builds the live capability catalog.
2. **Select** — `JOS-P2` exposes only relevant capabilities for this turn.
3. **Propose** — the engine emits an untrusted structured call.
4. **Normalize** — the adapter maps provider syntax to a canonical call.
5. **Validate** — Pandamonium checks name, schema, arguments, limits, and state.
6. **Authorize** — `JOS-P5` returns an allow, deny, or approval-required result.
7. **Execute** — the responsible native runner performs the bounded action.
8. **Correlate** — Pandamonium matches the result to the request and call.
9. **Verify** — explicit result fields or a verifier establish the outcome.
10. **Record** — events, result, timing, actor, and evidence enter canonical
    task/session history.
11. **Respond** — Jarvis explains only the outcome supported by that evidence.

No skipped stage is implied by fluent model text.

## Canonical action call

The logical `ActionCall` contains:

| Field | Meaning |
| --- | --- |
| `request_id` | Parent Jarvis request |
| `call_id` | Unique action correlation identifier |
| `agent_id` | Jarvis identity that proposed the call |
| `actor` | Engine, worker, extension, or deterministic lifecycle router |
| `capability_version` | Catalog/schema version used for validation |
| `name` | Canonical capability name |
| `arguments` | Validated bounded arguments |
| `target` | Tool, `extension:<extension_id>`, worker, or UI runner |
| `authority_ref` | `JOS-P5` decision or approval receipt |
| `limits` | Timeout, output, round, and resource bounds |

Backend-native tool syntax is adapter data and MUST NOT leak into policy or
canonical history.

## Canonical result

The logical `ActionResult` contains:

| Field | Meaning |
| --- | --- |
| `request_id`, `call_id` | Correlation |
| `status` | `succeeded`, `failed`, `denied`, `cancelled`, `timed_out`, or `unknown` |
| `summary` | Bounded operator/model-facing result |
| `structured` | Validated machine-readable payload when available |
| `evidence` | Native result, artifact locator, worker event, or verifier reference |
| `started_at`, `finished_at` | Execution timing |
| `retry_safe` | Whether the same request may be retried safely |
| `error` | Controlled failure category and useful detail |

Only `succeeded` supports an unqualified completion claim. `unknown` MUST NOT be
converted into success or retried automatically unless the responsible runner
proves the first execution did not start.

## Capability catalog

Pandamonium MUST compose the effective catalog from existing built-in schemas,
enabled MCP servers, active extension catalogs, and available worker actions.

- Every name is unique within the effective turn catalog.
- Unknown tools fail closed.
- Disabled tools remain absent at prompt and execution time.
- Newly discovered tools receive no authority by default.
- Dynamic extension tools disappear when the extension disengages.
- Tool descriptions guide selection but do not authorize execution.
- The catalog version or fingerprint is recorded with each call.

## Validation and execution

Validation MUST happen server-side even when the provider emitted a native
function call. It includes schema, type, size, path/URL confinement, owner,
active-state requirements, and protocol-specific allowlists.

Execution MUST be bounded by cancellation, timeout, output size, tool-round,
and concurrency policy. Long-running actions use durable task identifiers and
progress events rather than holding an unbounded model turn open.

For effectful operations, runners SHOULD accept an idempotency key or otherwise
declare whether a retry is safe.

## Verification

Verification uses the strongest available evidence in this order:

1. a native structured success/failure response from the responsible system;
2. a readback of the changed artifact or external state;
3. a worker/extension terminal event correlated to the action;
4. an independent verifier evaluating recorded actions and artifacts;
5. model text only as an explanation, never as proof.

An independent LLM verifier may find missing work, but it cannot turn a failed
or absent native result into success. Verification failures return to the same
bounded action loop or end visibly; they do not recurse without limit.

## Multi-action turns

Jarvis MAY compose multiple allowed actions. Pandamonium MUST preserve order and
dependencies, return each result before the next dependent decision, and stop
or compensate according to declared failure policy. Partial success is reported
as partial success with the successful and failed calls identified.

Extension actions use this same loop through `JOS-EXT-1`; they are not separate
command brains. ORACLE is the first reference adapter. Worker tasks also return
through the same evidence contract even when their internal execution is
asynchronous.

## Current implementation anchors

| Responsibility | Existing anchor |
| --- | --- |
| Built-in schemas and conversion | `src/tool_schemas.py` |
| Text/native tool normalization | `src/tool_parsing.py`, `src/llm_core.py` |
| Per-turn catalog selection | `src/tool_index.py`, `src/agent_loop.py` |
| Effective tool policy | `src/tool_policy.py`, `src/tool_security.py` |
| Dispatch and validation | `src/tool_execution.py` |
| Tool loop and result threading | `src/agent_loop.py` |
| Optional independent completion verifier | `src/agent_loop.py` |
| Worker task lifecycle and evidence | `src/jarvis_agent.py`, `src/agent_worker_adapters.py` |
| Reference-extension result correlation | `routes/voice_routes.py` |

The current runtime already enforces many stages, but result shapes and
correlation are heterogeneous across built-ins, MCP, UI, workers, and
extensions. The optional LLM verifier is useful secondary evidence, not yet a
uniform native verification contract.

## Compatibility gate

`JOS-P4` is satisfied only when these pass:

- native and text-only engines normalize to the same logical call;
- a disabled, unknown, malformed, oversized, or out-of-scope call fails closed;
- a successful read-only tool returns a correlated result to the same turn;
- a mutation is not claimed complete until native evidence/readback succeeds;
- timeout, cancellation, denial, failure, and unknown outcome remain distinct;
- retry-safe and non-retry-safe failures behave differently;
- a multi-action ORACLE request preserves dependencies and partial failures;
- a worker terminal result retains task, worker, workspace, and evidence;
- tool output containing prompt injection remains untrusted data;
- the tool-round and verifier-round limits prevent runaway loops.

## Definition of success

`JOS-P4` succeeds when every Jarvis action can be traced from one visible
capability through proposal, validation, authorization, execution, correlated
evidence, and truthful final wording independent of the engine's tool syntax.

## Non-goals

- Defining who may approve an action; that belongs to `JOS-P5`.
- Building a second action runner beside existing native implementations.
- Giving every tool the same retry or compensation behavior.
- Replacing working tool schemas solely to match the logical contract.
