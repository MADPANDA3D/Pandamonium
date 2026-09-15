# Repository ingestion and marketplace delivery

Approved by Leo on 2026-09-15: **“ok get it done.”** Linear project:
**Pandamonium — Daily Driver & Ecosystem**, milestone **M17 Repository-to-Plugin
Ingestion & Marketplace**. This is the complete program; finishing a foundation
issue does not mean arbitrary-repository ingestion is finished.

## Product contract

Paste a source URL, scan its purpose and actual interfaces, build a package with
verified agent capabilities, complete required setup, and install it into
Pandamonium. Describe what people can do with the tool before technical metadata.
A cloned directory or an empty tool schema is not a successful installation.

Skill bundles remain plugins and admit their selected skills through the native
Skills manager. Skills are instructions, not fabricated executable tools.
Executable integrations expose callable schemas through the shared agent runtime
and its existing authorization path. Text and voice must discover the same
installed capabilities. Use the existing model gateway for semantic repository
understanding and package-local generated adapters for unfamiliar interfaces.
Never add repository-name branches to Pandamonium's core scanner or runtime.

The developer's **Add to marketplace** action is the publishing decision. It
packages and publishes the tested integration without a second editorial approval
or an unsigned-submission dead end. Package validation and user setup remain
functional requirements. A new Pandamonium installation discovers this shared
catalog without copying files out of Leo's account. Catalog publication never
installs packages into an account.

Leo will test both a marketplace install and a fresh GitHub URL. Automated work
uses disposable owners/data directories/nodes and retains evidence and exact
cleanup. Preserve his existing ORACLE, Ponytail and Superpowers installations.

## Existing failure, verified against c890c857 / v1.0.67

- A marketplace preview downloads and verifies an archive, then calls the Git
  installer, which clones the source instead of using those verified bytes.
- The publisher clones upstream and requires a committed manifest; it loses
  generated manifests/adapters. Packages cannot faithfully carry local integration.
- Go classification reports dependencies but produces no installable integration.
  Python CLI drafts do not establish executable, correctly typed tools.
- Installed intake rows appear in the local marketplace but do not populate the
  public catalog. **Offer to marketplace** writes an unsigned submission file.
- Catalog discovery needs host-local configuration; clean-install bootstrap and
  the developer publishing flow are still missing.

## Delivery order and acceptance

Each issue is one sequential implementation baton. Read live Linear, Git and the
Home Lab handover before continuing; only the active issue is In Progress.

