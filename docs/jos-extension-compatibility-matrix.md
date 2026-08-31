# JOS-EXT-1 Compatibility Matrix

**Linear:** `MAD-755`

This matrix distinguishes inventory-only candidates from source-proven local
candidates. Candidate source, manifests, catalogs, and native actions stay in
their maintained candidate repositories; candidate names and URLs do not
belong in Odysseus core dispatch. No candidate has been pushed, deployed,
published, released, or persistently installed by these batons.

## Current host boundary

The source host at Odysseus commit `879a3463` supports:

- strict manifests and a durable, fail-closed capability registry;
- pinned Git checkout plus approval-gated reversible lifecycle;
- static web manifests with inline schemas and no lifecycle commands;
- configured external web runtimes with bounded live catalogs and no
  lifecycle commands;
- reviewed single-skill Agent Skill repositories and explicitly selected
  subsets of multi-skill Codex plugin repositories through the existing native
  skill manager; and
- generic P2-P7 metadata, permissions, action envelopes, result correlation,
  disable, and unavailable behavior after a tool reaches the model loop; and
- an extension-ID-keyed browser surface resolved from the installed manifest
  entry point and configured runtime origin, with exact frame/origin/call/tool/
  owner correlation and explicit browser-only media permissions; and
- a reference-neutral MCP lifecycle adapter over the existing native MCP
  manager, with pinned stdio entrypoints or configured loopback transport,
  identity/catalog reconciliation, bounded calls, restart restoration, and
  fail-closed ordinary-catalog isolation.

ORACLE's legacy message normalizer and unregistered fallback are deliberately
retained until source and deployed equivalence are separately proven.

## Candidate decisions

