# Jarvis OS Protocol Runtime Status

This record describes the source state on branch
`codex/mark7-concurrent-jarvis`. It is not a deployment claim.

| Protocol | Source status | Runtime owner / boundary |
| --- | --- | --- |
| `JOS-P0` | Canonical engine contract | Pandamonium mounts the installation-configured agent around a replaceable engine |
| `JOS-P1` | Source implementation | Authenticated settings own one identity/constitution across chat, agent, primary voice, authority, and diagnostics |
| `JOS-P2` | Generic source implementation | Context manifests, trust, omissions, compaction, and budgets include reference-neutral extension state with extension IDs |
| `JOS-P3` | Generic source implementation | Canonical memory provenance and optional projections use public defaults with backward-compatible legacy aliases |
| `JOS-P4` | Generic source implementation | Agent-loop tools and direct voice worker dispatch share the action envelope; extension targets retain actual IDs |
| `JOS-P5` | Generic source implementation | Receipts and direct worker decisions work; extension permission comes from declared server metadata and fails closed |
| `JOS-P6` | Generic source implementation | Promotion lifecycle protects the configured agent and operator identities rather than reference names |
| `JOS-P7` | Generic request-level source implementation | Agent, plain chat, direct worker, and learning events retain correlated protocol traces |
| `JOS-EXT-1` | Generic manifest, registry, pinned lifecycle, and live-catalog host source implementation | Live deployment/acceptance remains separate; compatibility evaluation continues in `MAD-755` |

## Implementation chain

The dependency order was completed as planned:

1. P2A context observability — `e6199188`
2. P2B context enforcement — `0df13d7e`
3. P2 runtime record — `768391a0`
4. P3 memory/provenance — `db95df94`
5. P4 action envelopes — `40553a82`
6. P5 authority receipts — `43519cbf`
7. P7 operational traces/rollback — `4c87c531`
8. P6 learning/promotion — `f06e8a0b`
9. Generic P2-P7 convergence — `MAD-750` (commit recorded at issue close)
10. Generic live-catalog extension host — `MAD-754` (commit recorded at issue close)

The implementation reuses Pandamonium's native session, memory, skill, tool,
worker, diagnostics, settings, backup, and ORACLE paths. It adds protocol
records and enforcement around those paths rather than a second orchestrator.
The evidence-backed portability findings and their source resolutions are
recorded in [the MAD-748 audit](jos-portability-audit.md). Source implementation
is not a deployment or live-environment acceptance claim. The read-only
[MAD-755 compatibility matrix](jos-extension-compatibility-matrix.md) records
the four selected repository classes, their native integration surfaces, and
the generic host gaps that must close before compatibility can be claimed.
The [MAD-756 release assessment](jos-public-release-readiness.md) keeps the
public release gate closed until those proofs, public defaults, clean-room
onboarding, and an explicitly selected distribution strategy are complete.

## Integrated verification

- P1 compatibility selection: 217 passed.
- Regression selection covering the repaired full-suite failures: 275 passed.
- Complete repository suite after P1 convergence: 4,967 passed, 4 skipped, 0 failed.
- MAD-750 P2-P7 focused selection: 277 passed, 0 failed.
- Complete repository suite after generic P2-P7 convergence: 4,974 passed,
  4 skipped, 0 failed.
- Complete repository suite after manifest registry and pinned installer:
  4,994 passed, 4 skipped, 0 failed.
- Complete repository suite after generic live-catalog host convergence:
  5,000 passed, 4 skipped, 0 failed.
- Python compilation and `git diff --check` pass.
- The four skipped tests remain intentional suite skips; nine warnings are
  existing SQLAlchemy/Starlette/Pydantic deprecations and scheduler test
  coroutine warnings, not protocol failures.

## Explicit boundaries after source completion

- Nothing in this protocol sequence was deployed to CT103 or CT104.
- No container, service, model endpoint, provider credential, Qdrant instance,
  Obsidian vault, KarpathyWiki build, ChatGPT export, or Manus export changed.
- P1 now uses public-safe `assistant` / `Assistant` defaults. A private Jarvis
  constitution is an installation setting, not a model-name trigger or source
  default. Live configuration/deployment remains operator-selected.
- Live Qdrant projection enablement, canonical lab/Obsidian inventory, and
  ChatGPT/Manus migration require runtime configuration or source exports plus
  explicit acceptance. Whole transcripts never become personal memory.
- ORACLE remains the reference extension and not an agent/model. Vision support
  remains deferred. Packaging/upstream-fork work remains unselected.
- No candidate repository was cloned, forked, modified, installed, executed,
  pushed, or deployed during the MAD-755 inventory. Candidate implementation
  remains in repository-owned child batons after the shared generic adapters.
- Deployment and live acceptance are separate operator-selected work because
  the production CT103 tree has known source divergence and requires narrow,
  backup-first changes.

## Mounted protocol packs (2026-09-12)

The JOS specifications now mount at runtime as versioned packs:

- `protocols/*.pack.md` carry a strict frontmatter manifest (`id`, `version`,
  `scope`, `protocol`, `domains`, `token_budget`, `enforcement`) plus a compact
  body; `src/protocol_registry.py` validates and renders them.
- Core packs (`JOS-P0`, `JOS-P1`) mount in every chat, agent, and voice turn.
  Duty packs (`JOS-P2`–`JOS-P7`, `JOS-IPAV`) mount by the deterministic intent
  domain map in `src/action_intents.py`; trivial chat mounts core only.
- The renderer enforces declared budgets. Under context pressure the agent loop
  strips the protocol layer before input-budget trimming, so tool evidence is
  never evicted by advisory text.
- JOS-P7 records the mounted `{id, version}` set per turn
  (`record_protocol_mount`).
- Operators control the layer in Settings → AI → Protocols (layer toggle,
  per-pack enable/disable, model window/budget maps, restore defaults).
  Disabled packs stay diagnosable but never mount.
- `GET /api/diagnostics/protocol/packs` lists pack versions, scopes, budgets,
  disabled state, and manifest errors.
- Model budgets follow the connected model: operator overrides
  (`model_context_windows`, `model_input_token_budgets`) outrank persisted
  provider catalog windows (`data/model_context_catalog.json`), the live
  catalog, and the built-in table.
- Enforcement parity is asserted by `tests/test_protocol_parity.py`: every pack
  must declare at least one module that exists.
- Deterministic benchmark (`scripts/protocol_benchmark.py --engine stub`): all
  four scenarios pass; protocol blocks render 621 tokens (trivial chat) to
  1,180 tokens (web research), including 91–148 tokens of render scaffolding,
  with every measured pack body at or under its declared budget. Live
  tool-selection, completion, and latency comparison remains operator-run.
