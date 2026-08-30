# JOS-P7 Runtime Baseline

Odysseus now has one request-level operational trace over its existing metrics,
health probes, durable sessions/tasks, backup tooling, and recovery mechanisms.

## Runtime contract

- `src/operational_protocol.py` persists a bounded, secret-redacted event
  envelope for engine start/final response, P5 decisions, and P4 results.
  Request, session, task, and call identifiers remain correlated across those
  events.
- All eight outcomes remain distinct: succeeded, failed, denied, cancelled,
  timed out, unknown, degraded, and unavailable. Unknown actions retain P4's
  no-automatic-retry rule.
- Event recording is fail-soft. An observability storage failure cannot grant
  authority, run an action, or block its native result.
- The existing admin diagnostics router now exposes protocol/component status
  and bounded trace queries in addition to its bounded, non-destructive service
  probes.
- The rollback registry records component configuration fingerprints,
  compatibility results, prior records, and the smallest component restored.
  Rolling back one component leaves unrelated canonical data and component
  records untouched.
- Canonical sessions, worker tasks, memories, and documents remain recovery
  sources. Retrieval indexes remain disposable projections and engine-native
  hidden state is not required.

## Backup evidence

`scripts/odysseus-backup` now embeds a `jos-p7.backup.v1` manifest containing
scope, creation time, source version, exclusions, external-vector treatment,
and restoration procedure. Snapshot output includes its SHA-256 digest.
Successful verification writes an atomic `.verified.json` proof beside the
archive; protocol diagnostics only report rollback availability for a verified
archive. Restore still rejects traversal, absolute paths, links, and special
files, and still stashes the replaced data directory first.

## Verification

`tests/test_operational_protocol.py` covers cross-component trace correlation,
all outcome states, restart reconstruction, secret-free controlled errors,
smallest-unit rollback, verified backup status, and fail-soft observability.
`tests/test_backup_cli_security.py` covers the embedded manifest, verification
proof, and existing safe extraction behavior.
