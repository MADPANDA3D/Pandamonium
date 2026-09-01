# Jarvis OS Public Release Readiness

**Linear:** `MAD-756`

**Decision:** NOT READY — release gate closed

**Assessment basis:** Odysseus `ca3f7177`, ORACLE `b619e2a`, Barehands
candidate `0ef7c9a`, img2threejs candidate `54734b5`, text-to-cad candidate
`fd444ccf`, Robin candidate `8d4b410`

This is a source-state assessment, not authorization to create a distribution
repository, package, tag, release, push, install, or deployment. `MAD-756`
remains Backlog with its clean-room, public-default, packaging, and immutable-
artifact gates still closed. The local source compatibility proof in `MAD-755`
is complete; it is not a public distribution claim.

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
- The local text-to-cad candidate rooted at upstream
  `0e94cd1d2b5fa2013d89aa9504ecadcf16ce39f6` passed its strict two-skill
  projection, deterministic offline STEP fixture, correlated loopback viewer
  handoff, failure boundaries, and temporary generic native skill lifecycle
  through candidate commit `fd444ccf`. Its public fork exists, but candidate
  commits were not pushed, installed persistently, deployed, packaged,
  published, or released.
- The local Robin candidate rooted at upstream
  `575d105e2f0fd61a450d5b4368535d0e83060354` passed its strict fixture-only
  MCP contract, hostile-data/receipt boundaries, two-revision native MCP
  lifecycle, and differently named coexistence proof through candidate commit
  `b104530`. Its public fork exists, but candidate commits were not pushed;
  no live network, Tor, onion, search-engine, or model traffic occurred.
- The proof exposed and fixed one generic stdio context-owner gap at Odysseus
  `89fb97ce`; the reference-neutral Quartz regression proves clean native MCP
  teardown without adding a client, transport, daemon, or dependency.
- One combined temporary-host proof installed Barehands `0ef7c9a`, img2threejs
  `54734b5`, text-to-cad `fd444ccf`, and Robin `8d4b410` through the same
  approval-gated pinned Git lifecycle. All four repository classes coexisted;
  after Barehands became unavailable and was uninstalled, Odysseus and the
  other three candidates remained healthy.
- The complete Odysseus suite passes: 5,016 passed, 4 intentional skips, zero
  failures.
- Source, push, install, deployment, and live-acceptance claims are recorded as
  separate states.

## Gate findings

| Release criterion | State | Evidence / remaining work |
| --- | --- | --- |
| Clean installation has no private identity, organization, path, host, credential, memory, or required ORACLE values | Not met | Generic identity defaults exist, but committed runtime/UI paths still contain private worker labels/topology, and `services/pc-codex-bridge/README.md` contains private path examples. Test-only negative fixtures and upstream provenance must be classified separately from install payload. |
| Onboarding configures identity/constitution and two model endpoints without code changes | Partial | Identity/constitution and model endpoint settings exist and are tested in source. A clean public onboarding run with two differently named compatible endpoints has not been executed. |
| Extension lifecycle is documented and tested from public sources | Partial | Lifecycle behavior is documented and extensively tested with temporary Git sources, including direct and partial multi-skill native admission; actual ORACLE, Barehands, img2threejs, text-to-cad, and Robin candidate revisions were proven through temporary managed roots. Candidate commits remain unpushed, so a clean public-source replay is still open. |
| Attribution, licenses, fork lineage, and compatible revisions are preserved | Partial | Odysseus upstream remote/license, ORACLE fork point/revision, Barehands upstream/candidate lineage, the Apache-2.0 img2threejs fork base, and the MIT text-to-cad and Robin fork bases are recorded. Barehands documents AGPL-3.0-or-later obligations; remaining candidate/release obligations stay open. |
| Compatibility tables distinguish source-tested, installed, and live-accepted states | Partial | The matrix records Barehands, img2threejs, text-to-cad, and Robin as local source-proven while distinguishing public fork creation from unpushed candidate commits and from persistent install/deploy/release. Robin live-network acceptance remains explicitly closed. |
| Security review covers repository, lifecycle, credential, capability, drift, and rollback risks | Partial | Manifest/registry/installer/live-catalog/skill-bundle/browser-surface/MCP rules fail closed and are tested. Barehands adds its browser/server boundaries; img2threejs adds fixed project paths, strict-quality/state/result checks, bounded subprocesses, offline default, optional-vision timeout, and failure isolation; text-to-cad adds selected-project confinement, deterministic STEP/hash validation, bounded viewer output/timeout, loopback-only correlation, and preserved-artifact failure isolation; Robin adds fixture-only execution, onion-only quoted provenance, prompt-injection/secret/SSRF/size/time rejection, explicit retention, and a separately closed lawful-use/live-network gate. Remaining clean-package review stays open. |
| Release artifacts are immutable and reproducible | Not met | No distribution repository or packaging strategy has been selected. No compatible release tags/artifacts were created; mutable branches are not accepted as install versions. |
| Fresh install passes without private infrastructure or optional extensions | Not met | No public clean-room acceptance run has been performed. |

## Confirmed blockers

1. Public defaults/onboarding still need a path-limited portability pass over
   private worker names, direct operator wording, workspace examples, and
   optional topology. These become installation configuration, not deleted
   functionality or public defaults.
2. Leo must explicitly select the packaging/distribution and release-repository
   strategy before repository creation, tagging, publishing, or pushing.

## Next baton order

`MAD-761`, `MAD-762`, `MAD-763`, `MAD-758`, `MAD-757`, `MAD-759`, `MAD-760`, and
the combined `MAD-755` compatibility proof are source-complete. The shared host
batons added no daemon, package manager, model, dependency, or second registry.
Barehands, img2threejs, text-to-cad, and Robin remain optional local candidates
rather than release artifacts. The next one-issue baton is `MAD-756`.

Recommended sequence:

1. run `MAD-756` public-default sanitation and clean-room acceptance;
2. obtain explicit packaging selection before creating immutable release
   artifacts.

Until those gates pass, the truthful release state is: strong generic protocol
source, one reference extension plus four optional candidates proven locally,
and no public distribution claim for candidate changes or Jarvis OS.