| Issue | Deliverable | Required proof |
|---|---|---|
| [MAD-957](https://linear.app/madpanda3d/issue/MAD-957) | Install verified integration artifacts; publish prepared trees | Generated files survive real archive installation, same-source upgrade, rollback and restart; skill package is admitted natively; tampering fails |
| [MAD-958](https://linear.app/madpanda3d/issue/MAD-958) | Semantic intake and integration generation | Source-backed purpose, interfaces, tools/skills, setup requirements and package-local adaptation; bounded generation with actionable failure |
| [MAD-959](https://linear.app/madpanda3d/issue/MAD-959) | CLI runtime and native agent tooling | yt-dlp, Ponytail and PandaFlix pass URL-to-package-to-real-use; CLI arguments and outputs have meaningful schemas |
| [MAD-960](https://linear.app/madpanda3d/issue/MAD-960) | Services, APIs, knowledge and interactive runtimes | Reuse shared runtime/configuration/job seams; real service/API operations, indexed knowledge retrieval and honest interactive requirements |
| [MAD-961](https://linear.app/madpanda3d/issue/MAD-961) | Purpose-first plugin UI and setup | Browse, detail, capabilities, examples, Install, setup and readiness; skill bundles visible in both Plugins and Skills |
| [MAD-962](https://linear.app/madpanda3d/issue/MAD-962) | Developer publication and catalog bootstrap | One developer action publishes the tested package; a clean installation discovers and installs it |
| [MAD-963](https://linear.app/madpanda3d/issue/MAD-963) | Complete requested inventory | All 30 entries reconciled with source, integration recipe, capability/setup evidence and published package status |
| [MAD-964](https://linear.app/madpanda3d/issue/MAD-964) | Held-out repository and recovery tests | Fresh URLs work without core source edits; install/disable/enable/upgrade/rollback/remove and failure cleanup are proven |
| [MAD-965](https://linear.app/madpanda3d/issue/MAD-965) | Release and operator acceptance handoff | Required CI, signed release, permitted service-only deployment/rollback, clean catalog discovery and two operator workflows ready |

### Intake and adaptation

1. Fetch a bounded, immutable source snapshot. Repository text is evidence, not
   instructions to the ingestion agent or authorization to execute it.
2. Read README/docs, real command definitions, APIs, MCP descriptors, skills and
   examples. Distinguish the app's purpose from incidental development tooling.
3. Prefer an existing native descriptor/MCP/OpenAPI interface. Otherwise use the
   configured model to generate a package-local adapter from those interfaces.
   Record evidence paths and exact interface bindings, rather than invented tools.
4. Produce a prepared package containing source, manifest, generated adapters,
   tool schemas/skill declarations, setup declarations and validation recipe.
   Record upstream revision separately from integration-package digest/version.
5. Validate in a disposable runtime with bounded build/run resources. Resolve
   dependencies and test actual calls; allow bounded repair using failure evidence.
   A missing credential/device/service stays **Needs setup** with the exact next
   step. An unverified operation is never presented as ready.
6. Both URL intake and marketplace installation use that same package and native
   admission path. Runtime data, environments, caches and owner credentials belong
   outside the immutable package tree.

### Installation and lifecycle

Installation resolves the target node and dependencies, creates the runtime,
collects owner-specific configuration, validates schemas, registers native
capabilities/skills and completes a real health/operation check. Schema discovery
alone is not execution proof. Service runtimes need start/stop/health and durable
job/artifact handling where relevant. Disable unregisters capabilities; enable
restores them; removal preserves declared user data; failed upgrades preserve the
working version. Two generated integrations at the same upstream commit must
remain distinct packages with independent rollback.

### Marketplace

Cards and details show name, purpose, available actions, examples and setup needs.
Technical provenance/dependencies remain inspectable secondary details. Publishing
ships package contents and public metadata, never account tokens or machine-local
runtime configuration. The developer action performs build/test/sign/upload/catalog
update with visible progress and precise failure. Public discovery has a bundled
trusted catalog/key source and supported refresh. Test fresh-user discovery.

## Complete requested inventory: 30 entries

These are requested source pointers and integration targets, not claims that
uninspected repositories already expose these tools. Resolve and pin each source
before packaging. Keep the per-entry implementation/evidence results in this
program's inventory; do not silently drop difficult entries.

| # | Entry | Requested source | Integration target |
|---|---|---|---|
| 1 | Ponytail | https://github.com/dietrichgebert/ponytail | Native skill bundle |
| 2 | Superpowers | https://github.com/obra/superpowers | Native skill bundle |
| 3 | agentic-awesome-skills | https://github.com/sickn33/agentic-awesome-skills | Evaluate/admit useful skills; approved default inclusion must use the same validated package path |
| 4 | Chatterbox | https://github.com/resemble-ai/chatterbox | Speech runtime; deployment-node wizard automatically connects voice configuration, editable in Settings |
| 5 | myinstants-api | https://github.com/MADPANDA3D/myinstants-api | Audio/API integration |
| 6 | yt-dlp | https://github.com/yt-dlp/yt-dlp | CLI tooling and resulting artifacts |
| 7 | open-gif-api | https://github.com/bryanstedman/open-gif-api | GIF/API integration |
| 8 | OtakuGIFs | https://otakugifs.xyz/ | Website/API pointer; resolve actual integration source, do not pretend it is a Git repository |
| 9 | gifukai-api | https://github.com/lucialv/gifukai-api | Held-out API/service URL |
| 10 | ani-cli | https://github.com/pystardust/ani-cli | Held-out shell/interactive CLI URL |
| 11 | PandaFlix | https://github.com/MADPANDA3D/pandaflix | Go CLI capabilities and interactive prerequisites |
| 12 | img2threejs | https://github.com/img2threejs/img2threejs | Image/3D integration |
| 13 | Barehands | https://github.com/jaredrhod/barehands | 3D/CAD integration |
| 14 | blender-agent-tools | https://github.com/elasticdotventures/blender-agent-tools | Blender agent integration and required host setup |
| 15 | step.parts | https://github.com/earthtojake/step.parts | CAD/parts integration |
| 16 | text-to-cad | https://github.com/earthtojake/text-to-cad | CAD generation integration |
| 17 | ORACLE | https://github.com/MADPANDA3D/ORACLE | Preserve working native integration; test distributable package |
| 18 | Robin | https://github.com/apurvsinghgautam/robin | Security/research integration |
| 19 | cvelistV5 | https://github.com/CVEProject/cvelistV5 | Dataset search/retrieval and update provenance |
| 20 | RedTeam-Tools | https://github.com/A-poc/RedTeam-Tools | Collection discovery; linked tools installed separately |
| 21 | BlueTeam-Tools | https://github.com/A-poc/BlueTeam-Tools | Collection discovery; linked tools installed separately |
| 22 | SpiderFoot | https://github.com/smicallef/spiderfoot | Service/CLI research tooling |
| 23 | theHarvester | https://github.com/laramies/theHarvester | CLI research tooling |
| 24 | Black-Hat-Bash | https://github.com/dolevf/Black-Hat-Bash | Knowledge/collection integration; no blanket execution of examples |
| 25 | build-your-own-x | https://github.com/codecrafters-io/build-your-own-x | Graphify, then vectorize for native knowledge use |
| 26 | Graphify | https://github.com/Graphify-Labs/graphify | Knowledge/codebase graph integration |
| 27 | public-apis | https://github.com/public-apis/public-apis | API discovery and guided endpoint/credential setup |
| 28 | Maxun | https://github.com/getmaxun/maxun | Service/automation integration |
| 29 | Stirling PDF | https://github.com/Stirling-Tools/stirling-pdf | Held-out document service URL |
| 30 | Unsloth Studio | Existing external-runtime adapter, MAD-796 | Resolve exact repository/version from existing adapter before publication; preserve external-node configuration |

Counts: 3 skill bundles, 8 media entries, 5 CAD/3D entries, 8 security/research
entries including ORACLE, 6 other entries. The later security shortlist, Pantheon
research and robotics_books are outside these 30.

**Held-out rule:** do not build special support against ani-cli, gifukai-api or
Stirling PDF while developing the generic machinery. Evaluate their fresh URLs
after it works; any needed improvement must generalize to their interface class.

## Completion evidence

For every package record source/ref, package digest/version, purpose/capabilities,
setup and tested platforms/node, commands/requests performed, actual results,
publication URL/catalog entry and cleanup. Tests may use disposable credentials
or require configured services; record those limits. A catalog listing, mock
success, installed badge, or language classification alone cannot close this
program. End with marketplace-install and fresh-URL acceptance instructions for
Leo, while leaving the requested packages uninstalled in his account.
