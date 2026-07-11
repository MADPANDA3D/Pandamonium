from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.chroma_client import get_chroma_client
from src.embeddings import FastEmbedClient
from src.rag_vector import VectorRAG

logger = logging.getLogger(__name__)

COLLECTION_NAME = "madpanda_knowledge_v1_fastembed"
DATA_DIR = Path(os.getenv("ODYSSEUS_DATA_DIR", "/srv/odysseus/data"))
MANIFEST_FILE = DATA_DIR / "madpanda_knowledge_v1_manifest.json"
SYNC_DIR = DATA_DIR / "madpanda_knowledge_sync"
PROPOSALS_FILE = DATA_DIR / "madpanda_knowledge_proposals.json"
AUDIT_FILE = DATA_DIR / "madpanda_knowledge_audit.jsonl"
AGENTS_FILE = Path(os.getenv("ODYSSEUS_KNOWLEDGE_AGENTS_FILE", "/etc/odysseus-knowledge-agents.json"))
LOCK = threading.RLock()
_STORE: "KnowledgeStore | None" = None


@dataclass(frozen=True)
class Agent:
    agent_id: str
    domains: tuple[str, ...]
    clients: tuple[str, ...]
    can_propose: bool = False
    can_pull_proposals: bool = False


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _agent(row: dict[str, Any]) -> Agent:
    return Agent(
        agent_id=str(row["agent_id"]),
        domains=tuple(str(value) for value in row.get("domains", [])),
        clients=tuple(str(value) for value in row.get("clients", [])),
        can_propose=bool(row.get("can_propose")),
        can_pull_proposals=bool(row.get("can_pull_proposals")),
    )


def agent_by_id(agent_id: str) -> Agent:
    for row in _read_json(AGENTS_FILE, {"agents": []}).get("agents", []):
        if row.get("agent_id") == agent_id:
            return _agent(row)
    raise PermissionError("unknown_agent")


def authenticate(authorization: str | None) -> Agent:
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    if not supplied:
        raise PermissionError("unauthorized")
    supplied_hash = hashlib.sha256(supplied.encode()).hexdigest()
    for row in _read_json(AGENTS_FILE, {"agents": []}).get("agents", []):
        expected = str(row.get("token_hash") or "")
        if expected and hmac.compare_digest(expected, supplied_hash):
            return _agent(row)
    raise PermissionError("unauthorized")


def _audit(agent: Agent, action: str, **details: Any) -> None:
    row = {"at": int(time.time()), "agent_id": agent.agent_id, "action": action, **details}
    try:
        with AUDIT_FILE.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, separators=(",", ":")) + "\n")
    except OSError:
        logger.warning("Could not write knowledge audit event")


