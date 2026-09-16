# MAD-963 — Complete requested plugin inventory

All **30 requested entries** are reconciled against the approved plan. **12 source
entries have 13 signed packages** (text-to-cad is split into two native skill
bundles); **15 entries retain explicit outstanding work**; **3 held-out URLs remain
untouched for MAD-964**. Twelve packages were newly published; the existing Ponytail
1.0.0 artifact was preserved. This closes inventory reconciliation, not the full
arbitrary-repository program or the outstanding integrations.

[Machine-readable evidence](mad-963-inventory.json) records every exact source pin,
prepared digest, final published digest/version/URL, actual native operation result,
lifecycle, cleanup and limitations. [Approved list](repository-plugin-ingestion-plan.md)
remains authoritative. No corpus package was installed into Leo's account; existing
ORACLE, Ponytail and Superpowers state was not read or modified for validation.

## Complete accounting

“Published” means the **declared coverage below**, not every upstream feature.
Skills are admitted instructions. Their downstream commands/devices are not
executed by skill admission. Every source revision below links to immutable source;
OtakuGIFs is correctly recorded as an API/website, and holdouts are intentionally
unpinned.

| # | Entry / source revision | State | Verified coverage and outstanding work |
|---|---|---|---|
| 1 | [Ponytail `e3ba2aa6`](https://github.com/DietrichGebert/ponytail/tree/e3ba2aa6f1e6f0bc4d69eb09c9f0d0a93af56156) | Published | Native admission and body retrieval of all six Ponytail skills. **Limit/next step:** Instructions only; commands inside skills keep normal host requirements and authorization. |
| 2 | [Superpowers `b36e0829`](https://github.com/obra/superpowers/tree/b36e0829c6d0140e93cfef2ca599b1b07d4a7797) | Published | Native admission and body retrieval of all fourteen Superpowers skills, including CJS, shell and DOT references. **Limit/next step:** Instructions only; optional credential-shaped brainstorm-server test fixtures excluded. |
| 3 | [agentic-awesome-skills `69906dde`](https://github.com/sickn33/agentic-awesome-skills/tree/69906dde999aaa0f3d173f0e3d5bcdb84c87a294) | Outstanding | Evaluated systematic-debugging, test-driven-development, verification-before-completion and api-design-principles against their real bodies and references. The first three duplicate Superpowers; api-design-principles is a useful candidate. No default enablement. **Limit/next step:** Outstanding: review selected-skill attribution and third-party exceptions, unsupported metadata/assets and credential-shaped examples. The 23,096-file mixed corpus has no installable draft; do not relicense it wholesale as MIT or treat evaluation as admission. |
| 4 | [Chatterbox `5de7a54a`](https://github.com/resemble-ai/chatterbox/tree/5de7a54aa4e5e2baadb0182dde554908b48b85c2) | Outstanding | Existing Chatterbox external-runtime recipe and node/voice setup contract reviewed against pinned upstream. **Limit/next step:** Needs setup: choose a disposable model-serving node, model weights and supported accelerator; run real speech inference. Prior fixture transport is not synthesis proof; preserve editable voice configuration. |
| 5 | [myinstants-api `435d40d3`](https://github.com/MADPANDA3D/myinstants-api/tree/435d40d309ab2790e21567b00c9727509badeedd) | Published | Real recent-sound API response through the mounted native tool, ten sound records with source/audio URLs. **Limit/next step:** Requires live Myinstants access; sound content retains its own rights. |
| 6 | [yt-dlp `bbc809a1`](https://github.com/yt-dlp/yt-dlp/tree/bbc809a1161d3bfca51fa36f59dda35556ee85a0) | Published | Inspect and download a real bounded direct-media WAV through native tools; artifact size 1,644 bytes. **Limit/next step:** This package supports the declared direct-media workflow; optional provider extractors are excluded, and all-site compatibility is not claimed. Two unused private-key certificate fixtures were excluded after full publication audit. |
| 7 | [open-gif-api `d78c919c`](https://github.com/bryanstedman/open-gif-api/tree/d78c919c5174a6971f72590a393f9dcaaf274ca8) | Outstanding | Pinned Node/Express source; README documents /all and /tag/<tag>. Static scan completed. **Limit/next step:** Outstanding: no top-level upstream license. Vendored node_modules licenses do not license this API project. Resolve redistribution rights and a working service endpoint before packaging/publication. |
| 8 | [OtakuGIFs](https://api.otakugifs.xyz) | Outstanding | Resolved official website documentation to api.otakugifs.xyz; real HTTP 200 allreactions and kiss GIF calls. **Limit/next step:** Outstanding: the website is not a Git repository. No official distributable backend/source license found. A third-party wrapper is not the official source; select an attributed licensed API adapter before package publication. |
| 9 | gifukai-api | Held out | Intentionally not fetched, adapted, installed or published by this task. **Limit/next step:** MAD-964 must evaluate the fresh URL without repository-specific core support. |
| 10 | ani-cli | Held out | Intentionally not fetched, adapted, installed or published by this task. **Limit/next step:** MAD-964 must evaluate the fresh URL without repository-specific core support. |
| 11 | [PandaFlix `f6484787`](https://github.com/MADPANDA3D/pandaflix/tree/f6484787ba5a23d4453d5bbd8eb34f05054ecfbb) | Published | Real Go compilation plus episode-range expansion, playback planning and isolated empty watch history through three native tools. **Limit/next step:** Interactive playback needs a configured player/display and provider access; not claimed by the verified planning operations. |
| 12 | [img2threejs `6e60b5e2`](https://github.com/img2threejs/img2threejs/tree/6e60b5e22419464b4853e01ddb6c0e6f6659a733) | Published | Real upstream sculpt-spec creation and validation through two native tools, retaining a JSON artifact. **Limit/next step:** Partial requested capability: scaffold creation is not image interpretation, mesh generation or rendered reconstruction. Full native root skill exceeds the 64-file asset ceiling; downstream image/Three.js workflow remains outstanding. |
| 13 | [Barehands `eb23bed2`](https://github.com/jaredrhod/barehands/tree/eb23bed2d772f9d5a24de26fb92f46c3c76d69cf) | Published | Real private Barehands server starts; native tools read /state and /config; lifecycle stops and removes it. **Limit/next step:** Source correction: Barehands is a webcam-controlled board, not a CAD generator. Interactive gesture proof requires Chrome and a webcam; no camera access was used. |
| 14 | [blender-agent-tools `7ace50a9`](https://github.com/elasticdotventures/blender-agent-tools/tree/7ace50a94f282b0bbfbe9eae2eb6e621ea012743) | Outstanding | Inspected the tutorial/meta-repository and referenced Blender agent host stack. Static scan completed. **Limit/next step:** Outstanding: no project license and no self-contained Blender add-on at this source. Resolve the actual add-on source/version and a disposable Blender host before packaging. |
| 15 | [step.parts `c6113328`](https://github.com/earthtojake/step.parts/tree/c6113328a5695b976a010a203a90fe86191769bf) | Published | Real official api.step.parts /v1/parts search for M3 with original part metadata through native dispatch. **Limit/next step:** Geometry LFS pointers are not bundled CAD binaries. Each downloaded model keeps its own license; search does not claim geometry generation. |
| 16 | [text-to-cad `3e4dfdee`](https://github.com/earthtojake/text-to-cad/tree/3e4dfdeef2cbd5804c369592b59620132188a150) | Published | All eleven canonical CAD skills admitted and read natively across two packages (eight CAD/fabrication plus three robotics/parts). **Limit/next step:** Split preserves the existing 64-file admission limit. These are instructions; cadgen, slicers, viewers and printer/device execution require owner setup and were not executed. Two unrelated credential-shaped test files excluded. |
| 17 | [ORACLE `b619e2a1`](https://github.com/MADPANDA3D/ORACLE/tree/b619e2a17015d0e1c044fb273677b00abccdbede) | Outstanding | Preserved existing native ORACLE integration; pinned distributable-source scan was attempted and failed closed with extension_scan_source_secret. **Limit/next step:** Outstanding: prepare a secret-clean distributable source and disposable browser bridge/runtime validation. Existing native web/live bridge is outside current isolated publisher adapters. No changes to Leo's installed ORACLE or its origin/configuration. |
| 18 | [Robin `7bda35f5`](https://github.com/apurvsinghgautam/robin/tree/7bda35f57408190642608bb78e196b791a1b2152) | Outstanding | Pinned Robin CLI/source and real requirements inspected; static scan completed. **Limit/next step:** Needs setup: disposable Tor SOCKS connectivity plus a selected model/provider or Ollama runtime. No live dark-web search or model result claimed; an isolated adapter and operation proof remain outstanding. |
| 19 | [cvelistV5 `9ea9ff38`](https://github.com/CVEProject/cvelistV5/tree/9ea9ff3852f0e9342443ca3378576f2048216c6b) | Outstanding | Pinned CVEProject tree and update provenance. The truncated recursive response already contains 77,154 files / 752,298,701 bytes. **Limit/next step:** Outstanding: full dataset exceeds 50,000-file / 512 MiB snapshot bounds. Design bounded revision-aware shards/update retrieval and confirm CVE terms; no sample masquerades as a complete indexed dataset. |
| 20 | [RedTeam-Tools `7b52217a`](https://github.com/A-poc/RedTeam-Tools/tree/7b52217a9856aceedc9ff4b1e0b11e1667e22fd2) | Outstanding | Real native status, cited collection search and refresh; linked tools remain reference data. **Limit/next step:** Publication blocked: upstream collection has no detected project license. Linked tools require separate source/installation decisions. |
| 21 | [BlueTeam-Tools `1bca7fd5`](https://github.com/A-poc/BlueTeam-Tools/tree/1bca7fd57c4a0a72f4aeb3fc1ea93ddfa63f1e7c) | Outstanding | Real native status, cited collection search and refresh; linked tools remain reference data. **Limit/next step:** Publication blocked: upstream collection has no detected project license. Linked tools require separate source/installation decisions. |
| 22 | [SpiderFoot `0f815a20`](https://github.com/smicallef/spiderfoot/tree/0f815a203afebf05c98b605dba5cf0475a0ee5fd) | Outstanding | Pinned SpiderFoot service/CLI and typed helpers inspected; static audit completed. **Limit/next step:** Outstanding: provision a disposable compatible dependency/service runtime, resolve the credential-shaped Citadel module finding, then validate real research operations. Scanner recognized a vendored BSD notice but misses the untitled root MIT grant; do not publish under the vendored license alone. |
| 23 | [theHarvester `78a78f08`](https://github.com/laramies/theHarvester/tree/78a78f08d4a0d6d9bf6058effa8ddafb9e2d070b) | Outstanding | Pinned theHarvester CLI, scope parser and Python requirements inspected; static audit completed. **Limit/next step:** Needs setup: private Python >=3.14 (app test runtime is 3.10), provider selection/credentials and sanitized distributable source. Real harvesting and generated adapter remain outstanding. GPL-2.0 appears in README/COPYING/LICENSES and is not recognized by the current heuristic. |
| 24 | [Black-Hat-Bash `92d92873`](https://github.com/dolevf/Black-Hat-Bash/tree/92d9287305255aab1e433cd54755acf21f5bd3f9) | Published | Real native knowledge status, cited search and refresh over README and chapter scripts as text. **Limit/next step:** No lab/example execution. Optional WordPress credential-shaped JS examples excluded; retain all shipped license notices. |
| 25 | [build-your-own-x `aa17439b`](https://github.com/codecrafters-io/build-your-own-x/tree/aa17439b62f384511a5561ce308e9598b94d8989) | Outstanding | Real Graphify Markdown extraction (36 nodes/35 edges), 27 indexed chunks, real local FastEmbed vectors, cited native search and refresh. **Limit/next step:** Publication blocked: no upstream project license found. Linked tutorials/tools retain their own rights and are not executed. |
| 26 | [Graphify `c7ec1082`](https://github.com/Graphify-Labs/graphify/tree/c7ec1082083e3e876443f643ecf86ebcfae177c2) | Published | Real Graphify Markdown extractor builds nodes/edges and graph.json through the mounted native tool. **Limit/next step:** Partial requested coverage: deterministic Markdown structure verified; other language/code extractors and model-inferred relations are not advertised. Input must be in package/private artifacts. |
| 27 | [public-apis `536d5c4e`](https://github.com/public-apis/public-apis/tree/536d5c4e5ff25e16c0f27e6bda4c9308ffc5fd33) | Published | Real native status, cited API-directory search and refresh. Returned entries show endpoint docs and authentication/CORS information. **Limit/next step:** This is API discovery; each selected endpoint still needs its own credentials, terms and integration setup. |
| 28 | [Maxun `5b946a3d`](https://github.com/getmaxun/maxun/tree/5b946a3d7c0676e53da92900c311e5ab21af4772) | Outstanding | Pinned Maxun service source and deployment requirements inspected; static audit completed. **Limit/next step:** Needs setup: disposable Postgres, Redis, MinIO and browser services with owner configuration. Required server/auth/database files have secret-like findings; do not delete required source to evade the audit. Recording/run operation proof remains outstanding. |
| 29 | Stirling PDF | Held out | Intentionally not fetched, adapted, installed or published by this task. **Limit/next step:** MAD-964 must evaluate the fresh URL without repository-specific core support. |
| 30 | [Unsloth Studio `af4e98e2`](https://github.com/unslothai/unsloth/tree/af4e98e2f63f1907eb4b5d5f8b9375311d9b667a) | Outstanding | Resolved existing MAD-796 external adapter to unslothai/unsloth, exact source commit, studio/ subtree and v1 adapter contract. **Limit/next step:** Needs setup: chosen external GPU/model runtime and token, plus real health/train/status/artifact checks. Preserve existing node configuration. Studio package version 0.0.0 is a build holder, not a released version; use immutable source pin until runtime release is identified. |

## Reproduction and actual evidence

The existing scanner, reviewed source-grounded proposal, prepared archive,
native installer, native agent mount/dispatcher and publisher were used throughout.
No repository-name condition was added to the core. **Reviewed recipe callbacks
are not live-model generation proof.** MAD-964 owns fresh held-out acceptance.

Each retained recipe runs through the same entry point, using a fresh output path:

```sh
sh scripts/check_package_runtimes.sh .venv/bin/python scripts/validate_cli_package.py   --recipe integrations/repository-packages/graphify/proposal.json   --output /tmp/graphify-validation
```

The receipt contains real operation output and proves install → disable → enable
→ uninstall with an empty disposable registry and deleted temporary app data.
`--output` retains only the prepared archive and receipt after cleanup. The wrapper
provides a finite 2 GiB runtime filesystem; cgroup/time/output and owner isolation
remain required. Source Git transport gets the existing supported 120-second
budget for larger repositories.

- Superpowers: all 14 native bodies retrieved. Ponytail: all 6. Text-to-cad: all
  11 canonical skills admitted as 8 + 3 within the existing 64-file asset limit.
- Myinstants: actual recent sound API; STEP parts: actual official catalog search.
- yt-dlp: actual direct media inspection/download, 1,644-byte WAV. PandaFlix: real
  Go build and three mounted operations, with playback/display limits explicit.
- Barehands: actual private server, `/state` and `/config`, service cleanup.
- img2threejs: actual upstream spec creation/validation and retained JSON artifact;
  an unassessed scaffold is not a rendered model.
- Graphify: actual Markdown extractor and graph artifact. build-your-own-x: **36
  nodes, 35 edges, 27 chunks**, real FastEmbed vectors, cited search and refresh.
  FastEmbed validation dependencies/model data were disposable, not added to the
  production dependency set. That corpus remains unpublished for missing license.
- Red/Blue team collections and Black Hat Bash: real cited knowledge retrieval;
  linked tools and chapter examples remain data.
- OtakuGIFs: official API returned HTTP 200 for all reactions and a kiss GIF URL;
  this external call is not misrepresented as native package dispatch.

## Publication, fresh-user proof and rollback

The existing developer publisher performed final byte/secret/license checks,
real native skill or isolated operation validation, signing, immutable GitHub
asset upload/readback and signed catalog compare-and-swap. Generated manifests and
adapters are included. No publisher key or account configuration enters a package.
The final signed [public catalog](https://github.com/MADPANDA3D/Pandamonium/blob/marketplace/marketplace/catalog.json)
was verified with the pinned bundled key and copied to the bundled snapshot. Each
new release has curated coverage/setup/validation/provenance notes read back from
GitHub. Exact immutable artifact URLs and digests are in the JSON evidence.

Fresh-user browser proof uses the existing deployed smoke:

```sh
PLUGIN_UI_PROOF_DIR=/tmp/mad963-browser sh scripts/check_package_runtimes.sh   node tests/deployed/marketplace-publication.mjs --inventory
```

A new authenticated disposable app, without the publisher key, discovered all
**13** packages. It installed **Superpowers** (14 native skills) and **STEP parts**
(real API validation), matched registry revisions to their public artifact digests,
then removed both. Zero plugins remained. The smoke always stops its app and
removes its temporary data. Retained proof: [consumer receipt](mad-963-consumer.json).
Desktop screenshots were inspected in the task's local evidence directory.

All packages target **Linux amd64 / Pandamonium >=1.0.68**. The browser smoke used
an explicit source-only compatibility override. Application version/release,
CT103 deployment and operator acceptance remain **MAD-965**; installed 1.0.67 is
not claimed to support this catalog. No service deployment happened here.

The [prior signed catalog](mad-963-catalog-rollback.json) is retained alongside
GitHub's marketplace branch history. Use `scripts/publish_marketplace.py --rollback` with
that verified snapshot to restore its entries with a fresh signature/CAS. Never
delete or overwrite immutable release artifacts. Installed owner data stays separate.

## Small shared repairs exposed by the corpus

- Materialize only bounded, confined source links into ordinary package files;
  reject external/.git/cyclic/nested directory links. Archive links still fail.
- Admit CJS/MJS/DOT and extensionless shebang text consistently in scanner and
  native Skills admission, retaining text/size/count validation.
- Permit explicit Python `Path` arguments to serialize as JSON strings; a variable
  merely named `path` is insufficient evidence.
- Use the configured adaptation path when an uncommitted native draft omits assets
  or retains secret findings; committed native manifests retain precedence.
- Secret audit AST positions use physical CR/LF lines, so Unicode text separators
  cannot crash the audit. Literal credential detection remains active.

The publisher caught additional unused certificate/private-key test fixtures in
yt-dlp and credential-shaped tests in text-to-cad. Recipes exclude only those
optional files and retain evidence/licenses/needed implementation. Required Maxun
server files were not removed to evade findings. A nested dependency license is
not treated as permission to redistribute an otherwise unlicensed source project.

## Verification and baton

- Full local Python suite: **6,484 passed, 5 skipped** before the final inventory
  assertion and Unicode/alias regressions; **91 focused tests** and **46 final
  source/inventory regressions** cover those additions. Required PR CI is the final exact-commit check.
- Fresh authenticated browser discovery/install/remove passed; all declared package
  operations have retained native-dispatch receipts and publisher validation.
- Scoped Ruff, isort Black profile, mypy, Python/Node syntax and diff hygiene passed
  before commit. No production dependency or release version changed.
- `tests/test_requested_plugin_inventory.py` prevents silently dropping requested
  entries, adapting holdouts early, advertising unchecked capabilities or recording
  a published digest that differs from the bundled signed catalog.

After exact-commit protected merge and live Linear Done readback, create only
**MAD-964 — Plugins: prove held-out URL ingestion and lifecycle recovery — Part 1/1**.
Keep the 15 outstanding entries and partial capability limits visible through the
remaining program; inventory completion does not relabel them Ready.
