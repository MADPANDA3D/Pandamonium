# Pandamonium capability and authorization contract

Status: accepted architecture checkpoint for MAD-778. This document defines the
canonical contract; it does not change runtime behavior, deployment state, or
CT103 data.

## Invariants

1. A Model supplies inference or artifact generation. It is replaceable and has
   no operator identity, durable authority, or workspace access of its own.
2. An Agent is the configured actor with identity, policy, memory context, and
   an active Model. Switching Models does not create a new Agent.
3. Every capability is discovered through the same entity envelope in
   `specs/schemas/pandamonium-discovery-v1.schema.json`.
4. An authenticated operator's explicit request authorizes ordinary read-only
   and reversible work inside configured scope. That request is the approval;
   the UI must not ask again.
5. A separate, exact approval gate exists only for destructive or
   difficult-to-recover work, external publication or communication, purchases,
   credential or authentication changes, privilege expansion, and access
   outside configured workspace boundaries.
6. Models, prompts, retrieved text, plugins, tools, workers, remote agents, and
   ORACLE cannot grant or widen authority. Delegation can only preserve or
   narrow the operator-approved scope.
7. Text and voice expose the same pending decision and approve-once/deny
   choices. ORACLE and every other fullscreen extension must yield to the chat
   approval surface; an approval may never exist only behind an iframe, overlay,
   log, or spoken message.

## Current capability inventory

The owner column names the smallest current code seam. A surface may use more
than one seam, but ownership is not assigned to a UI label or to a Model.

| Surface | Current capability | Owning code paths |
| --- | --- | --- |
| Chat | Authenticated streaming turns, session resume/stop/status, tool events, approval cards, and durable chat rendering | `routes/chat_routes.py`; `src/agent_loop.py`; `src/action_protocol.py`; `src/tool_schemas.py`; `src/tool_index.py`; `src/authority_protocol.py`; `routes/authority_routes.py`; `static/js/chat.js`; `static/js/chatStream.js`; `static/js/chatRenderer.js`; `static/index.html` |
| Voice | Session lifecycle, streamed turns/audio, interruption, deterministic actions, worker routing, and spoken worker events | `routes/voice_routes.py`; `routes/stt_routes.py`; `routes/tts_routes.py`; `static/js/jarvisVoice.js`; `static/js/voiceLifecycle.js`; `static/js/voiceOrb.js`; `static/js/voiceRecorder.js`; `static/index.html` |
| ORACLE | Optional extension/legacy geospatial tool harness, manifest-derived tools, bridge turns, and embedded presentation state; it is not an Agent or Model | `routes/voice_routes.py`; `src/extension_registry.py`; `src/extension_host.py`; `src/extension_mcp_adapter.py`; `src/ai_interaction.py`; `static/js/jarvisVoice.js`; `static/js/chat.js`; `static/app.js` |
| Sidebar | Sessions, models, tools, plugins, MAD MCP, and voice/worker activity navigation | `static/index.html`; `static/app.js`; `static/js/models.js`; `static/js/madMcp.js`; `static/js/sidebar-layout.js`; `static/js/section-management.js`; `static/js/sessions.js`; the matching route modules below |
| Settings | Owner preferences, feature toggles, integration state, agent identity, and provider configuration | `routes/auth_routes.py`; `routes/prefs_routes.py`; `src/settings.py`; `src/settings_scrub.py`; `src/agent_identity.py`; `static/js/settings.js`; `static/index.html` |
| MCP | Server transport lifecycle, catalog discovery, calls, OAuth connection state, Portal skills/mailboxes, and built-in MCP tools | `routes/mcp_routes.py`; `src/mcp_manager.py`; `src/builtin_mcp.py`; `src/tool_index.py`; `core/database.py` (`McpServer`); `static/js/madMcp.js`; `static/js/settings.js` |
| Extension lifecycle | Catalog, install plan/execute, pinned manifest validation, health, host, tool/skill adapters, rollback, and removal | `routes/extension_routes.py`; `src/extension_registry.py`; `src/extension_installer.py`; `src/extension_host.py`; `src/extension_mcp_adapter.py`; `src/extension_skill_adapter.py`; `specs/schemas/jos-extension-v1.schema.json`; `static/app.js`; `static/js/jarvisVoice.js` |
| Model/provider | Model discovery/probe/default selection, endpoint resolution, context limits, provider auth sessions, and UI selection | `routes/model_routes.py`; `src/model_discovery.py`; `src/endpoint_resolver.py`; `src/model_context.py`; `core/database.py` (`ModelEndpoint`, `ProviderAuthSession`); `static/js/models.js`; `static/js/modelPicker.js`; `static/js/providers.js`; `static/js/settings.js` |
| Worker bridge | Worker catalog/health, installation-owned workspace aliases, task dispatch, event stream, reply/cancel/approval, runtime evidence, and task knowledge | `routes/agent_task_routes.py`; `src/agent_worker_adapters.py`; `src/agent_worker_broker.py`; `services/codex-bridge`; `services/pc-codex-bridge`; `services/vps-worker`; `static/js/jarvisVoice.js`; `static/js/voiceOrbWorkers.js` |
| Knowledge | Owner-scoped memory, books, personal documents, wiki/source provenance, vector retrieval, and code-graph retrieval | `routes/memory/memory_routes.py`; `routes/personal_routes.py`; `routes/document_routes.py`; `src/memory_provider.py`; `src/personal_docs.py`; `src/knowledge_source_policy.py`; `src/rag_manager.py`; `src/madpanda_knowledge.py`; `src/graphify_runtime.py` |

