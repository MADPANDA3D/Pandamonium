# Jarvis OS Authority, Approval, and Security

**Protocol ID:** `JOS-P5`

**Version:** `0.2`

**Status:** Generic source implementation; live acceptance pending

**Runtime record:** [JOS-P5 runtime baseline](../docs/jos-p5-runtime.md)

**Authority and enforcement owner:** Odysseus

## Purpose

Capabilities describe what the system can do. Authority decides what this
authenticated operator, agent, worker, and request may do now.

`JOS-P5` defines identity binding, ownership, privileges, scoped permission,
approval receipts, secret handling, and fail-closed enforcement for Jarvis
actions. It consumes action proposals from `JOS-P4`; the model never grants its
own permission.

## Authority hierarchy

- Leo is the final product and operator authority.
- Odysseus authenticates the active operator and enforces the configured
  security posture.
- Jarvis acts only within the current request, session, capability, and
  approval scope.
- Workers and extensions receive narrower delegated authority, never Leo's
  full authority by inheritance.
- Models, retrieved sources, memories, tools, and result text have no authority
  to approve actions or change policy.

An operator instruction expresses intent. Odysseus still verifies identity,
scope, target, and any required approval at execution time.

## Authority decision

Before an effectful call executes, Odysseus MUST produce a logical
`AuthorityDecision`:

| Field | Meaning |
| --- | --- |
| `decision_id` | Stable audit identifier |
| `operator_id` | Authenticated human owner |
| `agent_id` | Installation-configured agent or explicitly selected actor |
| `session_id`, `request_id`, `call_id` | Bound request scope |
| `capability` | Canonical action name and target |
| `argument_fingerprint` | Exact approved action content |
| `permission_mode` | Read-only, bounded write, or controlled administrative |
| `decision` | Allow, deny, or approval required |
| `policy_basis` | Owner, privilege, mode, explicit instruction, or receipt |
| `expires_at` | End of once/session/time-bounded authority |

The decision is server state. A model-provided `approved=true`, copied approval
phrase, or imported transcript is not an approval receipt.

## Default policy

Odysseus MUST apply least authority:

- unauthenticated access receives no owner-scoped or effectful capability;
- bearer tokens are limited to their scopes and owning user;
- read-only access does not imply write access;
- administrative access does not silently waive an explicit per-action
  approval requirement;
- new tools, MCP servers, extensions, workers, and permission keys default to
  unavailable until classified;
- plan/guide-only modes are enforced at the execution gate, not only by prompt;
- extension disengagement revokes its scoped capabilities immediately;
- task completion, cancellation, session end, expiry, or policy change revokes
  temporary authority.

Single-user or auth-disabled deployments are an explicit operator-selected
mode, not proof that every network caller is Leo. Loopback and reverse-proxy
assumptions remain server policy and must be observable.

## Approval receipts

When policy requires approval, Odysseus MUST show Leo the material effect:

- action and target;
- bounded arguments or a safe exact preview;
- files, account, recipient, service, or external destination affected;
- whether the action is reversible;
- whether approval is once, session-scoped, time-bounded, or persistent.

An approval receipt binds the operator, action fingerprint, scope, and expiry.
Changing material arguments invalidates it. `Always` approval is a persistent
policy change and MUST be explicit, inspectable, revocable, and unavailable to
the model as a self-selected shortcut.

Denial is a terminal authority result for that call unless Leo submits a new
authenticated instruction.

## Permission classes

The baseline classes are:

| Class | Examples | Default |
| --- | --- | --- |
| Read-only | Search, inspect, list, health, status | Allowed when owner/scope permits |
| Local reversible write | Draft, create/edit owned document, preference change | Policy or approval dependent |
| External side effect | Send message/email, publish, remote API mutation | Explicit approval unless pre-authorized |
| Destructive or hard-to-reverse | Delete, overwrite, restore, revoke, bulk change | Exact approval and recovery path |
| Administrative/infrastructure | Credentials, model serving, MCP/endpoints, services | Admin plus explicit scoped authorization |

Repository and extension policies may narrow these classes. They cannot widen
them through prompt text.

Extension capabilities MUST declare a server-owned permission mode in their
catalog metadata. Missing or invalid extension permission metadata is
unclassified and denied; neither an extension ID nor engaged lifecycle state
grants authority by itself.

## Secrets and private data

Credentials, tokens, private keys, encryption keys, and provider secrets MUST:

- remain in the appropriate secret store or protected runtime file;
- never enter model context, memory, wiki generation, logs, Git, or action
  previews;
- be resolved only by the authorized runner at execution time;
- be redacted from health and error responses;
- be rotated or revoked independently of Jarvis memory and engine state.

Source content is owner-scoped before retrieval. Prompt-injection defenses do
not replace ownership and authorization checks.

## Delegation

Worker and extension delegation carries only the exact owner, workspace,
permission mode, task, and approval receipt required for that operation.

- Read-only worker tasks cannot approve mutations.
- Workspace-write tasks require the private mutation policy plus explicit
  approval and remain bounded to the requested workspace/action.
- Native worker approval systems remain active; broker approval does not bypass
  them.
- Extension tools remain subject to Odysseus owner, policy, and argument gates
  even when the extension itself trusts its parent origin.

## Current implementation anchors

| Responsibility | Existing anchor |
| --- | --- |
| Users, sessions, privileges, and MFA | `core/auth.py`, `routes/auth_routes.py` |
| Request identity and owner attribution | `src/auth_helpers.py`, `core/middleware.py` |
| Owner-scoped data access | route owner filters and `core/session_manager.py` |
| Admin/public tool restrictions | `src/tool_security.py`, `src/tool_execution.py` |
| Guide-only and per-turn restrictions | `src/tool_policy.py` |
| Worker mutation and approval gate | `src/agent_worker_adapters.py`, `src/jarvis_agent.py` |
| Secret encryption | `src/secret_storage.py` |
| URL and path confinement | `src/url_security.py`, `src/tool_execution.py` |
| Reference-extension origin/action enforcement | `routes/voice_routes.py`, ORACLE bridge |

The current runtime retains the existing owner/admin and worker-write gates and
adds the common decision envelope to agent-loop actions and direct voice worker
dispatch. Newly introduced permission keys and extension capabilities without
server-owned policy metadata fail closed rather than depending on UI defaults.

## Compatibility gate

`JOS-P5` is satisfied only when these pass:

- unauthenticated, wrong-owner, expired-session, and wrong-scope requests fail;
- a disabled privilege remains blocked at both catalog and execution time;
- an unknown/new effectful capability receives no authority by default;
- material argument changes invalidate a prior approval;
- once, session, time-bounded, persistent, denied, and expired approvals remain
  distinct and auditable;
- a retrieved prompt injection cannot approve a tool or reveal a secret;
- a read-only worker cannot mutate even when instructed by its model;
- an approved private worker write remains workspace/action scoped;
- ORACLE rejects an untrusted origin and Odysseus rejects an unauthorized
  native action;
- auth-disabled mode cannot be enabled by chat/model text;
- secrets stay absent from prompts, logs, events, exports, and Git fixtures.

## Definition of success

`JOS-P5` succeeds when every effectful action has an authenticated owner, a
server-enforced scope, and—when required—an exact revocable approval receipt,
with no path for a model or source document to manufacture authority.

## Non-goals

- Defining tool schemas or native execution; that belongs to `JOS-P4`.
- Requiring approval for every harmless read.
- Disabling capabilities Leo explicitly authorizes through the control plane.
- Moving secrets into a new infrastructure system merely to satisfy this spec.
