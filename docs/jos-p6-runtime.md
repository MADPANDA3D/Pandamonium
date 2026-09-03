# JOS-P6 Runtime Baseline

JOS-P6 turns the existing Pandamonium Skills and teacher-audit features into a
controlled learning lifecycle. It does not fine-tune a model and it does not
let model confidence alter Jarvis behavior.

## Runtime ownership

- `src/learning_protocol.py` owns normalized candidates, evaluation evidence,
  promotion records, monitoring metrics, demotion, and rollback.
- `services/memory/skills.py` remains the native skill store. Only `published`
  skills and pre-status legacy records are discoverable or executable.
- `routes/skills_routes.py` remains the skill test/audit surface. Its existing
  live representative run is now the `original` case; deterministic schema and
  policy checks provide the required `boundary` and `negative` cases.
- `src/teacher_escalation.py` remains the proposal producer. Teacher output is
  saved as a review-only draft through the same candidate path as other learned
  skills.
- Leo/the authenticated owner is the promotion authority. A teacher or agent
  cannot approve its own output.

## Lifecycle

1. Observe a failed or successful procedure without treating the trace as
   trusted instructions.
2. Propose a typed, owner-scoped candidate with references to its source.
3. Normalize secrets, user-specific paths, private endpoints, and runtime IDs
   into discovery placeholders.
4. Reject prompt-injection, identity-change, and authority-bypass content before
   it can enter a runnable skill.
5. Validate the artifact against the existing skill schema and declared tool
   capabilities.
6. Evaluate `original`, `boundary`, and `negative` cases. Unknown,
   inconclusive, unavailable, or evaluator-failure results never pass.
7. Promote only after independent corroboration and an atomic ledger write.
   Automatic promotion is limited to read-only guidance; action-capable,
   authority/security, and infrastructure candidates require operator review.
8. Monitor uses, successes, failures, latency, and regressions.
9. Demote or restore an exact prior promotion snapshot without deleting the
   evidence history.

## Runtime surfaces

- `GET /api/skills/promotion-ledger` returns the authenticated owner's
  candidates, evaluations, promotions, and monitoring counters.
- `POST /api/skills/{skill_id}/promote` applies an operator promotion after the
  evidence contract passes.
- `POST /api/skills/{skill_id}/demote` removes a skill from runtime discovery
  while retaining its history.
- `POST /api/skills/{skill_id}/rollback` restores an exact promoted artifact by
  `target_promotion_id`.
- Existing add/update/markdown routes stage content as a draft before any
  promotion. Explicit low-risk, human-authored publication is recorded as an
  operator-reviewed promotion; action-capable artifacts still require a native
  audit run.

## Compatibility and fail-closed behavior

- `learning_enabled=false` stops teacher proposals and all promotion while
  leaving existing published skills usable.
- `auto_approve_skills` and `skill_min_confidence` remain compatible audit
  preferences, but neither can make a draft executable.
- The producer's confidence is stored for review and never counts as evidence.
- Promotion/rollback operational events reuse JOS-P7. Observability failure is
  fail-soft; evaluation or promotion failure leaves the skill a draft.
- No production side effect is created by evaluation itself. The live original
  case uses the existing skill-test execution path and the same tool schemas and
  authority controls as normal agent execution.

## Verification

`tests/test_learning_protocol.py` covers normalization, injection rejection,
unknown evaluator outcomes, low-risk automatic promotion, high-risk manual
review, producer self-approval denial, version rollback, monitoring, learning
disabled compatibility, and the draft execution boundary. Existing skill,
teacher, owner-isolation, prompt-injection, import, and CLI tests remain part of
the focused P6 regression set.

