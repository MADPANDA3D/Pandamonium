# Jarvis OS Context and Attention

**Protocol ID:** `JOS-P2`

**Version:** `0.2`

**Status:** Runtime baseline implemented; live acceptance pending

**Context owner:** Odysseus

## Purpose

Jarvis needs the right information for the current turn, not every available
piece of information.

`JOS-P2` defines how Odysseus assembles, budgets, orders, labels, compacts, and
audits the bounded context mounted into a reasoning engine. The engine may
reason over that context, but it does not choose its own hidden sources or own
canonical conversation state.

## Implemented baseline

The existing Odysseus context paths now enforce the contract in two layers:

- **P2A observability** tags mounted context by class, source, and trust; reports
  mounted and removed token estimates, compaction, omissions, native tool
  catalogs, and extension lifecycle state; and strips internal metadata before
  provider transport. Implemented in `e6199188`.
- **P2B enforcement** applies deterministic class ceilings in the shared
  trimmer, preserves current operator intent and active tool-call/result pairs,
  bounds large memories/documents/wiki material, budgets native function
  schemas inside the same usable input window, and exposes the runtime policy
  in the existing **Settings -> Agent Tools -> Context Attention** panel.
  Implemented in `0df13d7e`.

This is an implementation baseline, not a deployment record. Qdrant indexes,
memory admission, source ingestion, and ChatGPT/Manus backfill remain `JOS-P3`
work and were not added by this slice.

## Context classes

Odysseus MUST keep these classes distinct:

| Class | Examples | Trust and lifetime |
| --- | --- | --- |
| Identity | Jarvis identity and constitution version | Trusted, stable |
| Operator intent | Current authenticated request and approved plan | Trusted as intent, turn/session scoped |
| Policy | Mode, authority decision, allowed tools, limits | Trusted, turn scoped |
| Conversation | Recent turns and Odysseus-owned summary | Canonical history plus derived summary |
| Working state | Active document, email, UI, extension, worker task | Dynamic, scoped, validated |
| Recalled memory | Approved personal-memory hits | Untrusted data with provenance |
| Retrieved knowledge | Lab docs, Obsidian, archives, generated wiki, web | Untrusted data with provenance |
| Tool and worker results | Correlated execution evidence | Untrusted content, authoritative only for its result fields |
| Presentation | Voice, preset, response-style guidance | Trusted behavior guidance below constitution |

Frequent use does not promote dynamic data into identity, policy, or memory.

## Logical attention packet

Before an engine call, Odysseus MUST be able to describe the mounted context as
a logical `AttentionPacket`:

| Field | Meaning |
| --- | --- |
| `request_id` | Correlates context, engine events, actions, and audit |
| `agent_id` | Canonical identity from `JOS-P1` |
| `session_id` | Odysseus-owned conversation |
| `goal` | Current operator request and explicit scope |
| `policy` | Mode, limits, authority, and allowed capabilities |
| `history` | Selected recent turns and any derived compaction summary |
| `working_state` | Current validated UI, document, extension, and worker state |
| `memory_hits` | Bounded approved memories with source references |
| `knowledge_hits` | Bounded source excerpts with locators and scores |
| `result_context` | Correlated tool/worker results for the active loop |
| `budget_report` | Estimated tokens by class, trimming, and omissions |

This is a logical contract. It does not require replacing the current message
array or OpenAI-compatible transport.

## Assembly rules

Odysseus MUST:

1. resolve identity, operator, session, mode, and authority before retrieval;
2. derive the current goal from the authenticated request and conversation;
3. select only sources relevant to that goal and active working state;
4. label retrieved and external content as untrusted data;
5. preserve source identity, locator, timestamp or version, and retrieval score;
6. select the effective tool catalog separately from knowledge retrieval;
7. fit the packet to the proven model context window with output headroom;
8. expose material omissions or degraded sources when they affect the answer;
9. retain enough audit metadata to explain why a source was mounted;
10. pass only the bounded packet to the engine.

The engine MUST NOT independently browse Jarvis memory, Qdrant, Obsidian,
filesystems, credentials, or extension state outside capabilities mounted by
Odysseus.

## Attention and budgeting

The budget is based on the context window actually reported or otherwise
proven for the selected engine. An unknown window MUST use a conservative
fallback rather than an optimistic model-family assumption.

Odysseus MUST reserve space for:

- the stable trusted prefix;
- the current operator request;
- the current tool loop and correlated results;
- a useful final response;
- provider-specific message overhead.

