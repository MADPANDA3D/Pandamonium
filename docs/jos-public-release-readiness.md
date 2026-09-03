# Jarvis OS Public Release Readiness

**Linear:** `MAD-756`

**Decision:** READY — public source release gate passed

**Assessment basis:** Pandamonium `jarvis-os-v1.0.0` at `ee470206`, ORACLE `b619e2a`, Barehands
candidate `0ef7c9a`, img2threejs candidate `54734b5`, text-to-cad candidate
`fd444ccf`, Robin candidate `8d4b410`

Leo selected the existing Pandamonium repository and native Git archives plus
SHA-256 checksums. The public release is
`https://github.com/MADPANDA3D/Pandamonium/releases/tag/jarvis-os-v1.0.0`.
This certifies public source distribution and temporary package installation;
it is not a deployment or live-provider/hardware acceptance claim.

## What is ready

- P0-P7 have generic, tested source implementations around Pandamonium's native
  identity, context, memory, action, authority, learning, and trace paths.
- JOS-EXT-1 has a strict manifest, registry, approval-gated pinned Git
  lifecycle, reversible state, external web live-catalog host, and native
  skill/plugin-bundle adapter through the existing skill manager, plus a
  generic extension-ID-keyed browser surface and correlated result bridge and
  a generic native MCP lifecycle adapter.
- Actual local ORACLE commit `b619e2a` registered all 28 native schemas through
  that generic catalog path in a temporary managed root; disable removed all
  28. Nothing was installed persistently or deployed.
- The Barehands candidate rooted at upstream
  `c6106cac49ecc6a6182c55746a95095888281f73` passed its candidate-owned
  manifest/catalog/bridge and temporary generic lifecycle proof through commit
  `bcddc23`; its verified readiness record is `0ef7c9a`. It is published from
  the maintained fork as immutable tag `jos-v0.1.0` and remains undeployed.
- The img2threejs candidate rooted at upstream
  `9fbd0ca5bbcc3b13bebe712745d6784d33db0b85` passed its strict manifest,
  bounded offline package, failure boundaries, and temporary generic native
  skill lifecycle through candidate commit `64163cf`. It is published as
  immutable tag `jos-v1.5.1-jos.2` and remains undeployed.
- The text-to-cad candidate rooted at upstream
  `0e94cd1d2b5fa2013d89aa9504ecadcf16ce39f6` passed its strict two-skill
  projection, deterministic offline STEP fixture, correlated loopback viewer
  handoff, failure boundaries, and temporary generic native skill lifecycle
  through candidate commit `fd444ccf`. It is published as immutable tag
  `jos-v0.4.28-jos.2` and remains undeployed.
- The Robin candidate rooted at upstream
  `575d105e2f0fd61a450d5b4368535d0e83060354` passed its strict fixture-only
  MCP contract, hostile-data/receipt boundaries, two-revision native MCP
  lifecycle, and differently named coexistence proof through candidate commit
  `b104530`. It is published as immutable tag `jos-v2.8.0-jos.2`; no live
  network, Tor, onion, search-engine, or model traffic occurred.
- The proof exposed and fixed one generic stdio context-owner gap at Pandamonium
  `89fb97ce`; the reference-neutral Quartz regression proves clean native MCP
  teardown without adding a client, transport, daemon, or dependency.
- One combined temporary-host proof installed Barehands `0ef7c9a`, img2threejs
  `54734b5`, text-to-cad `fd444ccf`, and Robin `8d4b410` through the same
  approval-gated pinned Git lifecycle. All four repository classes coexisted;
  after Barehands became unavailable and was uninstalled, Pandamonium and the
  other three candidates remained healthy.
- Public tag replay passed for ORACLE and all four candidates. The combined
  temporary lifecycle installed all four candidate tags, exercised Robin's
  fixture-only action, and preserved isolation after Barehands unavailability.
- All six archives rebuilt byte-identically. GitHub asset digests and the
  downloaded `SHA256SUMS` match. Fresh extraction/setup passed 11 public-
  default/schema checks with a generic administrator and empty extension state.
- The complete Pandamonium suite passes at release commit `ee470206`: 5,024
  passed, 5 intentional skips, zero failures in 123.84 seconds.
- Source, push, install, deployment, and live-acceptance claims are recorded as
  separate states.

## Gate findings

| Release criterion | State | Evidence / remaining work |
| --- | --- | --- |
| Clean installation has no private identity, organization, path, host, credential, memory, or required ORACLE values | Met | The downloaded Pandamonium archive excludes local state and private defaults. Fresh setup used a generic administrator/new data root, an empty extension root, and no private runtime value. |
| Onboarding configures identity/constitution and two model endpoints without code changes | Met | The package-installed clean-room check configures a differently named identity/constitution and two loopback endpoints through existing settings seams with no credential, extension, network, or source edit. |
| Extension lifecycle is documented and tested from public sources | Met | The maintained public tags resolve to the exact five extension commits. Public-tag ORACLE contract checks and the combined four-candidate approval-gated lifecycle replay pass. |
| Attribution, licenses, fork lineage, and compatible revisions are preserved | Met | The release manifest records maintained/upstream URLs, licenses, exact commits, tags, archives, and digests. Every archive retains its license; AGPL source and network-source obligations are documented. |
| Compatibility tables distinguish source-tested, installed, and live-accepted states | Met | The compatibility matrix records exact source revision, package-installed tag/receipt, and live-accepted state separately. Fixture proof is not promoted to live acceptance. |
| Security review covers repository, lifecycle, credential, capability, drift, and rollback risks | Met | `jos-public-operations.md` records all six risk classes and fail-closed controls; downloaded assets, archive contents, tag resolutions, and SHA-256 digests were verified. |
| Release artifacts are immutable and reproducible | Met | Six annotated tags resolve to exact commits. Two independent native Git archive builds were byte-identical, and GitHub's eight asset digests match the release receipt. No mutable branch is an installed version. |
| Fresh install passes without private infrastructure or optional extensions | Met | Downloaded assets passed checksum verification; fresh archive extraction/setup created new local state with no installed extension/private topology and passed 11 public-default/schema checks. |

## Remaining live-acceptance boundaries

1. ORACLE real-provider/deployed equivalence remains separate from this public
   source release.
2. Robin live Tor/onion/search/model acceptance still requires its documented
   lawful-use, isolation, egress, provenance, and explicit approval gates.
3. Hardware, CT103/CT104, persistent installation, and deployment remain out of
   scope and were not exercised.

## Release result

`MAD-761`, `MAD-762`, `MAD-763`, `MAD-758`, `MAD-757`, `MAD-759`, `MAD-760`, and
the combined `MAD-755` compatibility proof are source-complete. The shared host
batons added no daemon, package manager, model, dependency, or second registry.
Barehands, img2threejs, text-to-cad, Robin, and ORACLE remain optional
independently maintained extensions. The existing Pandamonium repository owns the
central source release, component archive set, release manifest, and checksum
receipt. No second registry, installer, package manager, dependency, daemon,
transport, deployment, or persistent install was added.

The truthful release state is: public immutable source distribution and
temporary package-install acceptance complete; live provider, external data,
hardware, and deployed acceptance remain explicitly **No**.