The catalog is currently fragmented across route responses, database rows,
extension manifests, tool schemas, environment-backed worker configuration, and
client-side compatibility records. The discovery contract below is the common
projection; it is not a second source of truth.

## Canonical entities

| Entity | Canonical meaning | Current owner or planned projection |
| --- | --- | --- |
| Model | A replaceable inference or generation engine identified by a Connection plus provider-native model ID and measured capabilities. It never owns identity or authority. | `core/database.py::ModelEndpoint`, projected by `routes/model_routes.py` and `src/model_discovery.py` |
| Agent | A persistent configured actor: identity, policy, memory context, and current Model binding. One installation Agent exists today; model labels and voice targets are not additional agents unless registered as such. | `src/agent_identity.py`; unified discovery projection planned |
| Worker | A local or remote execution adapter with health, capabilities, and allowed Workspace aliases. A Worker may host an agent runtime but does not inherit the operator's authority. | `src/agent_worker_adapters.py::worker_catalog` and `src/agent_worker_broker.py` |
| Workspace | A logical, installation-owned scope alias resolved by the selected Worker or host. Discovery exposes aliases, never raw private paths. | `src/agent_worker_adapters.py::configured_worker_workspaces`; a first-class registry is planned |
| Knowledge Source | An owner-scoped retrievable corpus with provenance, availability, and permitted actions, such as memory, books, personal docs, wiki sources, or a code graph. | Current providers and routes in the Knowledge inventory row; unified projection planned |
| Connection | A credential and transport boundary to a provider, service, MCP server, or worker endpoint. Secret material is referenced, never discovered. | `ModelEndpoint`, `ProviderAuthSession`, `McpServer`, `Integration`, and worker configuration; unified projection planned |
| Plugin | An installed, revision-pinned extension manifest plus lifecycle, health, capabilities, and data boundaries. A Plugin cannot inherit host authority. | `src/extension_registry.py::ExtensionRegistry` and the JOS extension schema |
| Tool | A callable action schema with an owning source, current health, configured scopes, and per-action effect. Built-in, MCP, Plugin, Worker, and client-native tools share this definition. | `src/tool_index.py::ToolIndex`, action/tool schemas, MCP and extension adapters |

Every discovered entity must map to one current owner above or be marked
`planned`; a label alone is not an entity. Provider branding is metadata on a
Model or Connection, not Agent identity.

## Model-neutral discovery schema

`specs/schemas/pandamonium-discovery-v1.schema.json` is the normative wire
schema. Its single envelope covers all eight entity kinds with the same fields:

- stable `kind` and installation-local `id`;
- operator-facing `display_name` and `availability`;
- explicit ownership, health, authenticated-request requirement, configured
  scopes, and delegation rule;
- a secret-free source reference back to the owning code/config/runtime seam;
- actions classified by effect and authorization rule.

The schema contains one repo-grounded example for each entity kind. Discovery
must redact credentials, bearer material, raw private endpoint URLs, and raw
host paths. A Connection may report that credentials exist and a Workspace may
report its alias; neither may return the credential or resolved filesystem path.
Runtime discovery merges records by `(kind, id)` and treats the owning source as
authoritative for availability and health.

