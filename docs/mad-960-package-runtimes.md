# MAD-960: package runtimes and setup

Generated packages now share one owner-scoped runtime for CLI, managed HTTP
services, connected APIs and indexed knowledge. Existing SkillsManager asset
admission, workspace/browser action bindings and Unsloth's external connection /
training-job adapter remain the native paths. Core code has no repository-name
switches. Full catalog population and held-out evaluation remain MAD-963/964;
release and target-host deployment remain MAD-965.

## Runtime contract

The package-local `.pandamonium/integration.json` extends the existing execution
recipe with optional `service` argv, `knowledge` files/graph/vectorization,
`voice_model`, and local device prerequisites. Every declared operation needs a
real check and typed input/output; discovery alone never enables tools.

- **Deploy/start:** approved setup writes dependencies only to the private runtime.
  `service` launches under a retained systemd scope, binds loopback at the allocated
  `PANDAMONIUM_PORT`, and passes both disposable and real-data operation checks.
- **Connect/configure:** declared values live encrypted in existing Integration
  rows. `ENDPOINT_ID` references an existing owned/shared ModelEndpoint, including
  its encrypted credential. Configuration reaches only the package sandbox via a
  read-only, anonymous file descriptor at `/run/pandamonium/config.json`.
- **Health/restart:** each call verifies owner, enabled package/revision, configuration
  fingerprint and running resource scope. Disable/enable restarts and rechecks.
  If the app or node dies, a dead service reports Needs setup; enable rebuilds it.
  New revisions activate before old services stop. Failed replacements retain the
  old service. Registration failure stops the new service and restores voice settings.
- **Remove/recovery:** disable/remove stop the exact scope and unregister tools.
  Failed new setup removes its partial revision runtime. Approved dependencies and
  declared owner HOME/artifacts survive disable/upgrade/remove for recovery.
- **Devices:** local GPU, display, audio and browser prerequisites fail with the
  exact missing setup. Connect an external runtime or reuse the existing workspace
  surface/worker binding when it supplies the capability. Device access and worker
  authority are not silently broadened. Unsloth continues through its existing
  adapter and durable training jobs, rather than a second launcher.

Setup changes invalidate outstanding approval plans and disable the installed
runtime before it can use changed configuration. The operator saves setup, reviews
a fresh preview, and approves revalidation. Secret values never return in the form;
blank secret input retains the saved value. Undeclared keys, cross-owner references,
credential-bearing output and stale configuration fail closed. No database schema
or production dependency was added.

## Enforced host requirements

Generated code requires Linux, bubblewrap, util-linux, a systemd user manager and
cgroup v2 memory/pids/cpu controllers. The app service user must have a dedicated,
**persistent** filesystem mounted at `ODYSSEUS_EXTENSION_RUNTIME_ROOT`, with a
finite capacity no greater than **8 GiB / 1,000,000 inodes**. Provision a dedicated
volume/filesystem for that path, owned by the service user, and mount it before
starting the app. An ordinary directory on a large home/root filesystem is refused.
Use the deployment host's supported mount/service configuration; do not remount
an existing data volume or share this quota with unrelated workloads.

All package scopes share `pandamonium-extensions.slice`: **4 GiB memory, no swap,
256 tasks, 200% CPU quota**. Each invocation/service additionally has **2 GiB /
128 tasks**. Actual kernel limits are read before releasing the bubblewrap startup
gate. Transient tmpfs mounts have explicit capacity limits. Existing per-process
CPU/file/fd limits and 64 KiB tool IO bounds remain. Unsupported hosts stay Needs
setup; no unsandboxed fallback exists. Timeout/cancellation kills the entire scope.
The global runtime lock intentionally serializes operations; per-package locking
can follow measured contention.

Ubuntu requires its restrictive `bwrap-userns-restrict` AppArmor profile. CI keeps
that profile and global restrictions active, starts the runner's user manager and
mounts a disposable 2 GiB filesystem. Local checks use an unprivileged private mount
namespace, never a host mount. Temporary tmpfs is for tests, **not persistent
production storage**. CT103 provisioning/readback is a later release gate.

An empty network declaration disables networking; a nonempty declaration shares
host networking. Destination declarations remain descriptive, not a firewall.
A connected API receives only the owner configuration explicitly supplied to it.

## Knowledge and collection setup

Selected immutable files become at most 2,048 bounded chunks (8 MiB total input).
The native SQLite FTS index lives in that package's private runtime; optional
vectors use the existing embedding client. `knowledge.search`, `knowledge.status`
and `knowledge.refresh` are explicit platform bindings with fixed schemas. Results
include source path/line, immutable revision, source URL and content hash. Refresh
atomically replaces the prior index; a changed embedding model requires refresh.
An upstream update uses the normal pinned package upgrade before refreshing.

Graphify's real Markdown extractor first builds the build-your-own-x structural
graph; native indexing then vectorizes the documents. This proves headings and
containment, not model-inferred semantic graph edges. Public APIs indexes the
actual API/description/authentication/HTTPS table. Search a topic, read the selected
API's linked documentation, then configure that API's own endpoint and credential
through its package setup. Collection URLs are data: indexing never follows,
installs or executes their projects.

