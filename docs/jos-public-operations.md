# Jarvis OS Public Operations

**Linear:** `MAD-756`

This guide documents public configuration and extension operations for the
published `jarvis-os-v1.0.0` source distribution. It is not a deployment or
live-provider acceptance claim.

## Claim vocabulary

- **Source-tested** means a pinned revision passed offline tests or a temporary
  managed lifecycle using local source. It does not imply a published package.
- **Package-installed** means an immutable published artifact was installed and
  its digest or exact tag was verified. `jarvis-os-v1.0.0` and its compatible
  extension tags have this state.
- **Live-accepted** means the package-installed revision passed its authorized
  real-provider or hardware acceptance gate. Source fixtures and temporary
  checkouts do not qualify.

Mutable branches are development inputs only. They are never installed-version
identifiers.

The canonical tag, component revisions, licenses, reproducible archive
commands, checksums, and clean-install receipt are recorded in
[`jos-public-release-v1.0.0.md`](jos-public-release-v1.0.0.md).

## Clean onboarding

1. Start with a new data directory and no worker, extension, credential, or
   private-topology environment values.
2. Sign in as the generated admin and set the assistant ID, display name,
   constitution, and constitution version in **Settings**. The same values are
   available through the admin-only `POST /api/auth/settings` route.
3. Add at least two differently named OpenAI-compatible model endpoints in
   **Settings -> Models** or through `POST /api/model-endpoints`. Endpoint URLs,
   credentials, selected models, and ownership are installation configuration;
   they are not source defaults.
4. Keep API keys runtime-only. Empty keys are valid for local endpoints that do
   not require authentication.
5. Confirm the extension registry is empty and that no optional worker workspace
   is exposed unless `ODYSSEUS_WORKER_WORKSPACES_JSON` explicitly declares it.

The offline source check is:

```bash
.venv/bin/python -m pytest -q tests/test_public_release_defaults.py
```

It creates a temporary identity, two loopback model endpoints, an empty
extension registry, and no credential or private-infrastructure value. It also
proves malformed worker topology fails closed. This is source-tested clean-room
evidence, not a package-install receipt.

## Extension lifecycle

Every operation is previewed, reviewed, approved, and then executed through the
existing P4/P5/P7 paths:

1. Preview install or upgrade with `POST /api/extensions/plans/source` using a
   canonical public HTTPS Git URL and a tag or full revision.
2. Review the resolved full commit, manifest, requested permissions, data and
   network boundaries, lifecycle vectors, health contract, and rollback state.
3. Resolve the returned authority decision through the existing approval route.
4. Execute exactly that plan with
   `POST /api/extensions/plans/{plan_id}/execute`.
5. Preview enable, disable, rollback, or recoverable uninstall with
   `POST /api/extensions/plans/lifecycle`, approve the exact decision, and
   execute the returned plan.
6. Read back `GET /api/extensions`. Disabled or uninstalled extensions expose
   no catalog, context, or tools; failed upgrades retain the prior revision.

The installer never runs arbitrary repository setup scripts. Runtime adapters
are reviewed Odysseus code, lifecycle vectors remain empty for current native
adapters, Git never uses a shell or interactive credentials, and the accepted
source revision is immutable.

## Security review

| Risk | Required control | Current source evidence |
| --- | --- | --- |
| Untrusted repositories | Canonical HTTPS hosts, bounded checkout, no submodules/LFS smudge, strict manifest and regular-file checks | `src/extension_installer.py`, `tests/test_extension_installer.py` |
| Lifecycle commands | Manifest text cannot create a runner; current adapters require empty command vectors | `src/extension_installer.py`, `specs/jarvis-os-extension-protocol.md` |
| Credentials | No credentials in manifests, registry, prompts, diagnostics, or approval records; runtime config owns secrets | `src/extension_registry.py`, `src/extension_mcp_adapter.py` |
| Capability escalation | Live schemas reconcile with declared permissions and the current turn policy; unknown or drifted capabilities fail closed | `src/extension_registry.py`, `src/authority_protocol.py` |
| Update drift | Every preview resolves to a full commit; activation rechecks identity, revision, health, and catalog | `src/extension_installer.py`, `src/extension_mcp_adapter.py` |
| Rollback failure | Prior revisions remain retained; failed upgrade preserves the active revision; uninstall is recoverable | `src/extension_installer.py`, `tests/test_extension_installer.py` |

Extension output, descriptions, schemas, browser messages, MCP content, and
retrieved text remain hostile data. Engagement never grants filesystem, action,
media, credential, or network authority.

## Attribution and release records

A distributable release record must contain, for Odysseus and every included
extension:

- upstream repository URL, license, and required notices;
- maintained-fork URL and preserved upstream history;
- exact compatible source commit and immutable release tag;
- artifact digest and reproducible build command;
- source-tested, package-installed, and live-accepted state recorded separately;
- any source-offer, network-source, copyleft, or redistribution obligation.

The compatible revisions and license findings are recorded in
[`jos-extension-compatibility-matrix.md`](jos-extension-compatibility-matrix.md).
