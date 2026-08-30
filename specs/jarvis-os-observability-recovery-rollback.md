# Jarvis OS Observability, Recovery, and Rollback

**Protocol ID:** `JOS-P7`

**Version:** `0.2`

**Status:** Implemented source baseline; deployment acceptance pending

**Runtime record:** [JOS-P7 runtime baseline](../docs/jos-p7-runtime.md)

**Operational state owner:** Odysseus

## Purpose

Jarvis must fail visibly, recover from canonical state, and return to a known
working configuration without depending on the failed model or component.

`JOS-P7` defines correlated operational events, health and outcome reporting,
restart reconstruction, backup verification, and reversible promotion of
engines, protocols, extensions, memory projections, and learned artifacts.

## Event envelope

Material operations MUST emit or persist a logical event containing:

| Field | Meaning |
| --- | --- |
| `event_id` | Stable unique identifier |
| `timestamp` | Server time with timezone |
| `request_id` | Parent Jarvis request when applicable |
| `session_id`, `task_id`, `call_id` | Correlation identifiers |
| `operator_id`, `agent_id`, `actor` | Ownership and attribution |
| `component` | Engine, tool, worker, extension, memory, scheduler, or control plane |
| `event_type` | Started, progress, result, approval, health, recovery, promotion, rollback |
| `status` | Controlled outcome/state |
| `duration`, `usage` | Bounded metrics when applicable |
| `evidence_refs` | Result, artifact, log, backup, or verifier references |
| `error` | Secret-free category and bounded detail |

Events may have component-specific metadata, but correlation and ownership
fields remain stable across transports.

## Outcome taxonomy

Odysseus MUST distinguish:

- `succeeded` — required evidence exists;
- `failed` — execution completed unsuccessfully;
- `denied` — authority policy prevented execution;
- `cancelled` — confirmed cancellation;
- `timed_out` — deadline elapsed, final external outcome may need reconciliation;
- `unknown` — execution may have occurred but cannot be proven;
- `degraded` — operation continues with a named capability unavailable;
- `unavailable` — required component cannot serve the request.

These states cannot be collapsed into a generic assistant apology or success.
Retries depend on the action's `retry_safe` declaration and reconciliation, not
on the model asking again.

## Observability

The operator surface SHOULD provide:

- current engine/provider identity and proven context window;
- request latency, input/output usage, context pressure, and tool rounds;
- effective capabilities and authority mode;
- tool, worker, extension, and approval events correlated to the turn;
- health for required stores, providers, workers, schedulers, and extensions;
- active degraded states and their user-visible consequence;
- current protocol/component versions and last known compatible set;
- backup age, verification status, and rollback availability.

Health probes MUST be bounded, non-destructive, owner/admin scoped, and free of
secrets. Logs and events use controlled errors rather than credential-bearing
URLs or raw provider exceptions.

## Failure containment

Failures preserve canonical state:

- engine timeouts, malformed streams, repetition collapse, and provider outage
  end the engine turn without corrupting session history;
- tool and worker failures remain attached to their calls/tasks;
- unavailable retrieval indexes degrade recall without deleting memory;
- an extension failure removes or degrades only its scoped capabilities;
- scheduler/event failures retain durable next-run or task state;
- repeated upstream failures enter a bounded cooldown rather than blocking all
  requests indefinitely;
- partial operations report their completed and incomplete components.

## Recovery

After process, engine, worker, extension, or index restart, Odysseus MUST be able
to reconstruct the next valid state from canonical storage:

1. load authenticated users, sessions, protocol/component versions, and policy;
2. restore canonical conversation/task state;
3. reconnect configured providers, workers, and extensions independently;
4. validate or rebuild disposable retrieval projections;
5. reconcile in-flight actions whose external outcome is unknown;
6. resume only retry-safe or explicitly operator-approved work;
7. expose any state that could not be recovered.

Engine-native caches and hidden conversation state are never required for
recovery.

## Rollback units

Every promoted mutable component SHOULD have a rollback record:

| Component | Minimum rollback unit |
| --- | --- |
| Engine | Previous endpoint/model/adapter configuration and compatibility result |
| Identity/constitution | Previous approved version |
| Context/memory policy | Previous versioned policy and index schema |
| Memory/document projection | Rebuildable schema plus previous working route |
| Tool/authority policy | Previous policy version |
| Extension | Previously compatible pinned extension and host versions |
| Skill/prompt guidance | Previous approved artifact version |
| Deployment | Reviewed file/config manifest and recovery archive |

Rollback changes the smallest failed unit. It MUST NOT silently revert
unrelated user data, memories, or documents.

## Backup and restore

Backups MUST identify scope, creation time, source version, exclusions,
integrity result, and restoration procedure. A backup is not trusted until
verified.

- Canonical data is backed up before destructive restore or migration.
- Restore requires explicit operator authorization and preserves a recoverable
  copy of the replaced state when practical.
- Archive extraction rejects traversal, absolute paths, and unsafe links.
- External vector volumes are either backed up separately or declared
  disposable and rebuilt from canonical sources.
- Secrets receive their own protected backup/rotation policy and never appear
  in ordinary export previews.
- Restore acceptance verifies application state, ownership, projections, and
  protocol versions rather than only process liveness.

## Current implementation anchors

| Responsibility | Existing anchor |
| --- | --- |
| Usage, context, timing, and tool events | `routes/chat_helpers.py`, `src/agent_loop.py`, `src/llm_core.py` |
| Durable sessions and task state | `core/session_manager.py`, `src/jarvis_agent.py` |
| Worker event correlation and replay | `src/agent_worker_adapters.py`, `routes/agent_task_routes.py` |
| Consolidated bounded service health | `src/service_health.py`, `routes/diagnostics_routes.py` |
| Event-trigger persistence | `src/event_bus.py`, `src/task_scheduler.py` |
| API data export/import | `routes/backup_routes.py` |
| Full data snapshot, verify, and restore | `scripts/odysseus-backup`, `docs/backup-restore.md` |
| Provider cooldown and stream failure guards | `src/llm_core.py` |
| Protocol-specific deployment rollback records | Home Lab `HANDOVER.md` |

Current telemetry and rollback evidence are distributed across chat metrics,
logs, tasks, health endpoints, backup tools, and handoff records. The runtime
does not yet provide one request-level protocol trace or one component-version
rollback manifest. Docker vector volumes also require explicit separate backup
or canonical rebuild treatment.

## Compatibility gate

`JOS-P7` is satisfied only when these pass:

- one request can be traced across engine, tool, approval, worker/extension,
  result, and final response without exposing secrets;
- failed, denied, cancelled, timed-out, unknown, degraded, and unavailable
  states remain distinct in storage and UI;
- an engine outage preserves the session and permits a compatible fallback or
  later retry;
- an index outage degrades recall and a rebuild restores parity from canonical
  sources;
- process restart reconstructs sessions and durable worker/task state;
- an unknown external mutation is reconciled before retry;
- a verified backup can restore into an isolated target and pass ownership and
  data checks;
- rollback of an engine, extension, policy, or skill restores the prior
  compatible version without reverting unrelated canonical data;
- health probes respect hard time budgets and redact secrets;
- failure of observability itself cannot authorize or execute an action.

## Definition of success

`JOS-P7` succeeds when Leo can answer what happened, who or what did it, what
evidence exists, what is degraded, what state survived, and exactly how to
recover or roll back the smallest affected component.

## Non-goals

- Replacing existing monitoring or backup tools before the unified contract is
  implemented.
- Treating logs alone as canonical action evidence.
- Automatically retrying unknown side effects.
- Requiring zero downtime from every optional extension or provider.
