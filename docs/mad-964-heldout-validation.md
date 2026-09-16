# MAD-964 held-out ingestion and lifecycle recovery

**In progress. No held-out package is validated or published.** All three original
URLs have now been supplied to real configured-model intake in authenticated,
disposable browser apps. The initial local app lacked an endpoint; read-only
inspection of the deployed configuration resolved that gate. Only its selected
model credential was staged privately for the disposable owner; no deployed
settings or personal installations changed. Credentials are absent from receipts.

[Source pins and scan receipts](mad-964-heldout-scans.json) distinguish initial
static results, missing-model behavior and actual model attempts. The configured
DeepSeek profile answered a small health probe but failed full intake requests.
The available Luna model exposed read/repair and CLI-evidence failures; Astra
reruns are in progress. None of these failures is counted as acceptance.

Stirling PDF's full shallow fetch completes in 28.3 seconds on the same 60-second
Git limit. Removing blob filtering avoids a second lazy-object transport during
checkout. Source size, timeout and security limits remain unchanged.

The additional source `https://github.com/junegunn/fzf` is reserved and has not
been read or fetched. Use it as genuinely unseen acceptance after the shared
repairs stabilize, alongside rerunning the three original URLs.

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

## Configured-model repairs and verification

- CLI argument validation now recognizes documented string argv placeholders and
  valueless boolean flags. Exact source evidence, matching argument names, actual
  operation checks and external-side-effect authorization remain required.
- A rejected response is retained for bounded repair. Remaining model calls are
  visible, and the prompt specifies the existing function-tool schema shape.
- Noncontiguous source windows carry explicit boundaries; concatenated snippets
  must not look like malformed upstream code.

- Literal filename search exposes deep definitions outside the initial 2000-path
  index, returning at most 100 matches without reading or executing files.

The expanded intake/transport/CLI focused set passes **67** checks. PR #272's first
pushed head passes all required CI, including **6492 Python checks (5 skipped)**
and Chromium. Local Chromium passed **185** checks. All **13** already published
inventory packages were downloaded with signature/digest verification and passed
native operations plus install/disable/enable/remove in a disposable owner; those
are regression results, not the missing held-out acceptance. Updated-head full
checks and another affected-corpus run remain due after semantic repairs stabilize.

## Remaining acceptance

Run configured-model original-URL ingestion, real package operation checks,
agent dispatch and browser install/lifecycle; resolve setup/dependencies and
publish only passing exact packages. Test interrupted actual builds and
failure cleanup for the admitted held-outs, and rerun affected corpus if generic
adaptation changes. Preserve the 15 outstanding MAD-963 inventory entries.
MAD-965 remains Backlog; no successor, application release, deployment or
personal-account package installation is permitted to masquerade as completion.
