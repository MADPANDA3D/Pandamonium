# Jarvis OS Public Release Readiness

**Linear:** `MAD-756`

**Decision:** NOT READY — release gate closed

**Assessment basis:** Odysseus `879a3463`, ORACLE `b619e2a`, Barehands
candidate `0ef7c9a`, img2threejs candidate `64163cf`

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
- The local img2threejs candidate rooted at upstream
  `9fbd0ca5bbcc3b13bebe712745d6784d33db0b85` passed its strict manifest,
  bounded offline package, failure boundaries, and temporary generic native
  skill lifecycle through candidate commit `64163cf`. Its public fork exists,
  but candidate commits were not pushed, installed persistently, deployed,
  packaged, or released.
- The complete Odysseus suite passes: 5,014 passed, 4 intentional skips, zero
  failures.
- Source, push, install, deployment, and live-acceptance claims are recorded as
  separate states.

## Gate findings

| Release criterion | State | Evidence / remaining work |
| --- | --- | --- |
| Clean installation has no private identity, organization, path, host, credential, memory, or required ORACLE values | Not met | Generic identity defaults exist, but committed runtime/UI paths still contain private worker labels/topology, and `services/pc-codex-bridge/README.md` contains private path examples. Test-only negative fixtures and upstream provenance must be classified separately from install payload. |
| Onboarding configures identity/constitution and two model endpoints without code changes | Partial | Identity/constitution and model endpoint settings exist and are tested in source. A clean public onboarding run with two differently named compatible endpoints has not been executed. |
| Extension lifecycle is documented and tested from public sources | Partial | Lifecycle behavior is documented and extensively tested with temporary Git sources, including direct and partial multi-skill native admission; actual ORACLE, Barehands, and img2threejs candidate revisions were proven through temporary managed roots. Clean public-source proofs for text-to-cad and Robin remain. |
| Attribution, licenses, fork lineage, and compatible revisions are preserved | Partial | Odysseus upstream remote/license, ORACLE fork point/revision, Barehands upstream/candidate lineage, and the Apache-2.0 img2threejs fork base are recorded. Barehands documents AGPL-3.0-or-later obligations; remaining candidate/release obligations stay open. |
| Compatibility tables distinguish source-tested, installed, and live-accepted states | Partial | The matrix records Barehands and img2threejs as local source-proven while distinguishing public fork creation from unpushed candidate commits and from persistent install/deploy/release. Text-to-cad and Robin lack equivalent proof. |
| Security review covers repository, lifecycle, credential, capability, drift, and rollback risks | Partial | Manifest/registry/installer/live-catalog/skill-bundle/browser-surface/MCP rules fail closed and are tested. Barehands adds its browser/server boundaries; img2threejs adds fixed project paths, strict-quality/state/result checks, bounded subprocesses, offline default, optional-vision timeout, and failure isolation. Remaining candidate and clean-package reviews remain. |
| Release artifacts are immutable and reproducible | Not met | No distribution repository or packaging strategy has been selected. No compatible release tags/artifacts were created; mutable branches are not accepted as install versions. |
| Fresh install passes without private infrastructure or optional extensions | Not met | No public clean-room acceptance run has been performed. |

## Confirmed blockers

1. `MAD-755` still lacks pinned candidate proofs for text-to-cad and Robin,
   followed by the combined coexistence/failure matrix. Barehands and
   img2threejs now have local temporary lifecycle and action/workflow proofs.
2. Public defaults/onboarding still need a path-limited portability pass over
   private worker names, direct operator wording, workspace examples, and
   optional topology. These become installation configuration, not deleted
   functionality or public defaults.
3. Leo must explicitly select the packaging/distribution and release-repository
   strategy before repository creation, tagging, publishing, or pushing.

## Next baton order

`MAD-761`, `MAD-762`, `MAD-763`, `MAD-758`, and `MAD-757` are source-complete.
The shared host batons added no daemon, package manager, model, dependency, or
second registry. Barehands and img2threejs remain optional local candidates
rather than release artifacts. The next child proof is `MAD-759`.

Recommended sequence:

1. `MAD-759` text-to-cad with only `cad` and `cad-viewer` admitted;
2. isolated/mock-first `MAD-760` Robin;
3. finish the coexistence/failure matrix and close `MAD-755`;
4. return to `MAD-756` for public-default sanitation, clean-room acceptance,
   explicit packaging selection, and only then immutable release artifacts.

Until those gates pass, the truthful release state is: strong generic protocol
source, one reference extension plus two optional candidates proven locally,
and no public distribution claim for candidate changes or Jarvis OS.
