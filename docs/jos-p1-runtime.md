# JOS-P1 Runtime Record

**Linear:** `MAD-749`

**Scope:** Source implementation only. This is not a deployment or live
configuration claim.

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

## Boundaries

- No dependency, deployment, service, endpoint, credential, repository, or
  infrastructure changed.
- Worker catalog generalization beyond identity prompts remains public-release
  certification work.
- Generic extension context, authority, and request-envelope convergence remain
  `MAD-750`.
