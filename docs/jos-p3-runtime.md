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
`JARVIS_QDRANT_READS_ENABLED=false` until rebuild, owner isolation, deletion,
outage, and rollback checks pass against a live Qdrant service.

If Qdrant fails, canonical writes and Chroma recall continue. Unset
`QDRANT_URL` to roll back without converting memory or documents.

## Current KarpathyWiki finding

The live vault is `/home/leo/the-lab`; KarpathyWiki `1.22.1` is installed and
the `wiki/` output contains 2,851 Markdown pages. The plugin is not ready to
generate: its custom endpoint points at local Ollama, but no model or watched
folders are selected and `llmReady` is false.

The P3 inventory currently admits 1,376 pages and excludes 1,475: 1,431 have
dependency/build lineage and 44 lack source links. This confirms that the wiki
can add derived navigation value, but it must be regenerated from a scoped
canonical source set before broad indexing.

Reproduce the read-only inventory:

```bash
.venv/bin/python scripts/jarvis_knowledge_inventory.py \
  --vault-root /home/leo/the-lab \
  --wiki-root /home/leo/the-lab/wiki \
  --plugin-manifest /home/leo/the-lab/.obsidian/plugins/karpathywiki/manifest.json
```

No plugin settings, watched folders, Qdrant service, or existing wiki pages are
changed by this baseline.
