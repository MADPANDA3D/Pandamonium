# JOS-P5 Runtime Baseline

Odysseus now produces a server-owned authority decision between JOS-P4
validation and native execution.

## Runtime contract

- `src/authority_protocol.py` classifies each action as read-only, bounded
  write, external side effect, destructive, controlled administrative, or
  unclassified. New/unclassified effectful capabilities fail closed.
- Every decision binds operator, Jarvis identity, session, request, call,
  capability target, exact argument fingerprint, permission mode, policy
  basis, and expiry. The decision ID becomes the P4 call's `authority_ref`.
- Existing owner/public restrictions, guide-only policy, staged email approval,
  worker write gates, and engaged ORACLE scope remain the native enforcement
  mechanisms. The authority layer records agent-loop decisions; direct broker
  dispatch still relies on its native gate, and extension policy remains
  ORACLE-specific until `MAD-750`.
- External and destructive operations without an existing native staged gate
  return `authority_approval_required` before dispatch.
- `routes/authority_routes.py` lets the authenticated operator inspect pending
  decisions, approve or deny them, select once/session/time-bounded/persistent
  scope, and revoke receipts. A changed material argument fingerprint never
  matches an old receipt.
- Once receipts are consumed atomically. Session receipts match only the bound
  session. Time-bounded receipts expire. Persistent receipts remain explicit,
  inspectable, and revocable. A denial is terminal only for the bound request.
- Approval previews and persisted action events redact credential-bearing keys
  and common bearer/provider token forms. Model and retrieved text cannot
  create or resolve a receipt.

Authority state is stored atomically under the configured Odysseus data
directory. Test runs using the repository's in-memory database configuration
keep the global store ephemeral.

## Verification

`tests/test_authority_protocol.py` covers unauthenticated and wrong-owner
denial, disabled policy, fail-closed new capabilities, exact fingerprints,
all receipt scopes and states, expiry/revocation, secret redaction, and a live
agent-loop approval/consume cycle. Existing worker and ORACLE security suites
continue to cover their native gates.