## Authorization matrix

The effect of the concrete action and arguments controls authorization; the
tool name, channel, provider, or agent persona does not.

| Action effect | Authenticated explicit request inside configured scope | Separate gate | Examples |
| --- | --- | --- | --- |
| Read-only | Authorized | No | Read files, inspect status, query configured knowledge |
| Reversible write | Authorized | No | Edit a workspace file, create a local document, change a non-auth preference, start a bounded worker task |
| Destructive or difficult to recover | Not sufficient | Yes | Delete, overwrite without recovery, purge, irreversible migration |
| External publication or communication | Not sufficient | Yes | Send email/message, publish/post, submit a form, create an external invite |
| Purchase | Not sufficient | Yes | Buy, subscribe, place a paid order, incur metered spend beyond an already approved bound |
| Credential or authentication change | Not sufficient | Yes | Add/rotate/revoke credentials, complete OAuth, change login or MFA state |
| Privilege expansion | Not sufficient | Yes | Grant a role, widen permissions, enable privileged execution |
| Outside configured Workspace boundary | Not sufficient | Yes | Read or write an unconfigured path, host, account, tenant, or workspace |

Unauthenticated requests, ambient conversation, background jobs, model output,
retrieved instructions, and delegated prompts are not operator consent. They
fail closed except for deliberately public read-only endpoints. An action that
combines effects uses the strictest row. A normal reversible action does not
become gated merely because it is performed through shell, MCP, a Plugin,
ORACLE, a Worker, or a remote agent. Conversely, those surfaces cannot downgrade
a gated effect.

A separate gate presents the exact target, bounded/redacted argument preview,
effect, Workspace, and approve-once/deny controls. Changing a material argument
invalidates the approval. Failure to render or resolve the decision denies the
action. Persistent or broader receipts are outside this v1 interaction contract.

### Channel-equivalent approval visibility

The server decision is canonical and channel-neutral:

1. Text renders the pending decision as a durable chat card.
2. Voice speaks a bounded summary and exposes the same decision in chat with
   approve-once and deny controls; voice may also accept those two choices.
3. If ORACLE or another extension occupies the foreground, it is collapsed or
   moved aside before the pending decision is announced.
4. Resolving in either channel resolves the same decision ID. Neither channel
   may create a broader or hidden approval.

Current evidence already provides the shared seams: `src/agent_loop.py` emits
`authority_approval_required`; `routes/chat_routes.py` streams it;
`static/js/chat.js` invokes `showChatForApproval`; `static/js/chatRenderer.js`
renders a redacted approve-once/deny card; and `static/js/jarvisVoice.js`
collapses the extension surface, renders worker approval controls, speaks a
worker approval notice, and refuses persistent voice approval. Generalizing that
parity to every effect in this matrix is implementation work, not a MAD-778
runtime change.

## Hardcoded activation and identity assumptions

These are inventory items for later configurable-alias work. Code symbol names
may remain for compatibility, but user-visible and routing behavior must resolve
from registered entities and installation configuration.

| Assumption | Current locations | Required later disposition |
| --- | --- | --- |
| `jarvis` and `friday` are the only direct-model voice targets; worker aliases map Jarvis/Friday/Hermes/Gordon to fixed IDs | `routes/voice_routes.py`; `static/js/jarvisVoice.js`; `static/index.html` | Resolve target aliases from Agent/Worker discovery records |
| “engage/activate/open/show/launch ORACLE” and shutdown variants are special grammar | `routes/voice_routes.py::_oracle_protocol_intent`; `src/agent_loop.py`; `src/tool_schemas.py`; `src/tool_index.py` | Register Plugin activation aliases and actions in configuration |
| “sir,” “Speak to Jarvis,” `character_name: Jarvis`, Jarvis-specific aria text, and fixed worker display labels are presentation defaults | `routes/voice_routes.py`; `static/js/jarvisVoice.js`; `static/index.html` | Render `Agent.display_name`, operator preference, and discovered Worker labels |
| `search_jarvis_knowledge` embeds one identity in a generic Knowledge Source action | `src/tool_index.py`; `src/tool_schemas.py`; `src/tool_parsing.py`; `src/tool_execution.py` | Keep a compatibility alias while exposing a neutral knowledge action |
| `home-lab`, `vps-ops`, `pc-codex`, `hermes`, `vps-codex`, `hermes-agent`, and ChatGPT URL heuristics participate in routing | `routes/voice_routes.py`; `src/agent_worker_adapters.py`; `static/js/jarvisVoice.js` | Resolve Workspace, Worker, Connection, and Model records instead of names/URLs |
| The canonical `PANDAMONIUM_WORKER_WORKSPACES_JSON` name is translated to an internal `ODYSSEUS_*` reader | `src/env_compat.py`; `src/agent_worker_adapters.py`; `.env.example` | Retain compatibility while moving the owner to the canonical configuration name |
| ORACLE may be injected as a legacy client record when a URL exists | `static/app.js`; `static/js/jarvisVoice.js`; `routes/voice_routes.py` | Remove the compatibility record only after registry equivalence is proven |

