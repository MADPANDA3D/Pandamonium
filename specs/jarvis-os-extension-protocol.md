# Jarvis OS Extension Protocol

**Protocol ID:** `JOS-EXT-1`

**Version:** `0.2`

**Status:** Manifest, registry, pinned installer, generic live-catalog host, and native skill-bundle adapter

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

ORACLE is a reference extension ID, not a special extension class. The same
host contract must accept a differently named compatible extension without core
code changes.

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

## Source installation contract

The intended public installation experience may begin with a Git URL, similar
to installing a community package. A repository is not compatible merely
because it can be cloned, and cloning it does not authorize its code or tools.

Before activation, Odysseus MUST:

1. read a versioned extension manifest or supported standard descriptor;
2. identify the extension ID, source revision, runtime requirements,
   capabilities, schemas, permissions, health check, and removal procedure;
3. present declared permissions for operator approval;
4. pin the accepted source revision rather than tracking a mutable branch;
5. install outside the Odysseus source tree through a bounded lifecycle;
6. validate the live capability catalog before exposing tools;
7. retain the prior working revision for rollback;
8. remove the catalog, state, and tools cleanly on disable or uninstall.

Tool discovery maps declared manifests, live catalogs, or supported standards
such as MCP/OpenAPI. It does not guess arbitrary endpoints or execute arbitrary
repository setup scripts without an explicit adapter and operator approval.

The source installer accepts canonical HTTPS repositories from its supported
public Git hosts and resolves `HEAD`, a named branch/tag, or an advertised full
commit to one immutable revision. Preview uses a bounded Git checkout under the
managed extension root, returns the manifest permissions and lifecycle vectors,
and creates an exact P5 approval decision. Approval never authorizes undeclared
setup scripts: only an installed Odysseus adapter may implement a runtime.

## Manifest v1

The normative schema is
[`jos-extension-v1.schema.json`](schemas/jos-extension-v1.schema.json). A
manifest declares:

| Area | Required declaration |
| --- | --- |
| Identity | Protocol version, extension ID, display name, extension version |
| Source | HTTPS repository URL and full pinned Git revision, or `self` for installer binding |
| Runtime | Runtime type and repository-relative entry point |
| Capabilities | Inline schema or reference to an MCP, OpenAPI, or live catalog descriptor |
| Authority | Default requested permission plus per-capability overrides |
| Health | Catalog or HTTP health check and bounded timeout |
| Lifecycle | Declarative argument vectors for install, start, stop, and removal |
| Data | Repository-relative read/write paths and explicit HTTPS network origins |
| Recovery | Removal paths, preserved paths, pinned-revision rollback, retention |

MCP, OpenAPI, and live-catalog manifests reference the existing descriptor;
they do not copy its tool schemas into the manifest. The responsible adapter
resolves that descriptor and passes its schemas, health result, and observed
source revision to the registry. Inline schemas are reserved for extensions
that have no supported external descriptor.

An in-repository manifest uses `source.revision: self` because a file cannot
contain the hash of the commit that contains itself. The installer resolves the
requested ref first, checks out that exact full revision, and replaces `self`
with the observed immutable revision before registry admission. An explicit
hash in the manifest must match exactly.

`src/extension_registry.py` validates and atomically stores only normalized
manifest metadata and effective capability schemas. It does not fetch an
endpoint, clone a repository, execute lifecycle vectors, or dispatch a tool.
Unknown security-relevant fields, malformed or duplicate schemas, unhealthy
catalogs, revision mismatches, and cross-extension name conflicts fail closed.
Disabled extensions expose neither tools nor context metadata.

`src/extension_installer.py` owns pinned source checkout and reversible package
state outside the Odysseus source tree. It reuses P4 action validation, P5 exact
approval receipts, P7 events, atomic JSON state, and the extension registry.
Its built-in adapters support static web extensions with inline schemas,
configured external web runtimes with bounded live catalogs, and reviewed
native skill bundles; all require empty lifecycle vectors. MCP, OpenAPI,
service, or command-driven runtimes stop with `extension_adapter_required`
until an explicit host adapter exists.

### Native skill-bundle adapter

Pinned repositories may declare `runtime.type: skills` with a
`capabilities.descriptor.type: skill_bundle`. The descriptor has exactly these
fields:

| Field | Contract |
| --- | --- |
| `type` | Must be `skill_bundle` |
| `format` | `agent_skill` or `codex_plugin` |
| `include` | Non-empty, unique list of reviewed skill IDs |

For `agent_skill`, `runtime.entrypoint` names one repository-relative
`SKILL.md`, and `include` contains exactly its validated skill ID. For
`codex_plugin`, the entry point names a repository-relative JSON plugin
descriptor whose `skills` field names one repository-relative skill directory;
only immediate child directories containing `SKILL.md` are candidates.
`include` selects the admitted subset, and an unknown requested ID fails closed.
This is the partial-admission boundary; the adapter never admits every plugin
skill implicitly.

The adapter MUST:

1. validate the immutable checkout, descriptor, selected paths, non-symlinked
   files, bounded text assets, strict supported skill frontmatter, unique skill
   IDs, and owner-scoped name collisions before activation;
2. reuse Odysseus's existing `SkillsManager` storage, parsing, discovery,
   invocation, enablement, and owner scope rather than introducing another
   skill root, registry, installer, or invocation path;
3. copy only each reviewed skill directory into the native owner-scoped skill
   store, mark it as an approved installed skill, and retain extension ID plus
   pinned source revision as provenance;
4. install a multi-skill admission atomically: a malformed skill, collision,
   unsafe path, or write failure leaves the previously active skill set intact;
5. record only manifest/provenance, requested permissions, data boundaries,
   and admitted skill metadata in the extension registry, never skill bodies;
6. remove the managed native skill entries on disable or uninstall, reinstall
   only the pinned reviewed entries on enable, and atomically replace them on
   upgrade or rollback; and
7. preserve project artifacts and every skill not owned by that extension.

Installed skills remain untrusted instructions, not executable authority. Their
native `requires_toolsets` and platform gates still apply. The manifest's
requested permission modes and read/write/network boundaries travel with the
existing P4 action, P5 decision, P7 evidence, registry diagnostics, and P2
extension/skill context records. No skill can grant itself a tool, widen a
project boundary, or claim a successful action. The exact operator-approved
install is the manual JOS-P6 admission; source metadata alone never publishes a
skill.

Live web runtime locations come from the installation-owned
`ODYSSEUS_EXTENSION_URLS` map keyed by extension ID. They are never inferred
from a reference-extension name or embedded as private source defaults. The
adapter binds the manifest ID, configured origin, catalog response, pinned
revision, declared permission modes, and health result before registration.

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
- a differently named reference fixture passes without an ORACLE-specific core
  branch;
- installation, disable, upgrade, rollback, and removal preserve Odysseus when
  the extension is broken or unavailable.

## Non-goals

- A second extension-specific agent or voice.
- A hardcoded natural-language command engine.
- Copying extension source into Odysseus.
- Building a generic SDK before a second extension proves the shared need.
- Designing the deferred ORACLE vision helper.
- Replacing the existing OpenAI-compatible engine transport.
