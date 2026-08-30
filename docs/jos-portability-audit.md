# JOS Public-Portability Audit

**Linear:** `MAD-748`

**Audited source:** `d6b45459a3d11845d7354f6b975039ad45d289ba`

**Protocol delta baseline:** `c941cdf0`

**Scope:** Read-only classification of the current JOS source branch before
deployment, packaging, or extension installation.

## Classification key

- **Protocol namespace** — stable internal JOS vocabulary that does not select
  an operator-facing identity or private installation.
- **Configurable default** — valid reference behavior only when supplied by
  authenticated installation configuration.
- **Reference-extension adapter** — ORACLE-specific host behavior that may stay
  temporarily while the generic host proves equivalent.
- **Private installation data** — Leo/MADPANDA/Home Lab workers, workspaces,
  paths, endpoints, names, or live findings that are not public defaults.
- **Defect** — behavior that contradicts the public-first P0-P7 or JOS-EXT-1
  contracts and has one bounded successor issue.

## Defects and exact successors

| ID | Classification | Evidence | Finding | Successor |
| --- | --- | --- | --- | --- |
| `ID-1` | Defect | `src/agent_identity.py:6-19`; `routes/chat_helpers.py:741`; `src/agent_loop.py:1607-1608` | A private Jarvis/Leo constitution is mounted when the backend model name contains `jarvis`. The model therefore still selects identity on chat and agent paths. | `MAD-749` |
| `ID-2` | Defect | `routes/voice_routes.py:73-99`; `src/agent_worker_adapters.py:90-109` | Voice and worker prompts duplicate private agent, operator, worker, and workspace identity instead of mounting one installation-owned record. | `MAD-749` |
| `ID-3` | Defect | `src/agent_loop.py:4261-4273`; `src/authority_protocol.py:236-240`; `src/operational_protocol.py:69-100` | Action, authority, and operational records fall back to the literal agent ID `jarvis` instead of the configured identity. | `MAD-749` |
| `ID-4` | Defect | `src/tool_execution.py:967-977`; `src/jarvis_agent.py:859-910` | Knowledge calls fall back to owner `leo` and agent `jarvis`, creating a real cross-installation ownership risk rather than harmless branding. | `MAD-749` |
| `P2-1` | Defect | `src/context_budget.py:19-29`; `src/context_compactor.py:216-225,352-359`; `src/model_context.py:573-580`; `src/settings.py:136-146`; `static/index.html:2358` | The reusable attention contract has an `oracle_state` class and setting. Other extensions cannot receive the same budget without an ORACLE-specific core branch. | `MAD-750` |
| `P4-1` | Defect | `src/action_protocol.py:157-166` | Every name in the dynamic extension set is classified as `extension:oracle`; the actual extension ID is lost. | `MAD-750` |
| `P5-1` | Defect | `src/authority_protocol.py:145-159,247-262` | `extension:oracle` is always a bounded write and is automatically allowed by an engaged-ORACLE branch. Permission mode is not derived from extension metadata. | `MAD-750` |
| `P6-1` | Defect | `src/learning_protocol.py:45-58` | Identity-change detection protects the hardcoded words `jarvis` and `leo`, not the configured agent and operator identities. | `MAD-750` |
| `P3-1` | Defect | `src/madpanda_knowledge.py:26-38`; `docs/jos-p3-runtime.md:48-67` | The optional knowledge implementation and runtime record contain MADPANDA/Jarvis collection names plus Leo's live vault paths and inventory. These are installation evidence, not portable defaults. | `MAD-750` |
| `FLOW-1` | Defect | `routes/voice_routes.py:1909-1934,2248-2278,2409-2420`; `src/agent_loop.py:4261-4289,4724-4742` | Model tool calls use P4/P5/P7, but deterministic voice worker dispatch calls `start_task` directly. Native owner/worker gates remain, yet the claimed common action, authority, and event envelope is absent. | `MAD-750` |
| `FLOW-2` | Defect | `routes/chat_routes.py:549-560`; `src/agent_loop.py:2671-2683,4917-4934` | Plain non-agent chat has P2 context reporting but calls the engine directly and emits no P7 request start/final event. The request-level trace is therefore an agent-loop baseline, not a universal chat baseline. | `MAD-750` |

The successors above already exist and own the smallest relevant seams. No new
issue or abstraction is needed.

## Valid reference and installation classifications

