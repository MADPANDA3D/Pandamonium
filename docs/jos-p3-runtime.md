# JOS P3 runtime baseline

Odysseus remains the canonical personal-memory owner. `data/memory.json` is the
auditable ledger; Chroma and optional Qdrant collections are disposable
retrieval projections.

## Implemented boundaries

- Canonical records normalize to `memory_id`, `owner_id`, `status`,
  `source_ref`, `source_time`, `admitted_at`, `admitted_by`, and `supersedes`.
- Normal recall returns only approved records and filters owner metadata before
  semantic ranking.
- Corrections create a new record and supersede the old record. Deletion leaves
  a tombstone and removes the projected point.
- `agent-migration.v1` manifests have write-free preview, privacy/duplicate
  inventory, candidate staging, and explicit approve/reject endpoints.
- Qdrant uses separate personal-memory, canonical-document, and generated-wiki
  collections. Writes are optional mirrors; reads remain disabled until live
  parity is accepted. Chroma stays available as rollback.
- Canonical knowledge is returned before secondary wiki synthesis.
- Wiki inventory records content hash, modified time, class, lineage links, and
  generator version. Dependency, build, VCS, Obsidian plugin, and secret paths
  are excluded.

## Migration API

| Method and path | Effect |
| --- | --- |
| `POST /api/memory/migration/preview` | Validate and inventory a manifest with zero writes |
| `POST /api/memory/migration/stage` | Stage safe memory items as non-recallable candidates |
| `GET /api/memory/migration/candidates` | List the authenticated owner's review queue |
| `POST /api/memory/migration/candidates/{id}/approve` | Admit and project one candidate |
| `POST /api/memory/migration/candidates/{id}/reject` | Reject one candidate without recall |

Conversation threads and archive documents remain source material; the stage
endpoint does not convert them into personal memory.

## Qdrant promotion controls

Set `QDRANT_URL` to enable projection writes. `QDRANT_API_KEY` is optional for a
protected endpoint. Collection names can be overridden independently. Leave
`ODYSSEUS_QDRANT_READS_ENABLED=false` until rebuild, owner isolation, deletion,
outage, and rollback checks pass against a live Qdrant service.

New installations use `odysseus_memory`, `odysseus_documents`, and
`odysseus_wiki`. The generic `ODYSSEUS_QDRANT_*` variables take precedence;
legacy `JARVIS_QDRANT_*` variables and existing MADPANDA knowledge data paths
remain compatibility aliases. The canonical ledger/source set can rebuild any
projection.

If Qdrant fails, canonical writes and Chroma recall continue. Unset
`QDRANT_URL` to roll back without converting memory or documents.

## Derived-wiki boundary

Wiki output remains optional derived knowledge, never canonical personal
memory. An installation may inventory its own vault and generated wiki only
after it supplies explicit roots. Dependency/build lineage, secrets, plugin
state, and pages without source links remain excluded. The public runtime
record contains no operator path, endpoint, inventory count, or plugin state.
