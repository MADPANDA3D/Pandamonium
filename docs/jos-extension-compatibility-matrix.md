# JOS-EXT-1 Compatibility Matrix

**Linear:** `MAD-755`

This is a read-only compatibility inventory. No candidate repository was
cloned, forked, modified, installed, executed, pushed, or deployed while
producing it. Candidate names and URLs are planning/install data; none belongs
in Odysseus core dispatch.

## Current host boundary

The source host at Odysseus commit `13fb3209` supports:

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
  owner correlation and explicit browser-only media permissions.

It does not yet provide an MCP runtime adapter. ORACLE's legacy message
normalizer and unregistered fallback are deliberately retained until source and
deployed equivalence are separately proven.

## Candidate decisions

| Candidate | Pinned revision | License | Native surface to reuse | Compatibility decision | Child baton |
| --- | --- | --- | --- | --- | --- |
| [img2threejs](https://github.com/img2threejs/img2threejs/tree/9fbd0ca5bbcc3b13bebe712745d6784d33db0b85) | `9fbd0ca5bbcc3b13bebe712745d6784d33db0b85` | Apache-2.0 | Root Agent Skill, Python 3.10+ stdlib `forge` gates, project state, TypeScript/spec/render artifacts | Conditional: use a generic skill-bundle adapter; do not flatten its scripts into core tools | `MAD-757` |
| [barehands](https://github.com/jaredrhod/barehands/tree/c6106cac49ecc6a6182c55746a95095888281f73) | `c6106cac49ecc6a6182c55746a95095888281f73` | AGPL-3.0 | Existing stdlib loopback server, browser UI, jailed notes/media, state and allowlisted command endpoints | Conditional: candidate-owned live catalog/bridge plus generic web-surface mount; browser camera remains client-side | `MAD-758` |
| [Robin](https://github.com/apurvsinghgautam/robin/tree/575d105e2f0fd61a450d5b4368535d0e83060354) | `575d105e2f0fd61a450d5b4368535d0e83060354` | MIT | Native Python search, scrape, health, and LLM modules in an isolated runtime | Conditional: candidate-owned narrow MCP server plus generic MCP adapter; never automate Streamlit | `MAD-760` |
| [text-to-cad](https://github.com/earthtojake/text-to-cad/tree/0e94cd1d2b5fa2013d89aa9504ecadcf16ce39f6) | `0e94cd1d2b5fa2013d89aa9504ecadcf16ce39f6` | MIT | Existing Codex plugin metadata, 12 Agent Skills, deterministic CLIs, loopback viewer, artifact workflow | Conditional: admit native plugin/skill metadata through the same generic skill-bundle adapter; start with `cad` and `cad-viewer` only | `MAD-759` |

## Evidence and constraints

### img2threejs

- `SKILL.md` is the native router and records its own version, license, state
  gate, workflow, inputs, outputs, and optional integrations.
- The normal `forge` path declares no third-party Python dependency. Optional
  GLB and vision integrations are isolated subprojects and stay disabled in
  the first compatibility slice.
- `.img2threejs/state.json` is candidate/project state, not Jarvis memory.
- Initial permissions are selected-image/project reads plus generated
  spec/state/TypeScript/review writes. Network remains off by default.

### barehands

- `server.py` binds `127.0.0.1`, exposes existing state/config/note/media and
  board-command endpoints, caps request bodies, jails note/media paths, and
  validates commands against its native allowlist.
- The configured note root may be an Obsidian vault. That makes it a useful UI
  surface, not a new memory authority or unrestricted filesystem reader.
- Webcam tracking and MediaPipe stay in the browser. A gesture is user intent,
  never an approval receipt.
- AGPL-3.0 obligations must be resolved before distributing or offering a
  modified networked fork.

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
3. `MAD-763` — generic MCP runtime adapter using the existing Odysseus MCP
   subsystem; blocks `MAD-760`.

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
