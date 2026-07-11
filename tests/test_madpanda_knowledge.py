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
