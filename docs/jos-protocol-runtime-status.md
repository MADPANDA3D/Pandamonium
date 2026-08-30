# Jarvis OS Protocol Runtime Status

This record describes the source state on branch
`codex/mark7-concurrent-jarvis`. It is not a deployment claim.

| Protocol | Source status | Runtime owner / boundary |
| --- | --- | --- |
| `JOS-P0` | Canonical engine contract | Odysseus mounts the installation-configured agent around a replaceable engine |
| `JOS-P1` | Source implementation | Authenticated settings own one identity/constitution across chat, agent, primary voice, authority, and diagnostics |
| `JOS-P2` | Generic source implementation | Context manifests, trust, omissions, compaction, and budgets include reference-neutral extension state with extension IDs |
| `JOS-P3` | Generic source implementation | Canonical memory provenance and optional projections use public defaults with backward-compatible legacy aliases |
| `JOS-P4` | Generic source implementation | Agent-loop tools and direct voice worker dispatch share the action envelope; extension targets retain actual IDs |
| `JOS-P5` | Generic source implementation | Receipts and direct worker decisions work; extension permission comes from declared server metadata and fails closed |
| `JOS-P6` | Generic source implementation | Promotion lifecycle protects the configured agent and operator identities rather than reference names |
| `JOS-P7` | Generic request-level source implementation | Agent, plain chat, direct worker, and learning events retain correlated protocol traces |
| `JOS-EXT-1` | Generic manifest and metadata-registry source implementation | Installer, ORACLE-owned reference manifest, and generic host proof remain `MAD-752` through `MAD-754` |

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

The implementation reuses Odysseus's native session, memory, skill, tool,
worker, diagnostics, settings, backup, and ORACLE paths. It adds protocol
records and enforcement around those paths rather than a second orchestrator.
The evidence-backed portability findings and their source resolutions are
recorded in [the MAD-748 audit](jos-portability-audit.md). Source implementation
is not a deployment or live-environment acceptance claim.

## Integrated verification

- P1 compatibility selection: 217 passed.
- Regression selection covering the repaired full-suite failures: 275 passed.
- Complete repository suite after P1 convergence: 4,967 passed, 4 skipped, 0 failed.
- MAD-750 P2-P7 focused selection: 277 passed, 0 failed.
- Complete repository suite after generic P2-P7 convergence: 4,974 passed,
  4 skipped, 0 failed.
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
- Deployment and live acceptance are separate operator-selected work because
  the production CT103 tree has known source divergence and requires narrow,
  backup-first changes.
