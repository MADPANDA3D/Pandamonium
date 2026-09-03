# JOS P2-P7 Generic Runtime Convergence

**Linear:** `MAD-750`

This source slice removes reference-installation assumptions from the existing
P2-P7 enforcement paths. It adds no second registry, runner, memory store, or
orchestrator and makes no deployment claim.

## Generic seams

- P2 budgets active extension data as `extension_state`; every manifest item
  keeps its `extension_id`. Existing `oracle_state` settings migrate as a
  compatibility alias.
- P3 uses `odysseus_memory`, `odysseus_documents`, and `odysseus_wiki` as new
  optional Qdrant defaults. Generic environment variables take precedence;
  legacy variables and existing knowledge data paths remain readable.
- P4 maps dynamic capabilities to their actual `extension:<extension_id>`
  target. Direct voice worker dispatch uses a bounded server-owned capability
  entry before calling the existing task runner.
- P5 derives extension permission mode from server-supplied capability policy.
  Missing or invalid policy is unclassified and denied. No extension receives
  authority merely because it is engaged or named ORACLE.
- P6 protects the configured agent identity and authenticated operator scope
  during candidate validation instead of matching reference names.
- P7 records start and terminal events for direct and streaming plain chat and
  correlates direct voice worker decisions/results to the existing task
  evidence.

## Portability proof

Focused tests exercise two distinct extension IDs and a no-extension state,
declared read/write permissions and undeclared denial, legacy settings and
projection aliases, configured learning identities, chat traces, direct worker
correlation, agent mode, voice, compaction, tool execution, and ORACLE
engage/disengage behavior.

The implementation reuses the settings API/UI, context manifest and compactor,
agent-loop catalog, authority store, operational event store, native worker task
runner, ORACLE executor, canonical memory ledger, and optional projection
adapters already present in Pandamonium.

## Boundaries

- ORACLE remains the first reference extension; its native code stays outside
  the generic host.
- The installer, lifecycle host, and reference-extension migration remain
  `MAD-752` through `MAD-754`; `MAD-751` owns the separate manifest and
  metadata-registry record.
- No Git repository was cloned or installed, no service was changed, no data
  was migrated, and no source was deployed.
