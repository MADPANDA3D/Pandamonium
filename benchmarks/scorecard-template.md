# Unscripted Tool-Use Scorecard Template

> Copy this template into a run report (or link the generated
> `benchmarks/runs/<run-id>/scorecard.md`). Fill every row from raw evidence,
> never from memory.

## Run

- Run id:
- Date / operator:
- Engine: `fixture` (offline) | `capture` (CT103 or other live capture)
- Suite / harness version:
- Git revision:
- Raw evidence directory:

## Per-model results

| model | class | context advertised | context effective | scenarios | pass | fail | false success |
|---|---|---|---|---|---|---|---|
| | | | | | | | |

## Per-scenario results

| scenario | category | surface | model | verdict | prompt tok | output tok | latency ms | tool calls (ok/gated/failed) | evidence | failure reason |
|---|---|---|---|---|---|---|---|---|---|---|
| | | | | | | | | | | |

## Gated and failure behavior

| scenario | mutation class | gate observed | approval requested | success claimed without evidence |
|---|---|---|---|---|
| | | | | |

## Evaluator contract cases

| case | expected | actual | result |
|---|---|---|---|
| | | | |

## CT103 acceptance (operator-run)

| scenario | model | capture file | verdict | reviewer | date |
|---|---|---|---|---|---|
| | | | | | |

## Notes

- Larger gaps found (file an issue; do not hide them here):
- Rollback: generated run artifacts live under `benchmarks/runs/` and are
  removable without touching any versioned asset.