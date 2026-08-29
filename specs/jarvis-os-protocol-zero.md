# Jarvis OS Protocol Zero: Engine Compatibility

**Protocol ID:** `JOS-P0`

**Version:** `0.1`

**Status:** Baseline contract

**Control plane and operator surface:** Odysseus

## Purpose

Jarvis is the protocol-governed system. A language model is a replaceable
reasoning engine inside that system.

`JOS-P0` defines the boundary between Odysseus and a reasoning engine. A new
engine is Jarvis-compatible only when it can be installed without transferring
canonical identity, memory, permissions, tools, or session ownership into the
model.

In the supercar model:

- Odysseus is the cockpit, body, controls, and instrument panel.
- Jarvis protocols are the chassis, wiring harness, and engine mounts.
- The engine adapter is the model-specific fitment.
- GPT-OSS or another model is the replaceable engine.

## System ownership

### Odysseus MUST own

- the stable `Jarvis` agent identity and operator-facing name;
- authenticated operator, agent, and session identity;
- canonical conversation and task state;
- context construction, ordering, compaction, and token budgets;
- durable memory admission, retrieval, correction, provenance, and deletion;
- the visible tool catalog and effective per-turn tool policy;
- authorization, approval, execution, and verification of actions;
- agent and worker routing, including Jarvis, Gordon/Hermes, and Codex workers;
- audit events, usage records, failure state, and rollback selection;
- the GUI, voice surface, notifications, and operator controls.

### The engine adapter MUST own

- translation between the Jarvis turn contract and a backend's API format;
- model-specific chat templates and message normalization;
- capability reporting for context size, tools, vision, streaming, and limits;
- timeout, cancellation, retry-safe error normalization, and stream parsing;
- removal of backend-specific details from the rest of Jarvis.

### The reasoning engine MAY own

- inference and disposable inference caches;
- generation of text;
- generation of proposed structured tool calls;
- model-native reasoning behavior that does not become canonical Jarvis state.

The engine MUST NOT own operator authority, credentials, permanent memory,
canonical session history, or the decision that its own action is authorized or
successful.

## Non-negotiable invariants

1. **Identity survives replacement.** Changing the backend model MUST NOT rename
   Jarvis or replace his identity with the checkpoint's identity.
2. **Canonical state stays outside the engine.** An engine cache or internal
   conversation state is disposable. Odysseus remains able to reconstruct the
   next turn after an engine restart or replacement.
3. **Context is mounted, not surrendered.** Odysseus supplies the bounded
   context for each turn. The engine does not independently read Jarvis memory,
   files, credentials, or infrastructure.
4. **Tools are capabilities, not authority.** A model-generated tool call is an
   untrusted proposal. Odysseus validates the schema, policy, owner, permission,
   and approval before execution.
5. **Outcomes require evidence.** Model text cannot prove that work ran or
   succeeded. Odysseus records results only from the responsible tool, worker,
   or verifier.
6. **Failures preserve the chassis.** A timeout, malformed stream, unavailable
   engine, or failed tool call MUST fail visibly without corrupting canonical
   Jarvis state.
7. **Backend quirks stop at the adapter.** Provider URLs, message formats,
   reasoning fields, tool-call shapes, and chat templates MUST NOT leak into the
   identity, memory, authority, or UI protocols.
8. **Replacement is reversible.** The previous working engine configuration and
   its compatibility result remain available until the replacement has passed
   acceptance.

## Canonical turn contract

This is an internal logical contract, not a requirement to replace the working
OpenAI-compatible HTTP transport.

An engine receives a bounded `TurnRequest` containing:

| Field | Meaning |
| --- | --- |
| `protocol_version` | Jarvis engine-contract version |
| `request_id` | Stable identifier for cancellation, events, and audit |
| `agent_id` | Stable agent identity, normally `jarvis` |
| `session_id` | Odysseus-owned conversation identifier |
| `messages` | Ordered context assembled by Odysseus |
| `allowed_tools` | Schemas for tools allowed on this turn only |
| `limits` | Context, output, tool-round, and timeout budgets |
| `required_capabilities` | Features this turn needs from the engine |

An adapter returns normalized events from this set:

| Event | Meaning |
| --- | --- |
| `text_delta` | Untrusted generated assistant text |
| `tool_call` | Untrusted structured action proposal |
| `usage` | Measured or backend-reported resource usage |
| `completed` | Engine generation ended normally |
| `error` | Normalized, operator-visible engine failure |

A tool result is added to the next engine turn only after Odysseus has validated
and executed the proposal. No engine event directly mutates canonical state.

## Engine compatibility gate

An engine can enter testing only when its adapter declares capabilities and
normalizes the canonical turn contract. It can become the active Jarvis engine
only after all of these pass:

- stable Jarvis identity appears in chat and voice without exposing a backend
  identity as the agent identity;
- deterministic plain-chat response through the real Odysseus route;
- multi-turn continuity reconstructed from Odysseus-owned state;
- structured tool proposal, validation, execution, and result round trip;
- a disabled or unauthorized tool remains unavailable even when requested by
  the model;
- context limit, compaction, output limit, and tool-round limits are enforced;
- cancellation, timeout, malformed output, and backend outage fail cleanly;
- the UI, memory store, tool registry, and worker routes require no
  backend-specific modification;
- the previous engine can be selected again without state conversion.

Quality and performance evaluation happens after compatibility. A brilliant
model that violates this contract is not a Jarvis engine.

## Current implementation anchors

Odysseus already implements much of `JOS-P0`; this protocol makes those pieces
an explicit compatibility boundary.

| Contract responsibility | Existing anchor |
| --- | --- |
| Stable Jarvis identity | `src/agent_identity.py` |
| Backend URL and provider normalization | `src/endpoint_resolver.py` |
| Backend inference normalization | `src/llm_core.py` |
| Context assembly and compaction | `routes/chat_helpers.py` |
| Per-turn tool restrictions | `src/tool_policy.py` |
| Tool-call loop and enforcement | `src/agent_loop.py` |
| Canonical chat/session state | `core/session_manager.py` |
| Worker task and result ownership | `src/jarvis_agent.py` |
| Voice and foreground routing | `routes/voice_routes.py` |
| Operator cockpit | `static/` |

## Protocol registry

Only `JOS-P0` is specified here. The remaining identifiers reserve clean
ownership boundaries so later protocols do not overlap:

| ID | Boundary |
| --- | --- |
| `JOS-P1` | Identity and constitution |
| `JOS-P2` | Context and attention |
| `JOS-P3` | Memory and provenance |
| `JOS-P4` | Actions, tools, and verification |
| `JOS-P5` | Authority, approval, and security |
| `JOS-P6` | Learning, evaluation, and promotion |
| `JOS-P7` | Observability, recovery, and rollback |

Extension contracts use a separate namespace. `JOS-EXT-1` defines how an
independently maintained project becomes a Jarvis capability without becoming
a second agent or model; see [Jarvis OS Extension Protocol](jarvis-os-extension-protocol.md).

## Definition of success

`JOS-P0` succeeds when replacing GPT-OSS with a compatible test engine changes
only endpoint configuration and its adapter fitment, while Jarvis keeps the same
identity, memories, sessions, tools, permissions, workers, GUI, and rollback
path.

## Non-goals

- Rewriting Odysseus.
- Creating a second orchestrator beside Odysseus.
- Moving existing modules merely to resemble a traditional operating system.
- Selecting or training the next model.
- Designing every future Jarvis protocol before its real boundary is traced.
