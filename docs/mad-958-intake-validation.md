# MAD-958 intake validation

Base: PR #262 / `c98642d8e8c3a27abe7e10e78699e14ce75241d7`.
Branch: `codex/mad-958-semantic-intake`.

## Implemented boundary

The existing scan now reuses native manifests/skills or asks the owner's configured
model for a source-backed integration proposal. Exact quoted evidence binds
purpose, interfaces and every typed argument to files actually read. Generated
files remain package-local; no source code is executed during analysis. Empty
placeholder CLI schemas and invented web/MCP entrypoints were removed.

The retained archive follows the MAD-957 artifact path into installation, with
source, manifest, archive and tree identity checks. Status survives restart;
cancellation stops model work and prevents admission. The browser restores its
scan after reload and displays preparation/setup status without claiming tested
operations. Generated inline operations require a service adapter and cannot be
misrepresented as a static web plugin to mount unimplemented tools.

## Evidence, 2026-09-15

- Focused intake/contracts/package/publisher/installer/sidebar matrix: **91 passed**;
  includes source-link confinement and generated permission/runtime rejection.
- Chromium intake suite: 6 passed, including desktop/mobile, unverified purpose,
  setup, reload recovery and cancellation.
- Full Python first run: 6,434 passed, 5 skipped, one stale assertion expecting
  the former five-phase UI. Updated the assertion and added the two new phase
  elements. Final full Python rerun: **6,438 passed / 5 skipped** in 217.51 seconds.
  Final six-test browser rerun also passed. Required PR CI remains the merge gate.
- Ruff passes for changed Python sources/tests; mypy passes all five changed
  source modules with `--follow-imports=skip --ignore-missing-imports`; isort
  black-profile checks, Python compileall, JS syntax and diff hygiene pass.
- Actual standalone execution of the test-owned generated CLI fixture returned
  `{"result": 8}` for `{"count": 4}` after archive extraction. This checks the
  proposed adapter protocol, not general CLI runtime admission (MAD-959).
- A running **full app** passed authenticated HTTP endpoint registration, scan,
  owner-routed model requests, unknown skill-layout understanding, prepared
  package preview, native authority approval, Plugins/native agent Skills
  readback, disable, enable and remove. Exactly one source checkout and two model
  HTTP calls occurred. The source transport and model responses were local
  fixtures; application routing, persistence, packaging and admission were real.
  Server/model processes, temporary owner/endpoint/database/source and packages
  were removed. Script: `/tmp/pandamonium-mad958-live-smoke.py`.

The full-app smoke exposed a real integration fault: scheduled background-model
calls wait for interactive quiet, but scan polling and visible-browser heartbeats
continually reset that gate. Intake now uses the configured candidate resolver and
foreground LLM gateway on the application's event loop. Unit and full-app checks
cover the fix and coroutine cancellation.

## Limits and next baton

No live model provider was configured in the local checkout. The model HTTP
fixture proves dispatch and source-proposal plumbing, not an external model's
semantic accuracy. Live corpus/model and executable runtime acceptance continue
in MAD-959–MAD-965. No app release, CT103 deployment, public corpus publication or
personal-account installation occurred in MAD-958.

Rollback is a scoped revert of this PR; retain account/runtime data. Prepared
packages contain no owner credential values. The existing native skill/MCP
lifecycle and installed package records remain compatible. See the
[package-generation contract](repository-integration-generation.md) and
[approved program](repository-plugin-ingestion-plan.md).