## Chatterbox target-node wizard

The Chatterbox recipe ships a small OpenAI-compatible wrapper around the pinned
`ChatterboxTTS.generate` interface plus `.pandamonium/SETUP.md`. Deploy that source
in a dedicated environment on the chosen private node (upstream Python/dependency,
hardware and model-access requirements apply). Add the service's `/v1` address
as a TTS endpoint in Settings, then select that deployment node in Plugins setup.
The dropdown uses existing connections and never copies credentials into packages.
No VM provisioning or arbitrary remote command execution is added.

A real WAV response must pass the package's synthesis check before activation
selects that endpoint/model in voice Settings. Settings remain editable. Removing
the plugin preserves the explicitly selected external endpoint and voice setting;
change/remove them in Settings when retiring that node. The wrapper retains the
upstream watermark. Missing dependencies, GPU/model access, authentication or
availability remain Needs setup; a working GPU inference deployment is not claimed
by the deterministic speech-transport fixture.

## Proof and reproduction

[Source/package/operation receipts](mad-960-package-corpus.json) distinguish real
source execution from reviewed semantic proposal fixtures. Disposable owners/data
were removed. No packages were installed into Leo's account.

| Source package | Actual result |
|---|---|
| myinstants-api `435d40d3` | Private checksum-verified PHP 8.5.10 service; real recent-sound API returned 10 records; native discovery/mount/call; disable/enable/remove. The private source copy restores upstream TLS verification. Recipe currently requires Linux x86_64 with compatible glibc. |
| build-your-own-x `aa17439b` + Graphify `c7ec1082` | Real Graphify Markdown extraction: 36 nodes / 35 edges. 27 indexed chunks, actual FastEmbed MiniLM vectors, source-cited search and refresh; native lifecycle. |
| public-apis `536d5c4e` | 148 indexed chunks; topic search returns authentication/documentation rows with pinned source citations; refresh and lifecycle. No links followed. |
| Chatterbox `5de7a54a` | Source-evidenced generated client/server recipe; encrypted endpoint reference and voice activation/rollback checks. Speech transport fixture is a real authenticated HTTP/WAV response, not model inference. |

```sh
sh scripts/check_package_runtimes.sh .venv/bin/python -m pytest -q tests/test_extension_package_runtimes.py tests/test_extension_cli_adapter.py
sh scripts/check_package_runtimes.sh .venv/bin/python scripts/validate_cli_package.py --recipe integrations/repository-packages/myinstants-api/proposal.json
sh scripts/check_package_runtimes.sh .venv/bin/python scripts/validate_cli_package.py --recipe integrations/repository-packages/build-your-own-x/proposal.json
sh scripts/check_package_runtimes.sh .venv/bin/python scripts/validate_cli_package.py --recipe integrations/repository-packages/public-apis/proposal.json
```

FastEmbed is already a core dependency. The local validation installed the missing
test-environment dependency into a disposable `/tmp` target, downloaded the real
MiniLM model into disposable app data, and removed it afterwards. Host/app dependencies
and operator embedding configuration were unchanged.

The focused checks cover owner/credential isolation, source attribution, model
changes, path confinement, stale approval invalidation, missing prerequisites,
actual service calls, restart, failed upgrade, same-source replacement/rollback,
partial runtime cleanup, cancellation and the kernel task ceiling. Existing native
skills/assets, workspace and Unsloth tests cover their reused paths.

## Running-app and check results

Authenticated uvicorn/browser proof used a new disposable admin and the actual
Chatterbox source scan/package. Saving the selected endpoint produced a fresh
approval; the real HTTP/WAV transport check passed and installed one native tool.
Voice settings read back `endpoint:disposable-speech-node`, model `chatterbox`,
enabled. [Sanitized speech setup receipt](mad-960-speech-setup.json). The fixture
returned deterministic audio; it does not prove a Chatterbox model/GPU deployment.

The browser exposed a stale-approval bug during setup changes. Saving now cancels
pending decisions, revokes prior receipts and invalidates old plans before a new
preview. Configuration fingerprints also bind connection changes to revalidation.
The regression verifies that the fresh preview can be approved and executed.

[Desktop setup](screenshots/mad960-setup-desktop.png) and
[mobile setup](screenshots/mad960-setup-mobile.png) show the editable target selector
and existing lifecycle controls in the running app.

Local verification: **6,456 Python passed / 5 skipped**, **183 Chromium passed**,
52 focused runtime/Skills/Unsloth/agent/setup checks, scoped Ruff/mypy and isort
(Black profile), Node error-copy and syntax checks, and diff hygiene. Follow-up
configuration-binding and knowledge-cancellation checks run against the final
source; protected CI is the final-head gate. No personal install, catalog publish,
release or service deployment occurred.

Rollback: revert this scoped source commit, retaining the declared runtime/user
data and encrypted connection records. The prior CLI runtime lacks aggregate
resource enforcement; do not enable package execution after a source rollback
until the replacement resource gate is present.