| Classification | Evidence | Disposition |
| --- | --- | --- |
| Protocol namespace | `src/qdrant_projection.py:106-111`; `src/memory.py:16-24`; `mcp_servers/memory_server.py:199,237` | Internal `jarvis:*` provenance and deterministic-ID namespaces do not select the display identity. Keep unless `MAD-750` finds a migration reason while making provider defaults portable. |
| Protocol namespace | `src/operational_protocol.py:25-35`; `specs/jarvis-os-protocol-zero.md` | `JOS-P0` through `JOS-P7` and `JOS-EXT-1` are product protocol identifiers, not agent names. Keep. |
| Reference-extension adapter | `.env.example:161-162`; `core/middleware.py:23-31`; `routes/voice_routes.py:1195-1226`; `src/ai_interaction.py:782-794` | Existing ORACLE URL, iframe origin, lifecycle phrases, and UI event bridge remain until `MAD-754` proves the generic host and removes equivalent special branches. |
| Reference-extension adapter | `routes/voice_routes.py:2538-2585`; `src/model_context.py:754-787` | The voice adapter passes ORACLE state through the already-generic `extensions` report and injects live schemas through the existing agent loop. Reuse these seams in `MAD-751` and `MAD-754`; do not add another runner. |
| Private installation data | `routes/voice_routes.py:887-945`; `src/agent_worker_adapters.py:471-490` | MADPANDA, Business, Home Lab, Project Linux, Friday, Gordon, Hermes, and the current worker topology are Leo's installation profile. They cannot be a clean-install requirement. Identity/profile extraction belongs to `MAD-749`; public absence is certified by `MAD-756`. |
| Private installation data | `services/freetoken-runtime-ui/freetoken-runtime-ui.service:10`; `services/vps-worker/jarvis-vps-codex.service:9-32`; `scripts/verify_jarvis_voice_stack.py:20` | Fixed LAN hosts, Unix users, paths, service names, and verifier usernames are deployment assets. They are not extension-host defaults and must be excluded, templated, or explicitly opt-in at public certification (`MAD-756`). |
| Configurable default | `src/madpanda_knowledge.py:27-33`; `.env.example` Qdrant settings | Qdrant remains optional and projection-only. Collection names, read promotion, and knowledge providers must become portable with backward-compatible aliases in `MAD-750`; no second memory store is needed. |

## P0-P7 enforcement inventory

| Protocol | Current source truth |
| --- | --- |
| `P0` | The engine boundary is canonical. Identity still leaks through model-name inference, so compatibility depends on `MAD-749`. |
| `P1` | Contract only. Chat, agent, and voice do not yet mount one configured identity/constitution. |
| `P2` | Context tagging, manifests, budgets, trimming, compaction, and tool-schema budgets run through existing chat and agent paths. The extension-state class is still ORACLE-specific. |
| `P3` | Canonical memory provenance, review/migration, owner filtering, and optional projection behavior are present. Knowledge-provider names and live documentation are not portable yet. |
| `P4` | Built-in, MCP, UI, worker-tool, and ORACLE-native calls made through `stream_agent_loop` share the envelope. Direct voice broker dispatch does not. |
| `P5` | Agent-loop calls receive server-owned authority decisions and receipts. Extension authority is ORACLE-special-cased, and direct broker dispatch relies only on its native owner/worker gate. |
| `P6` | Draft discovery, evaluation, promotion, demotion, and rollback gates are integrated. Identity safety matching is not configured. |
| `P7` | Agent-loop actions/responses and learning promotions emit correlated events; backup/rollback records exist. Plain chat and direct voice broker requests are not in the same trace. |
| `EXT-1` | ORACLE is a working reference adapter. A reference-neutral manifest, registry, installer, and generic host proof remain `MAD-751` through `MAD-754`. |

## Existing seams to reuse

- `src/settings.py` and the existing settings API/UI for installation identity,
  context policy, and managed extension configuration.
- `src/agent_identity.py` as the single compatibility module to replace, not a
  new identity framework.
- `build_context_manifest(..., extensions=...)` for arbitrary extension IDs.
- `extra_tool_schemas`, `compose_capability_catalog`, `tool_policy`, and the
  existing `tool_executor` hook for dynamic capabilities.
- `AuthorityStore`, `ProtocolEventStore`, `DATA_DIR`, and `atomic_write_json`
  for policy/evidence persistence.
- Existing native runners for built-ins, MCP, workers, UI, and ORACLE. The
  manifest registry must describe and select them, not duplicate dispatch.

## Audit conclusion

The P2-P7 source is a useful enforcement baseline, but the branch was not yet a
public-generic implementation. The next dependency remains `MAD-749`, followed
by `MAD-750`. No source behavior, service, repository, dependency, deployment,
or infrastructure changed during this audit.

## Post-audit resolution

`MAD-749` resolved `ID-1` through `ID-4` in source: the existing settings path
now owns the stable agent record, chat/agent/primary voice mount one resolver,
action/authority/operational records use its `agent_id`, private constitutions
are hidden from non-admin reads, and knowledge paths no longer fall back to
literal owner `leo` or agent `jarvis`. The implementation evidence is recorded
in [the P1 runtime record](jos-p1-runtime.md). No deployment claim is implied.

`MAD-750` resolved `P2-1` through `P6-1` and `FLOW-1` through `FLOW-2` in source.
The shared context class is `extension_state` with a legacy `oracle_state`
settings alias; action targets retain actual extension IDs; extension authority
uses declared server metadata and fails closed; learning protects configured
identities; optional projection defaults are public and keep legacy aliases;
and plain chat plus direct voice worker dispatch now produce correlated P7
events. The evidence is recorded in
[the generic P2-P7 runtime record](jos-p2-p7-portability-runtime.md). No
deployment claim is implied.
