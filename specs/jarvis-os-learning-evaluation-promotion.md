# Jarvis OS Learning, Evaluation, and Promotion

**Protocol ID:** `JOS-P6`

**Version:** `0.1`

**Status:** Baseline contract

**Promotion owner:** Odysseus under Leo's authority

## Purpose

Jarvis should improve from experience without silently turning one model's
guess, one failed trace, or one imported conversation into permanent behavior.

`JOS-P6` defines how observations become candidates, how candidates are tested,
and how approved artifacts are promoted, monitored, and rolled back. Learning
is implemented through governed state and procedures; it is not uncontrolled
online weight mutation.

## Governed artifacts

This protocol applies to:

- reusable skills and procedures;
- prompt and routing guidance below the Jarvis constitution;
- retrieval and ranking policies;
- engine, adapter, extension, and tool compatibility candidates;
- evaluation cases derived from verified failures and successes.

Personal memory admission remains governed by `JOS-P3`. Constitution changes
remain explicit under `JOS-P1`. Authority expansion remains explicit under
`JOS-P5`.

## Promotion lifecycle

Every learned artifact follows:

1. **Observe** — capture a bounded, owner-scoped success, failure, correction,
   or repeated need.
2. **Propose** — create a candidate with source evidence and intended scope.
3. **Normalize** — remove secrets, user-specific accidents, and transient
   infrastructure details.
4. **Validate** — check schema, required tools, ownership, portability, and
   conflicts.
5. **Evaluate** — run deterministic checks and bounded representative cases.
6. **Review** — apply Leo's configured manual or automatic promotion policy.
7. **Promote** — publish a versioned artifact to its scoped consumers.
8. **Monitor** — record use, success, failure, latency, and regressions.
9. **Demote or rollback** — remove it from active selection without losing
   provenance or the prior approved version.

The producing model, teacher, or worker cannot approve its own candidate.

## Candidate record

A candidate MUST retain:

| Field | Meaning |
| --- | --- |
| `candidate_id` | Stable identifier |
| `artifact_type` | Skill, prompt guidance, ranking policy, adapter, extension, or eval case |
| `owner_scope` | User, workspace, extension, engine, or system scope |
| `source_refs` | Verified turns, results, corrections, or test artifacts |
| `producer` | Model, teacher, worker, operator, or importer |
| `required_capabilities` | Tools and environment needed |
| `risk_class` | Read-only guidance, bounded write, authority/security, or infrastructure |
| `status` | Draft, evaluating, approved, rejected, demoted, or superseded |
| `version` | Artifact version and parent version |
| `evaluation` | Cases, verdicts, metrics, evaluator, and time |

Confidence supplied by the producing model is metadata, not promotion proof.

## Evaluation rules

Evaluation MUST:

- include the original case and at least one boundary or negative case when the
  artifact can cause actions;
- use real tool schemas and policy gates without production side effects;
- distinguish task failure, evaluator failure, unavailable infrastructure, and
  inconclusive evidence;
- retain the exact model/runtime and artifact version used;
- check that verification steps actually prove the claimed outcome;
- prevent test data and tool output from issuing instructions;
- remain bounded in time, cost, tool calls, and retries.

An LLM judge is advisory unless corroborated by deterministic checks or native
results. Evaluator errors and `unknown` verdicts never count as a pass.

## Teacher and student models

A stronger teacher may diagnose a failed student turn, take over the current
task, and propose a draft skill. The captured trace remains untrusted data.

- Teacher output does not change Jarvis identity or authority.
- Teacher-authored skills start as candidates with the teacher and source trace
  recorded.
- User-specific hosts, paths, credentials, thread IDs, and one-off values are
  removed or replaced by discovery steps.
- A successful teacher answer is not enough; the extracted reusable procedure
  must pass its own evaluation.
- Failure to produce a safe portable procedure results in no skill.

## Promotion policy

Manual promotion by Leo is always allowed through an authenticated, auditable
control-plane action.

Automatic promotion MAY be enabled for low-risk artifacts only when policy
defines minimum evidence, confidence, pass rate, sample count, scope, and
rollback behavior. The following never auto-promote:

- Jarvis constitution or identity changes;
- new authority, credentials, secret access, or approval bypasses;
- destructive, publishing, messaging, financial, or infrastructure actions;
- artifacts whose only evidence is producer self-evaluation;
- candidates containing unresolved conflicts or untrusted instructions.

Promotion is atomic and reversible. Consumers see either the previous approved
version or the new approved version, never a partially written artifact.

## Evaluation suites

Odysseus SHOULD maintain small versioned suites for:

- engine compatibility (`JOS-P0`);
- identity and prompt-injection stability (`JOS-P1`);
- context selection and compaction (`JOS-P2`);
- memory admission/recall/deletion (`JOS-P3`);
- tool and result truth (`JOS-P4`);
- authority and approval (`JOS-P5`);
- observability, recovery, and rollback (`JOS-P7`);
- each promoted extension and worker adapter.

Real operator corrections may propose regression cases after sensitive data is
removed. Passing a suite permits promotion only within the suite's declared
scope.

## Current implementation anchors

| Responsibility | Existing anchor |
| --- | --- |
| Failure detection and teacher takeover | `src/teacher_escalation.py` |
| Skill storage, drafts, usage, and retrieval | `services/memory/skills.py` |
| Skill testing and audit jobs | `routes/skills_routes.py`, `src/builtin_actions.py` |
| Skill prompt isolation and confidence gates | `src/agent_loop.py`, `src/prompt_security.py` |
| Scheduled evaluation actions | `src/task_scheduler.py` |
| Engine and extension compatibility gates | `JOS-P0`, `JOS-EXT-1` specs |

The current teacher/skills system already records drafts, provenance, audits,
usage, and published status. It still needs one promotion ledger and uniform
risk policy: teacher-generated drafts can become discoverable before the full
cross-protocol evaluation and rollback contract exists.

## Compatibility gate

`JOS-P6` is satisfied only when these pass:

- a failed trace can create a draft without publishing it;
- prompt injection in the trace cannot enter the learned procedure;
- evaluator failure and unknown verdicts cannot promote a candidate;
- a low-risk skill passes original, boundary, and negative cases before
  promotion under the configured policy;
- an authority-changing candidate always requires explicit Leo approval;
- promotion records artifact, source, evaluator, cases, metrics, and version;
- demotion removes the artifact from new turns while retaining audit history;
- rollback restores the prior approved version;
- engine or extension promotion uses its protocol compatibility suite;
- learning can be disabled without disabling ordinary Jarvis operation.

## Definition of success

`JOS-P6` succeeds when Jarvis can turn verified experience into scoped,
versioned improvements while Leo can see why each artifact exists, how it was
tested, who promoted it, how it performs, and how to remove it safely.

## Non-goals

- Autonomous online fine-tuning or weight updates.
- Letting a teacher model become Jarvis or a policy authority.
- Saving every successful trace as a skill.
- Building a large evaluation platform before the small protocol suites prove
  a need.
- Implementing this protocol before `JOS-P4`, `JOS-P5`, and `JOS-P7` provide
  evidence, authority, and rollback.
