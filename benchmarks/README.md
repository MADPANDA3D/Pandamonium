# Benchmarks: certify unscripted tool use (MAD-785)

This directory is the model-neutral benchmark harness for unscripted tool use.
It exists because earlier tests named the tool and the expected steps, so they
proved recall rather than autonomous discovery.

## What it covers

The `v1` suite has paired scenarios across ten categories: capability
discovery, files, books/RAG, calendar, integrations, ORACLE read/control, safe
mutation, gated mutation, failure recovery, and long-response completion.
Prompts state user goals and constraints only. The hidden rubric lives in
`suite/v1/suite.json` and is never sent to a model.

Runs capture, per scenario: selected model, advertised and effective context,
prompt/output tokens, latency, every tool call with its result, the evidence
that backs the answer, and a failure reason. A failed, unavailable, or gated
tool can never be reported as success - the evaluator fails any run that claims
success without a successful tool result.

## Offline baseline (runnable now)

```bash
python scripts/run_benchmark.py --engine fixture
```

This replays the versioned deterministic fixtures
(`fixtures/v1/suite-fixtures.json`) through the real tool-block parser and
result formatter against in-process mock services. It writes raw evidence and a
scorecard to `benchmarks/runs/<run-id>/`:

- `run.json` - environment, git revision, pinned asset hashes
- `results.json` - every scenario result with all captured fields
- `evaluator-cases.json` - negative/contract case verdicts
- `transcripts/<class>/<scenario>.json` - the exact replayed turns
- `scorecard.md` - the concise scorecard

Exit code is `0` only when the baseline passes. `benchmarks/runs/` is generated
output and is git-ignored; deleting it is the entire rollback.

## Versioning

`baselines/v1.json` pins the SHA-256 of the suite, fixtures, and evaluator
cases, plus the required-pass list for each model class. Any edit to a pinned
asset is a deliberate baseline revision: update the hash and the expectations
in the same commit. Fixture model classes (`fixture-local`,
`fixture-flagship`) are reviewer-authored deterministic recordings, not live
model runs; their job is to pin the harness contract and comparability rules.

## Local and flagship comparability

Local and flagship classes run the same hidden rubric. Scoring is evidence
based, so different prose passes as long as the same capability was used and
the same grounded evidence was returned. The scorecard reports both classes
side by side.

## CT103 acceptance (operator-run, separate)

Live models are never required here, and a fixture pass never counts as live
acceptance. See `docs/benchmarks/ct103-acceptance.md` for the capture procedure,
the scorecard template (`benchmarks/scorecard-template.md`), and the
`--engine capture` scoring command.

## Rollback

Benchmark assets are additive. To roll back, revert the harness commit and
delete generated `benchmarks/runs/` artifacts; no application behavior, data,
or settings are touched.