# Jarvis OS Extension Protocol

**Protocol ID:** `JOS-EXT-1`

**Version:** `0.1`

**Status:** Baseline contract

**Reference extension:** ORACLE

## Purpose

Jarvis is the persistent operating system. An extension adds a capability
domain to Jarvis without becoming a second agent, model, memory store, or
authority layer.

`JOS-EXT-1` defines the boundary between Odysseus and independently maintained
projects adapted as Jarvis extensions. ORACLE is the first implementation of
this contract and the pattern future open-source integrations must prove before
the contract grows.

## System hierarchy

- Leo is the operator and final authority.
- Jarvis is the stable identity and protocol-governed system.
- Odysseus is the control plane, cockpit, state owner, and enforcement surface.
- A reasoning model is a replaceable engine behind an adapter.
- An extension supplies domain UI, state, data, and native actions.
- Workers such as Gordon/Hermes and Codex remain separately routed services.

Engaging an extension changes Jarvis's available domain context and tools. It
does not change Jarvis's identity, voice, memory, permissions, or engine.

## Required extension contract

An extension MUST:

1. publish a versioned capability catalog from its live implementation;
2. expose only real native actions with bounded input schemas;
3. report sanitized state needed for Jarvis to understand the active view;
4. execute actions inside the extension's existing native runner;
5. return a correlated success or failure result for every requested action;
6. reject unknown actions, malformed arguments, and unauthorized origins;
7. remain optional so Odysseus works when the extension is absent;
8. keep provider credentials and private data outside model context and Git.

Odysseus MUST:

1. discover and validate the live capability catalog;
2. offer only the current turn's allowed capabilities to Jarvis;
3. treat model tool calls as untrusted proposals;
4. enforce owner, policy, permission, approval, and argument validation;
5. correlate each dispatched call with the extension result;
6. return actual results to Jarvis before Jarvis claims success;
7. fail visibly when the extension is unavailable or times out;
8. remove extension-specific context and tools when its protocol is disengaged.

The reasoning engine MAY choose and sequence allowed extension tools. It MUST
NOT decide that an action was authorized, executed, or successful.

## Lifecycle

The minimum lifecycle is:

1. **Discover** — Odysseus loads and validates the extension catalog.
2. **Engage** — the extension surface becomes active and its scoped tools/state
   are mounted for Jarvis.
3. **Operate** — Jarvis proposes one or more native calls; Odysseus validates,
   dispatches, and returns their results to the same agent loop.
4. **Disengage** — the extension surface, state, and tool catalog are removed
   without changing Jarvis or the active engine.

Lifecycle phrases may be routed deterministically. Domain commands belong to
Jarvis and the live capability catalog, not a parallel regex command brain.
Memorable phrases may be model guidance for native actions.

## Repository ownership

| Change | Canonical repository |
| --- | --- |
| Extension-native UI, data, feeds, state, or actions | Extension repository |
| Discovery, policy, agent loop, voice, lifecycle, or result enforcement | Odysseus |
| Installer, pinned component versions, and public distribution assembly | Jarvis OS distribution |
| Improvement useful to the original project without Jarvis assumptions | Upstream project when practical |

Forks retain their upstream remotes and history. Tested extension and Odysseus
versions are promoted by tag; the public distribution pins those versions. The
development frankenbuild is an integration laboratory, not a release artifact.

## ORACLE reference mapping

| Contract responsibility | Current implementation |
| --- | --- |
| Capability catalog | ORACLE `GET /api/oracle/capabilities` |
| Native catalog source | ORACLE `GEV_REALTIME_TOOLS` |
| Native action runner | ORACLE client tool runner |
| Origin-locked bridge | ORACLE `src/odysseusBridge.js` |
| Catalog/state relay | Odysseus `static/js/jarvisVoice.js` |
| Tool schemas and result correlation | Odysseus `routes/voice_routes.py` |
| Engine tool loop | Odysseus `src/agent_loop.py` |

In ORACLE mode, Jarvis remains Jarvis. ORACLE contributes the globe, live data,
scene state, and native actions. Jarvis chooses actions and explains verified
results through his existing voice and memory.

## Compatibility gate

An extension is Jarvis-compatible only when these pass through the real
Odysseus route:

- catalog discovery and schema validation;
- engage and disengage without changing Jarvis identity;
- one native action with a correlated verified result;
- one multi-action request completed through the same Jarvis loop;
- accurate capability explanation from the live catalog;
- unknown, malformed, unavailable, and timed-out actions fail closed;
- Odysseus remains functional with the extension disabled;
- a clean installation needs no private infrastructure values.

## Non-goals

- A second extension-specific agent or voice.
- A hardcoded natural-language command engine.
- Copying extension source into Odysseus.
- Building a generic SDK before a second extension proves the shared need.
- Designing the deferred ORACLE vision helper.
- Replacing the existing OpenAI-compatible engine transport.