The generic defaults in `src/settings.py` and identity resolution in
`src/agent_identity.py` are the intended base. GPT/Qwen/provider preset labels
are valid Model or Connection metadata unless they are used to infer Agent
identity or authority.

## Implementation checkpoints and proof

1. **Authority semantics (MAD-779):** replace broad tool-name buckets with the
   eight action effects above; treat ordinary reversible work as authorized by
   the request; add the six exclusive gate reasons; enforce exact arguments,
   configured Workspace boundaries, and narrower-only delegation.
2. **Discovery projection:** project the eight entity kinds from existing
   owners, merge by `(kind, id)`, expose redacted availability/health, and keep
   planned entities visibly planned.
3. **Identity and aliases:** move voice targets, activation phrases, honorifics,
   Worker labels, and compatibility names to installation-owned entity aliases.
4. **UI parity:** render one decision component from chat and voice, force every
   extension surface to yield, restore pending decisions after reconnect, and
   keep approve-once/deny behavior identical.
5. **Compatibility retirement:** remove legacy ORACLE and identity shims only
   after discovery, lifecycle, and rollback equivalence is demonstrated.

Focused tests for those checkpoints must cover every effect row, mixed-effect
strictness, unauthenticated denial, exact-argument invalidation, Workspace
escape denial, plugin/MCP/worker non-escalation, model replacement preserving
Agent identity, all eight discovery examples, secret/path redaction, chat/voice
decision-ID parity, ORACLE foreground yielding, reconnect restoration, and
legacy alias compatibility. MAD-778 itself is guarded by
`tests/test_agent_capability_contract.py` and JSON parsing of the schema.

Rollback is per checkpoint: revert the checkpoint commit or restore the prior
immutable release pointer and retain existing compatibility aliases/config. No
schema migration or destructive data rewrite is required for this contract.

### CT103 boundary and read-only proof

MAD-778 does not deploy, restart, reconfigure, snapshot, or write data on CT103.
The 2026-09-04 read-only check found CT103 running the same source revision as
this checkout before the documentation change (`14802ac7`) through immutable
release `v1.0.7-14802ac7`; `odysseus.service` was active with zero restarts and
the current release pointer targeted that release. Authentication and the three
worker bridges were enabled. Only non-secret configuration keys, logical
Workspace aliases, service state, and the credential-file key name were read;
no credential values or CT103 data were captured.

The live unit exposed canonical `PANDAMONIUM_WORKER_WORKSPACES_JSON`;
`src/env_compat.py` maps canonical `PANDAMONIUM_*` values to established
internal `ODYSSEUS_*` readers before `src/agent_worker_adapters.py` loads them.
That compatibility seam is recorded for later cleanup and is not changed here.
Any later CT103 implementation requires local tests first, an immutable release,
a staged backup/rollback point, redacted health readback, and separate deployment
authorization.

## MAD-778 acceptance evidence

- All requested current surfaces map to owning code paths in the inventory.
- The eight canonical entities each map to current ownership or an explicit
  planned projection and have a repo-grounded schema example.
- One model-neutral schema defines discovery, permissions, health, actions, and
  source provenance without secrets or raw private paths.
- One authorization matrix applies unchanged to text, voice, ORACLE, Plugins,
  Worker bridges, and remote agents.
- Approval visibility and ORACLE foreground behavior are normative and tied to
  current rendering/event seams.
- Hardcoded activation, identity, routing, and compatibility assumptions are
  enumerated for later removal.
- Checkpoints, focused tests, rollback, and CT103 non-mutation boundaries are
  explicit. MAD-779 is the successor; no MAD-779 implementation is part of this
  checkpoint.
