# Jarvis OS Protocol Runtime Status

This record describes the source state on branch
`codex/mark7-concurrent-jarvis`. It is not a deployment claim.

| Protocol | Source status | Runtime owner / boundary |
| --- | --- | --- |
| `JOS-P0` | Canonical engine contract | Odysseus mounts Jarvis around a replaceable engine |
| `JOS-P1` | Canonical contract; runtime convergence still separate | Odysseus-owned identity and constitution |
| `JOS-P2` | Agent/chat source baseline; portability pending | Context manifest, trust, omissions, compaction, and budgets work; `oracle_state` must become generic in `MAD-750` |
| `JOS-P3` | Source baseline; provider portability pending | Canonical memory provenance, review/migration, and optional projections work; private provider defaults/docs remain `MAD-750` |
| `JOS-P4` | Agent-loop baseline; broker convergence pending | Agent-loop tools share the envelope; direct voice worker dispatch does not yet (`MAD-750`) |
| `JOS-P5` | Agent-loop baseline; generic extension policy pending | Receipts work in the agent loop; extension authority is ORACLE-specific and broker paths use native gates (`MAD-750`) |
| `JOS-P6` | Source baseline; configured identity matching pending | Promotion lifecycle works; safety matching still contains reference names (`MAD-750`) |
| `JOS-P7` | Agent-loop/learning baseline; request convergence pending | Correlated agent events and rollback evidence work; plain chat/direct broker traces remain `MAD-750` |
| `JOS-EXT-1` | Canonical contract with an ORACLE reference adapter | Generic manifest, registry, installer, and host proof remain `MAD-751` through `MAD-754` |

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

The implementation reuses Odysseus's native session, memory, skill, tool,
worker, diagnostics, settings, backup, and ORACLE paths. It adds protocol
records and enforcement around those paths rather than a second orchestrator.
The evidence-backed portability and path-coverage limits are recorded in
[the MAD-748 audit](jos-portability-audit.md); "source baseline" is not a claim
that every compatible route is already reference-neutral or trace-enveloped.

## Integrated verification

- Cross-protocol selection: 177 passed.
- Regression selection covering the repaired full-suite failures: 275 passed.
- Complete repository suite: 4,961 passed, 4 skipped, 0 failed.
- Python compilation and `git diff --check` pass.
- The four skipped tests remain intentional suite skips; nine warnings are
  existing SQLAlchemy/Starlette/Pydantic deprecations and scheduler test
  coroutine warnings, not protocol failures.

## Explicit boundaries after source completion

- Nothing in this protocol sequence was deployed to CT103 or CT104.
- No container, service, model endpoint, provider credential, Qdrant instance,
  Obsidian vault, KarpathyWiki build, ChatGPT export, or Manus export changed.
- P1 runtime convergence remains separate: eliminate model-name inference and
  mount one canonical identity/constitution record across chat and voice.
- Live Qdrant projection enablement, canonical lab/Obsidian inventory, and
  ChatGPT/Manus migration require runtime configuration or source exports plus
  explicit acceptance. Whole transcripts never become personal memory.
- ORACLE remains the reference extension and not an agent/model. Vision support
  remains deferred. Packaging/upstream-fork work remains unselected.
- Deployment and live acceptance are separate operator-selected work because
  the production CT103 tree has known source divergence and requires narrow,
  backup-first changes.
