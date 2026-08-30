# Jarvis OS Memory and Provenance

**Protocol ID:** `JOS-P3`

**Version:** `0.1`

**Status:** Baseline contract

**Memory owner:** Odysseus

## Purpose

Jarvis memory must be useful, correctable, attributable, and removable. A
vector hit, transcript, generated wiki page, or model recollection is not by
itself a durable memory.

`JOS-P3` defines the boundary between canonical personal memory, source
documents, archived conversations, derived knowledge, and their retrieval
indexes. It also defines how ChatGPT, Manus, Obsidian, and other sources may
backfill Jarvis without dumping their full history into memory.

## Memory and knowledge layers

| Layer | Canonical owner | Role |
| --- | --- | --- |
| Personal memory | Odysseus memory records | Approved facts, preferences, relationships, decisions |
| Conversation archive | Odysseus/source export | Searchable historical evidence, not automatic memory |
| Lab documentation | Source files and Obsidian vault | Canonical project and operating knowledge |
| Generated wiki | KarpathyWiki output | Derived concept/entity/source synthesis |
| Retrieval projections | Qdrant or existing Chroma indexes | Disposable search acceleration |
| Skills | `JOS-P6` governed skill files | Reusable procedures, not personal facts |

Indexes MUST be rebuildable from canonical records and sources. Deleting an
index must not delete canonical memory or documents.

## Canonical memory record

An approved memory MUST have at least:

| Field | Meaning |
| --- | --- |
| `memory_id` | Stable Odysseus identifier |
| `owner_id` | Authenticated owner |
| `text` | Compact asserted fact or preference |
| `category` | Fact, preference, relationship, decision, or other controlled class |
| `status` | Candidate, approved, rejected, superseded, or deleted |
| `source_ref` | Source system and stable locator |
| `source_time` | Time of the underlying source when known |
| `admitted_at` | Time Odysseus approved the memory |
| `admitted_by` | Operator or approved admission policy |
| `supersedes` | Prior memory corrected by this record, when applicable |

Provider-specific metadata may extend the record but cannot replace owner,
status, or provenance.

## Admission rules

A memory may enter as a candidate from:

- Leo's explicit request to remember something;
- the existing post-conversation extraction flow;
- a reviewed ChatGPT, Manus, Hermes, or generic migration manifest;
- a correction or consolidation proposal;
- an authenticated external memory provider.

Odysseus MUST validate ownership, normalize the text, preserve the source,
check exact and semantic duplicates, detect likely conflicts, and apply the
configured review policy before approval.

Model output, retrieved text, and imported history MUST NOT silently become an
approved memory. An automatic admission policy is allowed only when Leo enables
it explicitly, the candidate class is allowed, provenance is retained, and the
action remains reversible.

## Recall rules

Recall is a read operation. It MUST:

- filter by authenticated owner before semantic ranking;
- return only approved, non-deleted records unless a review surface requests
  candidates;
- carry `memory_id`, source reference, status, score, and provider identity;
- deduplicate equivalent results across providers and indexes;
- remain bounded by the `JOS-P2` attention budget;
- treat memory text as untrusted source data in the engine prompt;
- never increase a memory's authority merely because it is retrieved often.

Usage counts may improve ranking but are not proof that a memory is true.

## Correction, consolidation, and deletion

Leo MUST be able to inspect, edit, reject, supersede, merge, and delete memory.

- Corrections create an attributable replacement or update rather than relying
  on a contradictory new vector.
- Consolidation preserves the source references of every merged record.
- Deletion removes the record from recall and propagates to every projection.
- Failed projection cleanup leaves a visible repair task and MUST NOT resurrect
  the memory on the next rebuild.
- Rebuilding an index uses current canonical status, not stale indexed text.

## Selected storage topology

The target topology is:

1. Odysseus records remain the canonical personal-memory store during the
   migration.
2. Qdrant becomes the semantic retrieval projection for approved personal
   memory and canonical documentation.
