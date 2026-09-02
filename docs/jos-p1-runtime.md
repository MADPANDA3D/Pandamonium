# JOS-P1 Runtime Record

**Linear:** `MAD-749`

**Scope:** Source implementation only. This is not a deployment or live
configuration claim.

**Product follow-up:** `MAD-770` makes this protocol visible and configurable
through the existing first-run and authenticated Settings surfaces.

## Existing paths reused

- `src/settings.py` and `POST /api/auth/settings` store the installation agent
  ID, display name, constitution, and constitution version. The write route is
  admin-only and validates all four fields.
- `src/agent_identity.py` resolves and mounts one record. It never inspects the
  backend endpoint or model name.
- Chat, agent mode, and primary voice call the same resolver. Voice-specific
  phrasing remains presentation guidance below the constitution.
- P4/P5/P7 records use the configured `agent_id`; the admin-only protocol
  diagnostics expose safe identity metadata and fallback state.
- Non-admin and unauthenticated settings reads blank the constitution body.

## Public defaults and failure behavior

```json
{
  "agent_id": "assistant",
  "agent_display_name": "Assistant",
  "agent_constitution_version": "1"
}
```

The default constitution is generic and contains no Leo, MADPANDA, Home Lab,
ORACLE, worker, endpoint, or model assumptions. A clean installation can set a
different identity through its authenticated settings call. Jarvis is the
reference profile and can be applied after installation without changing code.

An invalid hand-edited identity value does not reach the model. The resolver
uses the safe public default for that field and reports `status: degraded` plus
bounded fallback reasons at `GET /api/diagnostics/protocol`. The diagnostics do
not include the constitution body.

## Compatibility evidence

- Two differently named compatible backends receive the same effective
  identity prompt because backend names are no longer resolver inputs.
- Chat and agent mode mount the same identity function.
- Primary voice mounts the same identity and constitution before its narrower
  voice presentation guidance.
- ORACLE engage/disengage appends extension state without replacing identity.
- Compaction preserves the trusted system prefix and does not promote retrieved
  content into identity.
- Worker transfers keep worker attribution; legacy route names such as
  `jarvis` remain compatibility aliases only.

## First-run and self-identification contract

The configured agent is the persistent identity. The selected model/provider is
a replaceable reasoning engine and is described separately. Every prompt surface
therefore tells the engine to answer identity questions using the configured
display name rather than adopting a vendor assistant identity.

On a clean install, the first admin welcome screen presents one resumable
checklist using existing owners only:

1. Configure agent ID, display name, constitution, and version in authenticated
   AI Defaults.
2. Connect at least one local or hosted model endpoint through the existing
   model manager.
3. Optionally connect integrations and install plugins through their existing
   managers.

The checklist derives identity state from safe authentication diagnostics and
model state from the model registry. It stores no second copy of either. The
constitution remains admin-only and is never included in first-run status.

## Reference-harness review

The `MAD-770` implementation reviewed the official repositories at these
revisions on 2026-09-01:

- `deepseek-ai/deepseek-harness` `4e84901e6471b79ec0338099867ebb4606d12bb5`
- `nousresearch/hermes-agent` `00b2e03c8028cbe9e6b59b03306be300c6a6df8c`

| Reference behavior | Pandamonium decision |
|---|---|
| DeepSeek fixed harness-identity opener plus a separate deployment persona | Adopt the explicit self-identification invariant; keep installation identity in the existing validated settings owner. |
| DeepSeek profiles, prompt-section registry, and plugin inventory | Keep the native Odysseus preset, context, extension registry, and lifecycle paths; do not add Cordis or a second plugin tree. |
| Hermes stable SOUL identity, model-neutral provider setup, and one-time onboarding hints | Adopt a visible, non-blocking first-run checklist and keep identity separate from the replaceable engine. |
| Hermes personality overlay and user profile as separate layers | Continue using Odysseus presets for presentation and governed memory for user facts; do not overload agent identity with either. |

ORACLE/God's Eye remains an extension capability and context projection. Its
engagement can change what the configured agent can see or do, never who that
agent is.

## Boundaries

- No dependency, deployment, service, endpoint, credential, repository, or
  infrastructure changed.
- Worker catalog generalization beyond identity prompts remains public-release
  certification work.
- Generic extension context, authority, and request-envelope convergence remain
  `MAD-750`.
