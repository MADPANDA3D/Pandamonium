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

## Brain operator surface

The Brain UI reports each storage/retrieval layer separately through
`GET /api/memory/status`; it must not imply that an optional projection is the
canonical store.

| Layer | Authority | Behavior |
| --- | --- | --- |
| Canonical memory | Owner-scoped `data/memory.json` ledger | Auditable source of approved recall records |
| Keyword recall | Canonical-record fallback | Remains available when semantic retrieval is degraded |
| Chroma | Local semantic projection | Optional and rebuildable; a missing service is reported as degraded |
| Qdrant | Optional remote/local projection | Write-only until reads are explicitly enabled and accepted |
| Local skills | Owner-scoped `data/skills/**/SKILL.md` bundles | May be reviewed, published, injected, and invoked under native policy |
| MAD MCP skills | Read-only Portal catalog metadata | Searchable discovery only; visibility never grants install or execution authority |

Text imports remain review-before-save. Full transcripts, archives, and mixed
memory exports should be retained as Library/RAG source material; only stable,
reviewed facts belong in the personal-memory ledger. Project-specific memory
stays with its project unless the operator explicitly promotes a fact to the
global owner layer.

Code-graph generators such as Graphify complement retrieval projections but do
not replace them: they derive relationship artifacts from explicitly selected
source roots. Any such generator remains an optional governed extension, with
dependency installation, source roots, and indexing runs separately approved.

### Optional Graphify code graph

Graphify is disabled unless an operator supplies `ODYSSEUS_GRAPHIFY_ROOTS` as
a JSON map of short root IDs to exact `repository_root` and isolated
`output_root` paths. The application never walks the workspace to discover
repositories and never builds a graph during startup. Build one admitted root
explicitly:

```bash
scripts/odysseus-graphify build --root-id project
```

The fixed build is local, code-only, single-worker, and writes outside the
repository. The optional MCP wrapper exposes only `graphify_status` and
`graphify_query`; callers select a configured root ID rather than submitting a
filesystem path. Upstream multi-project path selection and PR/network tools are
not exposed. Tool results are path-redacted and capped at 32 KiB. Graphify is
an optional runtime dependency and is not installed by the public default.

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

The `scripts/odysseus-qdrant-parity` diagnostic compares owner-filtered counts,
ranked canonical IDs, and cosine scores against the first canonical Chroma lane
without changing the production read flag:

```bash
scripts/odysseus-qdrant-parity \
  --owner OWNER_ID \
  --query "first representative query" \
  --query "second representative query"
```

Promotion remains an explicit operator deployment decision after parity,
owner isolation, rebuild, and deletion proof.

If Qdrant fails, canonical writes and Chroma recall continue. Unset
`QDRANT_URL` to roll back without converting memory or documents.

## Derived-wiki boundary

Wiki output remains optional derived knowledge, never canonical personal
memory. An installation may inventory its own vault and generated wiki only
after it supplies explicit roots. Dependency/build lineage, secrets, plugin
state, and pages without source links remain excluded. The public runtime
record contains no operator path, endpoint, inventory count, or plugin state.