3. Personal memory, canonical documents, and generated wiki content use
   separate collections or mandatory type/owner partitions so they cannot be
   confused at recall time.
4. The existing Chroma-backed tool index remains in place; changing tool
   selection storage is not required for Qdrant memory/document adoption.
5. Existing Chroma memory and RAG indexes remain available until Qdrant parity,
   rebuild, deletion, owner-isolation, and rollback tests pass.

Qdrant is an index, not Jarvis's source of truth.

## Obsidian and KarpathyWiki

Obsidian and the lab filesystem remain the authoring and canonical-document
surfaces. The ingestion pipeline records path, content hash, modified time,
document class, and owner or visibility scope.

KarpathyWiki is a secondary knowledge compiler:

```text
canonical docs -> scoped KarpathyWiki generation -> derived wiki -> wiki index
```

Generated pages MUST retain links to their input sources and generation
version. They are useful for concept/entity navigation and synthesis, but they
cannot overwrite source documentation or personal memory. Vendor trees,
dependencies, generated build output, secrets, and other excluded paths MUST be
filtered before generation.

## ChatGPT and Manus backfill

Backfill uses the existing source-neutral migration pattern:

```text
provider export -> source adapter -> agent-migration.v1 -> preview -> staged apply
```

The apply flow MUST:

1. inventory counts, warnings, duplicates, and privacy-sensitive content;
2. archive conversations as searchable source material first;
3. extract compact memory candidates with links to the source conversation;
4. group duplicates, conflicts, stale facts, and low-value candidates;
5. show Leo a review/consolidation surface;
6. admit only approved candidates through the normal memory path;
7. skip credentials and hidden provider state by default;
8. remain resumable and idempotent for large exports.

ChatGPT and Manus adapters understand their export shapes. The core admission
contract remains source-neutral.

## Current implementation anchors

| Responsibility | Existing anchor |
| --- | --- |
| Canonical native memory | `src/memory.py` |
| Provider-neutral record and provider registry | `src/memory_provider.py` |
| Current Chroma memory projection | `src/memory_vector.py` |
| Memory review, audit, edit, import, and deletion | `routes/memory/memory_routes.py` |
| Personal-document RAG | `src/personal_docs.py`, `src/rag_vector.py` |
| Curated lab knowledge and audit | `src/madpanda_knowledge.py` |
| Knowledge API | `routes/madpanda_knowledge_routes.py` |
| Source-neutral migration manifest | `docs/agent-migration.md`, `scripts/agent_migration_manifest.py` |
| Embedding model separation | `src/embedding_lanes.py` |

Current memory records do not yet require the complete provenance/status
contract, and Qdrant is not implemented. Native memory, personal RAG, and
curated knowledge also use separate paths that need one owner-safe admission
and projection policy rather than an immediate rewrite.

## Compatibility gate

`JOS-P3` is satisfied only when these pass:

- owner-isolated add, recall, correction, consolidation, and deletion;
- exact and semantic duplicate detection without cross-owner leakage;
- Qdrant projection rebuild from canonical memory and documents;
- deletion and supersession parity across native and Qdrant recall;
- one ChatGPT and one generic/Manus-style manifest dry run with no writes;
- reviewed import that preserves source-thread locators;
- canonical document results outrank conflicting generated-wiki results;
- excluded KarpathyWiki paths never enter generation or indexing;
- Qdrant outage degrades recall without losing canonical records;
- rollback to the existing native/Chroma path requires no memory conversion.

## Definition of success

`JOS-P3` succeeds when Jarvis can recall useful personal and lab knowledge with
source proof, while Leo can see where every memory came from, correct it once,
delete it everywhere, and rebuild every search index from canonical state.

## Non-goals

- Treating whole transcripts or the whole vault as personal memory.
- Making KarpathyWiki the canonical source.
- Replacing the existing tool index with Qdrant.
- Importing ChatGPT or Manus data before Leo supplies and reviews the exports.
- Selecting a graph database or knowledge-graph framework before the derived
  wiki proves that need.