| Candidate | Pinned revision | License | Native surface to reuse | Compatibility decision | Child baton |
| --- | --- | --- | --- | --- | --- |
| [img2threejs](https://github.com/img2threejs/img2threejs/tree/9fbd0ca5bbcc3b13bebe712745d6784d33db0b85) | `9fbd0ca5bbcc3b13bebe712745d6784d33db0b85` | Apache-2.0 | Root Agent Skill, Python 3.10+ stdlib `forge` gates, project state, TypeScript/spec/render artifacts | Source-proven locally: candidate-owned strict manifest, small skill package, bounded offline runner, and optional-vision timeout reuse the generic skill-bundle adapter. Fork exists; candidate commits are not pushed or released | `MAD-757` |
| [barehands](https://github.com/jaredrhod/barehands/tree/c6106cac49ecc6a6182c55746a95095888281f73) | `c6106cac49ecc6a6182c55746a95095888281f73` | AGPL-3.0-or-later | Existing stdlib loopback server, browser UI, jailed notes/media, state and allowlisted command endpoints | Source-proven locally: candidate-owned manifest/catalog/bridge reuses the generic web surface; browser camera remains client-side. Not pushed, published, deployed, or persistently installed | `MAD-758` |
| [Robin](https://github.com/apurvsinghgautam/robin/tree/575d105e2f0fd61a450d5b4368535d0e83060354) | `575d105e2f0fd61a450d5b4368535d0e83060354` | MIT | Native Python search, scrape, health, and LLM modules in an isolated runtime | Conditional: candidate-owned narrow MCP server plus generic MCP adapter; never automate Streamlit | `MAD-760` |
| [text-to-cad](https://github.com/earthtojake/text-to-cad/tree/0e94cd1d2b5fa2013d89aa9504ecadcf16ce39f6) | `0e94cd1d2b5fa2013d89aa9504ecadcf16ce39f6` | MIT | Existing Codex plugin metadata, 12 Agent Skills, deterministic CLIs, loopback viewer, artifact workflow | Conditional: admit native plugin/skill metadata through the same generic skill-bundle adapter; start with `cad` and `cad-viewer` only | `MAD-759` |

## Evidence and constraints

### img2threejs

- The maintained candidate branch starts from pinned upstream revision
  `9fbd0ca5bbcc3b13bebe712745d6784d33db0b85`. Contract, package/runtime, and
  upgrade/lifecycle proof are commits `a9e7d15`, `0e05cd1`, and `64163cf`.
- The strict manifest selects only `jos/img2threejs/SKILL.md`; the whole
  337-file repository is never copied into native skill storage. The selected
  package stays below the generic 64-file/2,000,000-byte text-only boundary.
- The stdlib runner confines reads and writes to fixed paths under one selected
  project, keeps network empty, bounds every child process, and atomically
  replaces successful TypeScript/Tier-1 artifacts. Missing, malformed, unsafe,
  late, unavailable, and failed strict-quality inputs return nonzero.
- The candidate's shared optional-vision wrapper now owns its internal timeout.
  Optional vision remains off by default and an unavailable interpreter fails
  before factory output.
- Candidate checks prove the offline fixture and failure boundaries. The real
  candidate revisions completed install, engage, disable/enable, upgrade,
  rollback, malformed-state isolation, uninstall, and reinstall through the
  generic lifecycle while a differently named reference extension stayed
  healthy and project artifacts survived.
- `.img2threejs/state.json` remains candidate/project state, not Jarvis memory.
  No generic host gap or img2threejs conditional was found in Odysseus.

### barehands

- The maintained candidate branch starts from pinned upstream revision
  `c6106cac49ecc6a6182c55746a95095888281f73`. Contract, runtime, focused
  boundary tests, and lifecycle proof are commits `d013959`, `6d1e932`,
  `1836226`, and `bcddc23`; readiness documentation is `0ef7c9a`.
- Its strict JOS manifest uses the generic live-catalog web runtime and exposes
  only `read_board_state` and `present`; the broader native command allowlist
  is not copied into Odysseus.
- `server.py` enforces loopback binding, caps request bodies, keeps notes
  read-only, and routes every source-bearing native command through the same
  configured media jail and stageable-suffix check.
- The configured note root may be an Obsidian vault. That makes it a useful UI
  surface, not a new memory authority or unrestricted filesystem reader.
- Browser mocks prove the JOS bridge never requests the camera or serializes a
  frame. Webcam tracking and MediaPipe stay in the browser; a gesture is user
  intent, never an approval receipt.
- Candidate-owned checks prove catalog/config, exact parent origin, bounded
  board-state read, one correlated present action, malformed/unknown/timeout
  failure, traversal and non-media rejection, read-only notes, client-side
  frames, clean install, disable/enable, upgrade, rollback, server-loss
  isolation, uninstall, and reinstall. A differently named reference surface
  remains registered throughout the lifecycle proof.
- `docs/JOS_EXTENSION.md` records AGPL-3.0-or-later conveyance and section 13
  network-source obligations before any publication or release. No release
  action occurred.

### Robin

- Search uses Tor SOCKS on loopback, concurrent external requests, retries,
  and long network timeouts. Scraping accepts direct HTTP(S) as well as onion
  targets and returns hostile external text.
- The first proof must use fixtures/mocked network only. Live acceptance needs
  lawful-use confirmation, isolation, egress rules, SSRF/content/size/time
  limits, provenance, secret boundaries, and explicit approval.
- Retrieved text is data. It cannot invoke a capability, alter identity or
  policy, trigger an automatic pivot, or receive promotion as memory without a
  separate trusted process.

### text-to-cad

- `.codex-plugin/plugin.json` already declares the native skill directory and
  plugin metadata. The reviewed revision contains 12 separate skills rather
  than one monolithic tool.
- The initial slice enables only `cad` and `cad-viewer`; part search, slicing,
  printer upload/start, robotics, and other hardware/network behavior remain
  absent until separately permissioned and tested.
- The generic JOS adapter now delegates explicit reviewed IDs to Odysseus's
  native skill system without replacing the global skill root or creating a
  second registry. Candidate-specific admission and action proof remain
  `MAD-759` work.
- Leo's preserved ZIP is revision
  `8d7bf1060aac9b4230fe03372c020428aff82e62`, SHA-256
  `3a8affebbc1d119b340bb1e71ea236b4470a3a432668c856072b5ea255bcc624`.
  It is evidence only; current upstream is the future install source.

## Shared host batons

Three reference-neutral host gaps were separated from candidate-native work:

1. `MAD-761` — source-complete native skill/plugin-bundle adapter using the
   existing Odysseus skill manager. A reference-neutral fixture proves direct
   Agent Skill and partial multi-skill Codex plugin admission, owner/permission
   boundaries, disable/enable, upgrade, rollback, removal, reinstall inputs,
   collision rejection, unsafe metadata/path rejection, and atomic failure
   recovery. This unlocks `MAD-757` and `MAD-759` without admitting either
   candidate here.
2. `MAD-762` — source-complete generic browser-surface mount and result bridge
   using the current session/action/result paths. A differently named fixture
   proves exact origin/ID/tool/call/owner/size correlation, single and
   multi-action results, unavailable/malformed/timeout behavior, disable,
   uninstall, restart recovery, and explicit browser-only media permission.
   ORACLE compatibility remains pending separate source/deployed equivalence.
3. `MAD-763` — source-complete generic MCP runtime adapter using the existing
   Odysseus MCP subsystem. The reference-neutral Quartz fixture proves pinned
   stdio lifecycle/action routing, identity/catalog/duplicate/unavailable
   failure isolation, bounded malformed/late/oversized results, disable,
   upgrade, rollback, removal, reinstall, restart, and secret boundaries.

Any JOS-EXT-1 schema change is written and tested first in its shared baton.
Candidate manifests, catalogs, shims, UI, data, state, and native tools remain
in their maintained candidate forks. Compatibility is not claimed until each
child baton proves clean install, one action, failure isolation, disable,
upgrade, rollback, removal, reinstall, and coexistence through the same generic
host lifecycle.

MAD-761 verification: 33 focused extension/context tests passed, followed by
the complete Odysseus gate with 5,005 passed, 4 intentional skips, and zero
failures. No real candidate repository or persistent runtime was used.

MAD-762 verification: 10 contract checks and 127 runtime-focused Python checks,
plus the browser harness and syntax gates, passed. The complete Odysseus gate
then passed with 5,005 tests, 4 intentional skips, and zero failures. No real
candidate repository, persistent install, dependency, daemon, deployment, or
ORACLE change occurred.

MAD-763 verification: 9 reference-neutral MCP adapter checks and 153 focused
extension/MCP/voice regression checks passed. The complete Odysseus gate then
passed with 5,014 tests, 4 intentional skips, and zero failures in 119.75
seconds. No real candidate repository, persistent install, dependency, second
MCP client, daemon, registry, approval layer, deployment, or ORACLE change
occurred.

MAD-758 verification: 3 candidate server boundary checks, the candidate browser
bridge harness, and the candidate-owned generic lifecycle integration check
passed. The real pinned candidate revisions completed clean install,
disable/enable, upgrade, rollback, server-loss isolation, uninstall, and
reinstall while a differently named reference surface remained healthy. The
existing Odysseus extension/ownership regression set passed 34 Python checks,
and its generic browser-surface harness passed. The complete Odysseus gate then
passed with 5,014 tests, 4 intentional skips, and zero failures in 182.67
seconds. Candidate syntax/manifest checks also passed. No Odysseus core,
ORACLE, dependency, persistent install, push, deployment, publication, or
release change occurred.

MAD-757 verification: 104 candidate offline/package/boundary checks and one
real-revision generic lifecycle integration check passed. The current
skill-bundle/registry/context regression set passed 22 checks. The complete
Odysseus gate passed with 5,014 tests, 4 intentional skips, and zero failures
in 120.91 seconds. The public maintained fork exists, but candidate commits
remain local-only. No Odysseus core, dependency, persistent install, live
vision traffic, ORACLE/Barehands/CT103/CT104, push, deployment, package, or
release change occurred.