Within the remaining budget, current intent and recent verified state outrank
older conversation, recalled memory, broad documents, and generated wiki
material. One large source, memory collection, or tool description set MUST NOT
crowd out the current request.

Exact class budgets are runtime policy, not protocol constants. They MUST be
observable and adjustable without changing the reasoning engine.

The current default ceilings are independent percentages of the usable input
budget; they intentionally do not sum to 100:

| Enforced class | Default ceiling |
| --- | ---: |
| Conversation history | 45% |
| Active working state | 35% |
| Recalled memory | 15% |
| Canonical retrieved knowledge | 25% |
| Derived wiki knowledge | 10% |
| Native and described tool catalog | 20% |
| Correlated tool results | 30% |
| ORACLE extension state | 20% |
| Current-time context | 5% |

Identity, policy, presentation, and the current operator request are protected
classes rather than ordinary retrieval allocations. If the protected set alone
cannot fit, Odysseus reduces dynamic state and result content first, then trusted
prompt tails, and truncates current intent only as the final fallback.

## Compaction

When history approaches the usable window, Odysseus MAY summarize or trim older
turns. It MUST:

- preserve the current request, recent turns, active tool-call/result pairs,
  protected working state, identity, and policy;
- mark summaries as derived context rather than original evidence;
- retain the covered history range and compaction count;
- avoid turning a summary assertion into durable memory;
- fail without discarding canonical history if summarization fails;
- reconstruct the next turn after engine restart or replacement.

Canonical history remains outside the engine even when only a compacted view is
mounted.

## Retrieval lanes

`JOS-P2` consumes retrieval results but does not define their storage. The
selected target lanes are:

- approved personal memory from the `JOS-P3` memory index;
- canonical lab and Obsidian documentation from the `JOS-P3` document index;
- KarpathyWiki output from a separate derived-wiki index;
- the current Chroma-backed tool index for capability selection;
- live web, extension, or worker data only when the turn enables it.

Results from different lanes MUST remain attributable and independently
budgeted. Generated wiki text may help connect concepts, but canonical source
documents outrank it when they conflict.

## Current implementation anchors

| Responsibility | Existing anchor |
| --- | --- |
| Chat context assembly | `routes/chat_helpers.py`, `src/chat_processor.py` |
| Agent context and active-state mounting | `src/agent_loop.py` |
| Model context discovery and token estimates | `src/model_context.py` |
| Adaptive input budget | `src/context_budget.py` |
| History compaction and trimming | `src/context_compactor.py` |
| Context manifest and native-schema budget | `src/model_context.py` |
| Source trust wrappers | `src/prompt_security.py` |
| Dynamic tool attention | `src/tool_index.py` |
| Current-time context | `src/user_time.py` |
| Canonical session history | `core/session_manager.py` |
| Runtime policy persistence | `src/settings.py`, `routes/auth_routes.py` |
| Operator controls | `static/index.html`, `static/js/settings.js` |

Chat and agent paths retain their existing assembly flows, but both now pass
through the shared context annotation, manifest, and enforcement functions.
Compaction summaries carry derived-context provenance, and compatible providers
receive the same logical class policy while provider-specific payload sanitizing
continues unchanged.

## Compatibility gate

`JOS-P2` is satisfied only when these pass through real Odysseus routes:

- the same request produces the same logical packet across compatible engines;
- an unknown context window uses the conservative budget;
- a long session compacts without losing the current request or corrupting
  tool-call/result ordering;
- irrelevant memory and large wiki pages cannot displace current intent;
- every mounted memory and knowledge excerpt retains a source locator;
- prompt-injection text in every retrieval lane remains data;
- ORACLE state appears only while the extension is engaged and is removed on
  disengage;
- disabled tools do not reappear through tool retrieval or a skill;
- a retrieval or compaction failure degrades visibly while chat continues with
  the remaining valid packet;
- the packet can be explained without exposing secrets or hidden reasoning.

## Definition of success

`JOS-P2` succeeds when Odysseus can show what Jarvis was allowed to attend to,
why it was selected, how much budget it used, what was omitted, and how the same
turn can be reconstructed independently of the engine.

## Non-goals

- Storing or approving durable memories; that belongs to `JOS-P3`.
- Authorizing or executing tools; that belongs to `JOS-P4` and `JOS-P5`.
- Sending the complete vault or generated wiki on every turn.
- Replacing existing context code before the contract is accepted.
