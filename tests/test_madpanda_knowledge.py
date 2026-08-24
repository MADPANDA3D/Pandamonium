from pathlib import Path

import pytest

from src import madpanda_knowledge as knowledge


def test_agent_scope_blocks_client_without_client_name():
    store = knowledge.KnowledgeStore.__new__(knowledge.KnowledgeStore)
    agent = knowledge.Agent("jarvis", ("shared", "business_client"), ("*",))
    assert store._domains(agent, None, None) == ["shared"]
    with pytest.raises(ValueError, match="client_required"):
        store._domains(agent, "business_client", None)


def test_auth_uses_hashes(tmp_path: Path, monkeypatch):
    registry = tmp_path / "agents.json"
    registry.write_text(
        '{"agents":[{"agent_id":"hermes","token_hash":"2bb80d537b1da3e38bd30361aa855686bde0eacd7162fef6a25fe97bf527a25b","domains":["shared"],"clients":[]}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(knowledge, "AGENTS_FILE", registry)
    assert knowledge.authenticate("Bearer secret").agent_id == "hermes"
    with pytest.raises(PermissionError):
        knowledge.authenticate("Bearer wrong")


def test_large_source_embeddings_are_batched():
    class Embedder:
        calls = []
        def encode(self, texts, normalize_embeddings=True):
            self.calls.append(len(texts))
            return [[0.0] for _ in texts]

    class Collection:
        ids = []
        def upsert(self, ids, documents, metadatas, embeddings):
            self.ids.extend(ids)
        def get(self, where=None, include=None):
            return {"ids": list(self.ids)}
        def delete(self, ids):
            raise AssertionError("fresh source must not delete new chunks")

    store = knowledge.KnowledgeStore.__new__(knowledge.KnowledgeStore)
    store.embedder = Embedder()
    store.collection = Collection()
    store._split = lambda _text: [f"chunk {index}" for index in range(205)]
    count = store._upsert_source({
        "source_id": "source",
        "source": "note.md",
        "domain": "shared",
        "content_hash": "hash",
        "text": "body",
    }, "v1")
    assert count == 205
    assert store.embedder.calls == [25, 25, 25, 25, 25, 25, 25, 25, 5]


def test_knowledge_embedder_is_isolated_from_generic_rag(monkeypatch):
    created = []
    expected = object()
    monkeypatch.setattr(
        knowledge,
        "FastEmbedClient",
        lambda model: created.append(model) or expected,
    )
    store = knowledge.KnowledgeStore.__new__(knowledge.KnowledgeStore)
    store.embedder = None

    assert store._ensure_embedder() is expected
    assert created == [knowledge.KNOWLEDGE_EMBEDDING_MODEL]
    assert knowledge.KNOWLEDGE_EMBEDDING_MODEL == "BAAI/bge-small-en-v1.5"


def test_latest_sync_state_returns_completed_manifest(tmp_path: Path, monkeypatch):
    sync_dir = tmp_path / "sync"
    sync_dir.mkdir()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        '{"index_version":"stamp-contenthash","sources":{"a":{},"b":{}}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(knowledge, "SYNC_DIR", sync_dir)
    monkeypatch.setattr(knowledge, "MANIFEST_FILE", manifest)

    assert knowledge.latest_sync_state() == {
        "sync_id": None,
        "index_version": "stamp-contenthash",
        "content_fingerprint": knowledge._manifest_fingerprint({"a": {}, "b": {}}),
        "source_fingerprints": {
            source_id: knowledge._source_fingerprint({"source_id": source_id})
            for source_id in ("a", "b")
        },
        "sources": 2,
        "batches": [],
        "finalized": True,
    }


def test_delta_sync_preserves_unchanged_sources_and_removes_missing(tmp_path: Path, monkeypatch):
    sync_dir = tmp_path / "sync"
    manifest = tmp_path / "manifest.json"
    sync_dir.mkdir()
    manifest.write_text(
        '{"sources":{"a":{"source":"a.md","content_hash":"old"},'
        '"b":{"source":"b.md","content_hash":"gone"}}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(knowledge, "SYNC_DIR", sync_dir)
    monkeypatch.setattr(knowledge, "MANIFEST_FILE", manifest)
    store = knowledge.KnowledgeStore.__new__(knowledge.KnowledgeStore)
    monkeypatch.setattr(store, "_all_source_ids", lambda: {"a", "b"})
    removed = []
    monkeypatch.setattr(store, "_delete_source", lambda source_id: removed.append(source_id) or 1)
    monkeypatch.setattr(store, "_source_hash", lambda _source_id: "")
    monkeypatch.setattr(store, "_upsert_source", lambda _doc, _version: 1)

    result = store.sync_batch(
        "sync-1",
        "version-1",
        0,
        [{
            "source_id": "c",
            "source": "c.md",
            "content_hash": "new",
            "mtime": 1,
            "domain": "home_lab",
            "client": "",
            "authority": "primary",
            "sensitivity": "internal",
            "text": "new document",
        }],
        True,
        ["a", "c"],
    )

    saved = knowledge._read_json(manifest, {})
    assert set(saved["sources"]) == {"a", "c"}
    assert removed == ["b"]
    assert result["sources"] == 2
