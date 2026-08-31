# Jarvis OS Public Release Readiness

**Linear:** `MAD-756`

**Decision:** NOT READY — release gate closed

**Assessment basis:** Odysseus `dfe098be`, ORACLE `b619e2a`, Barehands
candidate `0ef7c9a`

This is a source-state assessment, not authorization to create a distribution
repository, package, tag, release, push, install, or deployment. `MAD-756`
remains Backlog and is blocked by the incomplete `MAD-755` compatibility proof.

## What is ready

- P0-P7 have generic, tested source implementations around Odysseus's native
  identity, context, memory, action, authority, learning, and trace paths.
- JOS-EXT-1 has a strict manifest, registry, approval-gated pinned Git
  lifecycle, reversible state, external web live-catalog host, and native
  skill/plugin-bundle adapter through the existing skill manager, plus a
  generic extension-ID-keyed browser surface and correlated result bridge and
  a generic native MCP lifecycle adapter.
- Actual local ORACLE commit `b619e2a` registered all 28 native schemas through
  that generic catalog path in a temporary managed root; disable removed all
  28. Nothing was installed persistently or deployed.
- The local Barehands candidate rooted at upstream
  `c6106cac49ecc6a6182c55746a95095888281f73` passed its candidate-owned
  manifest/catalog/bridge and temporary generic lifecycle proof through commit
  `bcddc23`; its verified readiness record is `0ef7c9a`. It was not pushed,
  published, deployed, released, or persistently installed.
- The complete Odysseus suite passes: 5,014 passed, 4 intentional skips, zero
  failures.
- Source, push, install, deployment, and live-acceptance claims are recorded as
  separate states.

## Gate findings

| Release criterion | State | Evidence / remaining work |
| --- | --- | --- |
| Clean installation has no private identity, organization, path, host, credential, memory, or required ORACLE values | Not met | Generic identity defaults exist, but committed runtime/UI paths still contain private worker labels/topology, and `services/pc-codex-bridge/README.md` contains private path examples. Test-only negative fixtures and upstream provenance must be classified separately from install payload. |
| Onboarding configures identity/constitution and two model endpoints without code changes | Partial | Identity/constitution and model endpoint settings exist and are tested in source. A clean public onboarding run with two differently named compatible endpoints has not been executed. |
| Extension lifecycle is documented and tested from public sources | Partial | Lifecycle behavior is documented and extensively tested with temporary Git sources, including direct and partial multi-skill native admission; actual ORACLE and the local Barehands candidate were proven through temporary managed roots. Clean public-source proofs for the remaining candidates are absent. |
| Attribution, licenses, fork lineage, and compatible revisions are preserved | Partial | Odysseus upstream remote/license, ORACLE fork point/revision, and Barehands upstream/candidate lineage are recorded. Barehands documents AGPL-3.0-or-later conveyance and network-source obligations, but its candidate fork has not been published. Other candidate obligations remain open. |
| Compatibility tables distinguish source-tested, installed, and live-accepted states | Partial | The matrix now records Barehands as local source-proven and explicitly not pushed, published, deployed, released, or persistently installed. The remaining candidates lack equivalent proof. |
| Security review covers repository, lifecycle, credential, capability, drift, and rollback risks | Partial | Manifest/registry/installer/live-catalog/skill-bundle/browser-surface/MCP rules fail closed and are tested. Barehands adds candidate-owned loopback, note/media jail, timeout/result, client-side camera, and failure-isolation proof. Remaining candidate and clean-package reviews remain. |
| Release artifacts are immutable and reproducible | Not met | No distribution repository or packaging strategy has been selected. No compatible release tags/artifacts were created; mutable branches are not accepted as install versions. |
| Fresh install passes without private infrastructure or optional extensions | Not met | No public clean-room acceptance run has been performed. |

## Confirmed blockers

1. `MAD-755` still lacks pinned candidate proofs for img2threejs, text-to-cad,
   and Robin, followed by the combined coexistence/failure matrix. Barehands
   now has its local temporary lifecycle and one-action proof.
2. Public defaults/onboarding still need a path-limited portability pass over
   private worker names, direct operator wording, workspace examples, and
   optional topology. These become installation configuration, not deleted
   functionality or public defaults.
3. Leo must explicitly select the packaging/distribution and release-repository
   strategy before repository creation, tagging, publishing, or pushing.

## Next baton order

`MAD-761`, `MAD-762`, `MAD-763`, and `MAD-758` are source-complete. The shared
host batons added no daemon, package manager, model, dependency, or second
registry. Barehands remains an optional local candidate rather than a release
artifact. The next child proof is `MAD-757`.

Recommended sequence:

1. `MAD-757` img2threejs offline fixture;
2. `MAD-759` text-to-cad with only `cad` and `cad-viewer` admitted;
3. isolated/mock-first `MAD-760` Robin;
4. finish the coexistence/failure matrix and close `MAD-755`;
5. return to `MAD-756` for public-default sanitation, clean-room acceptance,
   explicit packaging selection, and only then immutable release artifacts.

Until those gates pass, the truthful release state is: strong generic protocol
source, one reference extension plus one optional candidate proven locally,
and no public distribution claim.