class KnowledgeStore:
    def __init__(self) -> None:
        self.embedder = None
        self.collection = get_chroma_client().get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={
                "hnsw:space": "cosine",
                "embedding_lane": "fastembed",
                "embedding_model": "BAAI/bge-small-en-v1.5",
                "embedding_dimension": 384,
            },
        )

    def _ensure_embedder(self) -> Any:
        if self.embedder is not None:
            return self.embedder
        try:
            from src.rag_singleton import get_rag_manager

            legacy = get_rag_manager()
            self.embedder = next(
                lane.client for lane in legacy._lanes if lane.name == "fastembed"
            ) if legacy else None
        except Exception:
            self.embedder = None
        if self.embedder is None:
            self.embedder = FastEmbedClient()
        return self.embedder

    def _split(self, text: str) -> list[str]:
        # Reuse the proven legacy sentence-aware splitter without opening its collection.
        return VectorRAG._split_into_chunks(None, text)

    def _ids_for_source(self, source_id: str) -> list[str]:
        rows = self.collection.get(where={"source_id": source_id}, include=[])
        return list(rows.get("ids") or [])

    def _source_hash(self, source_id: str) -> str:
        rows = self.collection.get(where={"source_id": source_id}, include=["metadatas"], limit=1)
        metadata = rows.get("metadatas") or []
        return str(metadata[0].get("content_hash") or "") if metadata else ""

    def _all_source_ids(self) -> set[str]:
        rows = self.collection.get(include=["metadatas"])
        return {
            str(metadata.get("source_id"))
            for metadata in rows.get("metadatas") or []
            if metadata.get("source_id")
        }

    def _delete_source(self, source_id: str) -> int:
        ids = self._ids_for_source(source_id)
        if ids:
            self.collection.delete(ids=ids)
        return len(ids)

    def _upsert_source(self, doc: dict[str, Any], index_version: str) -> int:
        chunks = self._split(str(doc["text"]))
        if not chunks:
            return 0
        source_id = str(doc["source_id"])
        digest = str(doc["content_hash"])
        ids = [hashlib.sha256(f"{source_id}:{digest}:{number}".encode()).hexdigest() for number in range(len(chunks))]
        metadata = []
        for number in range(len(chunks)):
            metadata.append({
                "source_id": source_id,
                "source": str(doc["source"]),
                "title": str(doc.get("title") or ""),
                "heading": str(doc.get("heading") or doc.get("title") or ""),
                "domain": str(doc["domain"]),
                "client": str(doc.get("client") or ""),
                "sensitivity": str(doc.get("sensitivity") or "internal"),
                "authority": str(doc.get("authority") or "primary"),
                "source_type": str(doc.get("source_type") or "markdown"),
                "status": "active",
                "mtime": int(doc.get("mtime") or 0),
                "content_hash": digest,
                "index_version": index_version,
                "chunk_id": number,
            })
        for start in range(0, len(chunks), 25):
            batch_chunks = chunks[start:start + 25]
            embeddings = self._ensure_embedder().encode(batch_chunks, normalize_embeddings=True)
            if hasattr(embeddings, "tolist"):
                embeddings = embeddings.tolist()
            self.collection.upsert(
                ids=ids[start:start + 25],
                documents=batch_chunks,
                metadatas=metadata[start:start + 25],
                embeddings=embeddings,
            )
        stale = [row_id for row_id in self._ids_for_source(source_id) if row_id not in set(ids)]
        if stale:
            self.collection.delete(ids=stale)
        return len(ids)

    def sync_batch(
        self,
        sync_id: str,
        index_version: str,
        batch: int,
        documents: list[dict[str, Any]],
        final: bool,
    ) -> dict[str, Any]:
        if not sync_id or not index_version or batch < 0:
            raise ValueError("invalid_sync")
        if len(documents) > 100:
            raise ValueError("batch_too_large")
        with LOCK:
            SYNC_DIR.mkdir(parents=True, exist_ok=True)
            state_file = SYNC_DIR / f"{sync_id}.json"
            manifest = _read_json(MANIFEST_FILE, {"sources": {}})
            state = _read_json(state_file, {
                "sync_id": sync_id,
                "index_version": index_version,
                "seen": [],
                "sources": {},
                "batches": [],
                "chunks_added": 0,
                "unchanged": 0,
            })
            if state.get("index_version") != index_version:
                raise ValueError("sync_version_mismatch")
            if batch in state.get("batches", []):
                return {"ok": True, "duplicate_batch": batch, "finalized": False}
            seen = set(state.get("seen", []))
            sources = dict(state.get("sources", {}))
            for doc in documents:
                source_id = str(doc.get("source_id") or "")
                source = str(doc.get("source") or "")
                text = str(doc.get("text") or "").strip()
                if not source_id or not source or not text or len(text) > 2_000_000:
                    raise ValueError("invalid_document")
                seen.add(source_id)
                previous = sources.get(source_id) or manifest.get("sources", {}).get(source_id) or {}
                previous_hash = str(previous.get("content_hash") or self._source_hash(source_id))
                sources[source_id] = {
                    key: doc.get(key)
                    for key in ("source", "content_hash", "mtime", "domain", "client", "authority", "sensitivity")
                }
                if previous_hash == doc.get("content_hash"):
                    state["unchanged"] = int(state.get("unchanged", 0)) + 1
                    continue
                state["chunks_added"] = int(state.get("chunks_added", 0)) + self._upsert_source(doc, index_version)
                state.update(seen=sorted(seen), sources=sources)
                _write_json(state_file, state)
            state.update(seen=sorted(seen), sources=sources, batches=[*state.get("batches", []), batch])
            _write_json(state_file, state)
            removed = 0
            if final:
                stale = self._all_source_ids() - seen
                for source_id in stale:
                    removed += self._delete_source(source_id)
                _write_json(MANIFEST_FILE, {
                    "index_version": index_version,
                    "updated_at": int(time.time()),
                    "sources": sources,
                })
                for path in SYNC_DIR.glob("*.json"):
                    path.unlink(missing_ok=True)
            return {
                "ok": True,
                "sync_id": sync_id,
                "index_version": index_version,
                "batch": batch,
                "finalized": final,
                "sources": len(sources),
                "chunks_added": int(state.get("chunks_added", 0)),
                "chunks_removed": removed,
                "unchanged": int(state.get("unchanged", 0)),
            }

    def _domains(self, agent: Agent, domain: str | None, client: str | None) -> list[str]:
        if domain and domain not in agent.domains:
            raise PermissionError("domain_forbidden")
        if client:
            if "business_client" not in agent.domains:
                raise PermissionError("client_forbidden")
            if "*" not in agent.clients and client not in agent.clients:
                raise PermissionError("client_forbidden")
            if domain and domain != "business_client":
                raise ValueError("client_requires_business_client_domain")
            return ["business_client"]
        if domain == "business_client":
            raise ValueError("client_required")
        if domain:
            return [domain]
        return [value for value in agent.domains if value not in {"business_client", "wiki"}]

    def _query_domain(self, query: str, domain: str, client: str | None, limit: int, authority: str) -> list[dict[str, Any]]:
        clauses: list[dict[str, Any]] = [
            {"status": {"$eq": "active"}},
            {"domain": {"$eq": domain}},
            {"authority": {"$eq": authority}},
        ]
        if client:
            clauses.append({"client": {"$eq": client}})
        count = self.collection.count()
        if not count:
            return []
        results = self.collection.query(
            query_embeddings=self._ensure_embedder().encode([query], normalize_embeddings=True).tolist(),
            n_results=min(max(limit * 3, 10), count),
            where={"$and": clauses},
            include=["documents", "metadatas", "distances"],
        )
        rows = []
        query_words = set(query.casefold().split())
        for row_id, document, metadata, distance in zip(
            results.get("ids", [[]])[0],
            results.get("documents", [[]])[0],
            results.get("metadatas", [[]])[0],
            results.get("distances", [[]])[0],
        ):
            overlap = len(query_words & set(str(document).casefold().split())) / max(1, len(query_words))
            score = 0.7 * (1.0 - float(distance)) + 0.3 * overlap
            rows.append({"id": row_id, "text": document, "score": round(score, 4), **metadata})
        return rows

    def search(
        self,
        agent: Agent,
        query: str,
        domain: str | None = None,
        client: str | None = None,
        limit: int = 6,
        include_secondary: bool = False,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        domains = self._domains(agent, domain, client)
        candidates: list[dict[str, Any]] = []
        for permitted in domains:
            authority = "secondary" if permitted == "wiki" else "primary"
            candidates.extend(self._query_domain(query, permitted, client, limit, authority))
        candidates.sort(key=lambda row: row["score"], reverse=True)
        unique: list[dict[str, Any]] = []
        seen = set()
        for row in candidates:
            key = (row.get("source_id"), row.get("chunk_id"))
            if key in seen:
                continue
            seen.add(key)
            unique.append(row)
            if len(unique) >= limit:
                break
        if (include_secondary or len(unique) < 3) and "wiki" in agent.domains and domain in (None, "wiki"):
            wiki = self._query_domain(query, "wiki", None, limit, "secondary")
            for row in sorted(wiki, key=lambda item: item["score"], reverse=True):
                key = (row.get("source_id"), row.get("chunk_id"))
                if key not in seen:
                    seen.add(key)
                    unique.append(row)
                if len(unique) >= limit:
                    break
        manifest = _read_json(MANIFEST_FILE, {})
        _audit(
            agent,
            "search",
            domains=domains,
            client=client or "",
            results=len(unique),
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
        )
        return {
            "query": query,
            "index_version": manifest.get("index_version"),
            "stale": int(time.time()) - int(manifest.get("updated_at") or 0) > 3600,
            "results": unique[:limit],
        }

    def fetch(self, agent: Agent, source_id: str, chunk_id: int) -> dict[str, Any]:
        rows = self.collection.get(where={"source_id": source_id}, include=["documents", "metadatas"])
        selected = []
        total = 0
        for document, metadata in zip(rows.get("documents") or [], rows.get("metadatas") or []):
            domain = str(metadata.get("domain") or "")
            client = str(metadata.get("client") or "")
            if domain not in agent.domains or (client and "*" not in agent.clients and client not in agent.clients):
                continue
            if abs(int(metadata.get("chunk_id") or 0) - chunk_id) > 1:
                continue
            text = str(document)
            if total + len(text) > 12_000:
                text = text[: max(0, 12_000 - total)]
            selected.append({"text": text, **metadata})
            total += len(text)
            if total >= 12_000:
                break
        if not selected:
            raise PermissionError("source_not_found_or_forbidden")
        selected.sort(key=lambda row: int(row.get("chunk_id") or 0))
        _audit(agent, "fetch", source_id=source_id, chunks=len(selected))
        return {"source_id": source_id, "chunks": selected}

    def status(self, agent: Agent) -> dict[str, Any]:
        manifest = _read_json(MANIFEST_FILE, {})
        updated = int(manifest.get("updated_at") or 0)
        sources = manifest.get("sources", {})
        allowed = [row for row in sources.values() if row.get("domain") in agent.domains]
        return {
            "healthy": True,
            "agent_id": agent.agent_id,
            "domains": list(agent.domains),
            "clients": list(agent.clients),
            "index_version": manifest.get("index_version"),
            "last_sync": updated or None,
            "stale": not updated or int(time.time()) - updated > 3600,
            "sources": len(allowed),
            "chunks": self.collection.count(),
            "embedding": "BAAI/bge-small-en-v1.5 via FastEmbed CPU",
        }


def store() -> KnowledgeStore:
    global _STORE
    if _STORE is None:
        with LOCK:
            if _STORE is None:
                _STORE = KnowledgeStore()
    return _STORE


def sync_in_worker(payload: dict[str, Any]) -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = ""
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=DATA_DIR, prefix="knowledge-sync-", suffix=".json", delete=False) as stream:
            json.dump(payload, stream)
            path = stream.name
        os.chmod(path, 0o600)
        completed = subprocess.run(
            [sys.executable, "-m", "src.madpanda_knowledge", "sync-worker", path],
            cwd="/opt/odysseus",
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        if completed.returncode:
            raise RuntimeError((completed.stderr or "knowledge sync worker failed")[-500:])
        return json.loads(completed.stdout)
    finally:
        if path:
            Path(path).unlink(missing_ok=True)


def latest_sync_state() -> dict[str, Any]:
    paths = sorted(SYNC_DIR.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not paths:
        return {}
    state = _read_json(paths[0], {})
    return {
        "sync_id": state.get("sync_id"),
        "index_version": state.get("index_version"),
        "sources": len(state.get("seen", [])),
        "batches": list(state.get("batches", [])),
    }


def create_proposal(agent: Agent, payload: dict[str, Any]) -> dict[str, Any]:
    if not agent.can_propose:
        raise PermissionError("proposal_forbidden")
    title = str(payload.get("title") or "").strip()
    body = str(payload.get("body") or "").strip()
    domain = str(payload.get("domain") or "").strip()
    refs = [str(value)[:1000] for value in payload.get("source_refs", [])][:20]
    if not title or not body or len(body) > 12_000 or domain not in agent.domains:
        raise ValueError("invalid_proposal")
    row = {
        "proposal_id": str(uuid.uuid4()),
        "agent_id": agent.agent_id,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "title": title[:300],
        "body": body,
        "domain": domain,
        "source_refs": refs,
        "suggested_path": str(payload.get("suggested_path") or "")[:1000],
        "status": "pending",
    }
    with LOCK:
        state = _read_json(PROPOSALS_FILE, {"proposals": []})
        state.setdefault("proposals", []).append(row)
        _write_json(PROPOSALS_FILE, state)
    _audit(agent, "proposal_created", proposal_id=row["proposal_id"], domain=domain)
    return {"proposal_id": row["proposal_id"], "status": "pending"}


def list_proposals(agent: Agent) -> dict[str, Any]:
    if not agent.can_pull_proposals:
        raise PermissionError("proposal_pull_forbidden")
    rows = _read_json(PROPOSALS_FILE, {"proposals": []}).get("proposals", [])
    _audit(agent, "proposals_listed", results=len(rows))
    return {"proposals": rows[-200:]}


def self_check() -> None:
    row = {"agent_id": "x", "domains": ["shared"], "clients": []}
    assert _agent(row).domains == ("shared",)
    assert VectorRAG._split_into_chunks(None, "short") == ["short"]


if __name__ == "__main__" and len(sys.argv) == 3 and sys.argv[1] == "sync-worker":
    payload = _read_json(Path(sys.argv[2]), {})
    result = store().sync_batch(
        str(payload["sync_id"]),
        str(payload["index_version"]),
        int(payload["batch"]),
        list(payload.get("documents", [])),
        bool(payload.get("final")),
    )
    print(json.dumps(result))
