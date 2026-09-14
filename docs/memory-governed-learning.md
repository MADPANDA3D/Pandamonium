# Governed Memory: what "learning" means

> MAD-786 audit reference. Defines the memory contract the acceptance set in
> `tests/test_memory_governed_acceptance.py` verifies.

## Definition

In Pandamonium, **learning means retrieval and curated memory updates** — the
system recalls governed facts and the operator can inspect, correct, disable,
export, and delete them. Learning never means silent weight modification, model
fine-tuning, or policy changes hidden inside prompt text. Durable behavior
changes require an explicit, evaluated promotion (see `src/learning_protocol.py`
for the skill/procedure path); conversational memory is data, not weights.

## Write lifecycle

Every memory write carries a provenance envelope:

| Field | Meaning |
|---|---|
| `source` | `user`, `auto`, `memory_audit`, `correction`, `migration`, ... |
| `owner` / `owner_id` | the tenant the fact belongs to; recall filters on it |
| `confidence` | 1.0 for operator/user statements, 0.6 for LLM extraction, 0.8 for high-precision pattern extraction, 0.5 for migration/audit. Descriptive only — never promotes a fact by itself |
| `status` | `candidate` → `approved` → `superseded` / `deleted` / `rejected` |
| `source_ref` / `source_time` | where and when the fact came from |
| `admitted_by` / `admitted_at` | which policy or operator admitted it |
| `supersedes` / `superseded_by` | correction lineage |
| `deleted_by` / `deleted_at` | deletion path kept as a tombstone |

Only `approved` records are recallable (`MemoryManager.load`). Candidates,
rejected, superseded, and deleted records stay visible for review and export
through `load_all`, the review/audit surface.

## Extraction bounds

- Background extraction is queued to run only after the foreground response
  stream has gone idle, and jobs run strictly one at a time
  (`routes/chat_helpers._run_extraction_jobs_sequentially`), so a side model
  call cannot starve foreground inference.
- The extraction window is the last `CONTEXT_WINDOW` (6) messages, flattened
  into one transcript message; the extractor prompt caps durable facts at two
  per run. Extraction runs only every fourth message pair and only when
  `auto_memory` is on and the session is not incognito/compare mode.
- The audit short-circuits when the store is unchanged and refuses a total wipe
  or a >50% removal of a store of 8+ entries as `unsafe_removal`.

## Retrieval

- Retrieval is owner-scoped, bounded (`top_k`/`max_items`), and deduplicated by
  text. Ranking uses content tokens (stop words removed, prefix match for
  morphology) so shared function words cannot create false recall.
- Identity facts are only force-included for identity questions.
- The chat path injects pinned facts plus at most three retrieved facts; the
  recalled-memory class budget is 15% of the effective input budget
  (`src/context_budget.py`).

## User controls

- Inspect: `GET /api/memory`, `GET /api/memory/{id}`, `/timeline`, `/search`,
  `/status`.
- Correct: `PUT /api/memory/{id}` supersedes the old record with a
  provenance-linked replacement.
- Disable: turn off `auto_memory` (per-user preference) or use incognito; the
  `use_memory` flag disables injection for a turn.
- Export: the UI exports the current `GET /api/memory` payload as JSON.
- Delete: `DELETE /api/memory/{id}` tombstones the record; recall stops
  immediately and the deletion path remains auditable.

## Acceptance and evaluation

```bash
python -m pytest tests/test_memory_governed_acceptance.py -q
```

The set runs deterministic fixtures with a scripted extractor (no network) and
covers useful recall, false recall, stale facts, correction, deletion with no
recall afterward, cross-owner isolation, extraction bounds/confidence, the
audit safety net, candidate non-promotion, and recall latency/token cost.
