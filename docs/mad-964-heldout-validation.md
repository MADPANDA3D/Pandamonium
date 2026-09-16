# MAD-964 held-out ingestion and lifecycle recovery

**In progress. No held-out package is validated or published.** The original
ani-cli, gifukai-api and Stirling PDF URLs were fetched in disposable storage.
The canonical local app has zero configured model endpoints. An authenticated,
otherwise real browser app stops all three scans in `understand` with
`extension_scan_model_unavailable`, and shows the Settings/retry instruction.
No fixture model or reviewed proposal is counted as unseen generation proof.

[Exact source pins and scan receipts](mad-964-heldout-scans.json) retain the
initial result. Static classification is not semantic understanding: ani-cli is
unknown, gifukai-api is classified as Go, and Stirling PDF's incidental Rust
files produce `rust_lib`. None produces a static installable manifest. The source
sizes remain within the existing bounds. Stirling PDF's first Git attempt timed
out; a second bounded fetch succeeded without raising the 120-second ceiling.

The model question is pending: use an existing permitted endpoint in a disposable
owner/app, with credentials kept out of evidence and packages. Do not copy Leo's
installed ORACLE/Ponytail/Superpowers or relabel these failures as acceptance.
The three sources have now been read statically but never supplied to an intake
model. If semantic/interface-class fixes become necessary, reserve another fresh
source for genuinely unseen acceptance after those fixes.

## Shared recovery repairs

- A stale approved Install plan previously replaced a package installed after its
  preview. Execution now rechecks installed state and requires Upgrade. Persisted
  plan identity allows the original interrupted installation to retry after
  activation; a different plan cannot claim that retry.
- Duplicate execution on the application's lifecycle manager is serialized and
  returns the completed result without repeating activation. This deliberately
  serializes lifecycle operations on that manager; it is not a distributed lock
  or a cross-process transaction guarantee.
- A Git timeout previously killed the Git parent while transport children could
  continue writing into cleanup paths. The existing detached-process platform
  primitive now gives each invocation a group; error/timeout cleanup kills the
  group and reaps the process. No timeout, source-size or security gate was relaxed.

Regressions reproduce stale approval, simultaneous duplicate execution, retry
before/after activation, and a real delayed-writing subprocess child killed by
transport timeout. Existing native runtime checks cover missing configuration,
encrypted owner values/redaction, failed upgrade preservation, same-source
package distinction/rollback, disable/enable/remove, cancellation and resource
admission. These automated fixture checks prove the shared recovery behavior;
they do not prove the three held-out integrations.

```sh
sh scripts/check_package_runtimes.sh .venv/bin/python -m pytest -q \
  tests/test_extension_package.py tests/test_extension_installer.py \
  tests/test_extension_cli_adapter.py tests/test_extension_package_runtimes.py
```

The final focused set passes **42** checks; the earlier broader scanner/registry/
skill/native runtime set passed **86** before the two interrupted-install cases.
Ruff, isort Black profile, scoped mypy with explicit package bases, and syntax/diff
checks pass. Full Python/Chromium verification is recorded below when complete.

## Remaining acceptance

Run configured-model original-URL ingestion, real package operation checks,
agent dispatch and browser install/lifecycle; resolve setup/dependencies and
publish only passing exact packages. Test interrupted actual builds and
failure cleanup for the admitted held-outs, and rerun affected corpus if generic
adaptation changes. Preserve the 15 outstanding MAD-963 inventory entries.
MAD-965 remains Backlog; no successor, application release, deployment or
personal-account package installation is permitted to masquerade as completion.
