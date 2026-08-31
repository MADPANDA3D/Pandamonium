# Jarvis OS Public Release Readiness

**Linear:** `MAD-756`

**Decision:** NOT READY — release gate closed

**Assessment basis:** Odysseus `13fb3209`, ORACLE `b619e2a`

This is a source-state assessment, not authorization to create a distribution
repository, package, tag, release, push, install, or deployment. `MAD-756`
remains Backlog and is blocked by the incomplete `MAD-755` compatibility proof.

## What is ready

- P0-P7 have generic, tested source implementations around Odysseus's native
  identity, context, memory, action, authority, learning, and trace paths.
- JOS-EXT-1 has a strict manifest, registry, approval-gated pinned Git
  lifecycle, reversible state, external web live-catalog host, and native
  skill/plugin-bundle adapter through the existing skill manager, plus a
  generic extension-ID-keyed browser surface and correlated result bridge.
- Actual local ORACLE commit `b619e2a` registered all 28 native schemas through
  that generic catalog path in a temporary managed root; disable removed all
  28. Nothing was installed persistently or deployed.
- The complete Odysseus suite passes: 5,005 passed, 4 intentional skips, zero
  failures.
- Source, push, install, deployment, and live-acceptance claims are recorded as
  separate states.

## Gate findings

| Release criterion | State | Evidence / remaining work |
| --- | --- | --- |
| Clean installation has no private identity, organization, path, host, credential, memory, or required ORACLE values | Not met | Generic identity defaults exist, but committed runtime/UI paths still contain private worker labels/topology, and `services/pc-codex-bridge/README.md` contains private path examples. Test-only negative fixtures and upstream provenance must be classified separately from install payload. |
| Onboarding configures identity/constitution and two model endpoints without code changes | Partial | Identity/constitution and model endpoint settings exist and are tested in source. A clean public onboarding run with two differently named compatible endpoints has not been executed. |
| Extension lifecycle is documented and tested from public sources | Partial | Lifecycle behavior is documented and extensively tested with temporary Git sources, including direct and partial multi-skill native admission; actual ORACLE was proven from the local fork. A clean public-source install/upgrade/rollback/uninstall run is still absent. |
| Attribution, licenses, fork lineage, and compatible revisions are preserved | Partial | Odysseus upstream remote/license and ORACLE fork point/revision are recorded. Candidate fork histories and distribution obligations do not exist yet; Barehands adds an AGPL-3.0 review gate. |
| Compatibility tables distinguish source-tested, installed, and live-accepted states | Partial | The source-level status and candidate matrix make the distinctions explicit. Candidate package-installed and live-accepted evidence does not exist. |
| Security review covers repository, lifecycle, credential, capability, drift, and rollback risks | Partial | Manifest/registry/installer/live-catalog/skill-bundle/browser-surface rules fail closed and are tested. MCP, candidate license, OSINT isolation, and clean-package threat reviews remain. |
| Release artifacts are immutable and reproducible | Not met | No distribution repository or packaging strategy has been selected. No compatible release tags/artifacts were created; mutable branches are not accepted as install versions. |
| Fresh install passes without private infrastructure or optional extensions | Not met | No public clean-room acceptance run has been performed. |

## Confirmed blockers

1. `MAD-755` still lacks pinned candidate installs, one real action per
   candidate, cross-extension failure isolation, rollback, removal, and clean
   reinstall proof.
2. `MAD-763` must add a generic MCP runtime adapter using the existing
   Odysseus MCP subsystem before `MAD-760` Robin.
3. Public defaults/onboarding still need a path-limited portability pass over
   private worker names, direct operator wording, workspace examples, and
   optional topology. These become installation configuration, not deleted
   functionality or public defaults.
4. Leo must explicitly select the packaging/distribution and release-repository
   strategy before repository creation, tagging, publishing, or pushing.

## Next baton order

`MAD-761` and `MAD-762` are source-complete. They added no daemon, package
manager, model, dependency, second registry, or real candidate. The next
generic prerequisite is `MAD-763`.

Recommended sequence:

1. `MAD-763` generic MCP runtime;
2. `MAD-757` img2threejs offline fixture;
3. `MAD-759` text-to-cad with only `cad` and `cad-viewer` admitted;
4. `MAD-758` Barehands;
5. isolated/mock-first `MAD-760` Robin;
6. finish the coexistence/failure matrix and close `MAD-755`;
7. return to `MAD-756` for public-default sanitation, clean-room acceptance,
   explicit packaging selection, and only then immutable release artifacts.

Until those gates pass, the truthful release state is: strong generic protocol
source, one reference extension proven locally, no public distribution claim.
