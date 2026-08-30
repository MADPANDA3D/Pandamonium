from src.embedding_lanes import (
    EmbeddingLane,
    LANE_CUSTOM,
    LANE_FASTEMBED,
)
from tests.helpers.embedding_lanes import (
    FakeChroma,
    FakeCollection,
    FakeEmbedder,
    FailingEmbedder,
    patch_chroma,
)


def test_memory_vector_store_writes_both_lanes_and_prefers_custom(monkeypatch):
    fake = FakeChroma()
    patch_chroma(monkeypatch, fake)

    import src.embedding_lanes as lanes

    monkeypatch.setattr(lanes, "_build_custom_client", lambda: FakeEmbedder(768, "nomic", "http://embeddings/v1"))
    monkeypatch.setattr(lanes, "_build_fastembed_client", lambda: FakeEmbedder(384, "mini", "local://fastembed"))

    from src.memory_vector import MemoryVectorStore

    store = MemoryVectorStore("data")
    store.add("mem-1", "Nicholai likes direct memory systems")

    assert fake.collections["odysseus_memories_custom"].count() == 1
    assert fake.collections["odysseus_memories_fastembed"].count() == 1

    results = store.search("direct memory", k=5)
    assert results[0]["memory_id"] == "mem-1"
    assert results[0]["embedding_lane"] == LANE_CUSTOM


def test_memory_vector_owner_filter_applies_before_ranking(monkeypatch):
    fake = FakeChroma()
    patch_chroma(monkeypatch, fake)

    import src.embedding_lanes as lanes

    monkeypatch.setattr(lanes, "_build_custom_client", lambda: None)
    monkeypatch.setattr(lanes, "_build_fastembed_client", lambda: FakeEmbedder(384, "mini", "local://fastembed"))

    from src.memory_vector import MemoryVectorStore

    store = MemoryVectorStore("data")
    store.add("alice-memory", "shared topic", owner="alice")
    store.add("leo-memory", "shared topic", owner="leo")

    results = store.search("shared topic", owner="leo", k=5)

    assert [result["memory_id"] for result in results] == ["leo-memory"]


def test_memory_rebuild_projects_only_approved_records(monkeypatch):
    fake = FakeChroma()
    patch_chroma(monkeypatch, fake)

    import src.embedding_lanes as lanes

    monkeypatch.setattr(lanes, "_build_custom_client", lambda: None)
    monkeypatch.setattr(lanes, "_build_fastembed_client", lambda: FakeEmbedder(384, "mini", "local://fastembed"))

    from src.memory_vector import MemoryVectorStore

    store = MemoryVectorStore("data")
    store.rebuild([
        {"id": "approved", "text": "safe", "status": "approved", "owner": "leo"},
        {"id": "candidate", "text": "review me", "status": "candidate", "owner": "leo"},
    ])

    rows = fake.collections["odysseus_memories_fastembed"].get()
    assert rows["ids"] == ["approved"]
    assert rows["metadatas"][0]["projection_schema"] == "jos-p3.1"
    assert rows["metadatas"][0]["owner"] == "leo"


def test_memory_search_merges_fallback_only_results_before_limit():
    custom_collection = FakeCollection("odysseus_memories_custom", metadata={"embedding_lane": "custom"})
    fast_collection = FakeCollection("odysseus_memories_fastembed", metadata={"embedding_lane": "fastembed"})
    custom_collection.add(
        ids=["old-1", "old-2"],
        embeddings=[[0.0] * 768, [0.0] * 768],
        documents=["older custom memory", "another custom memory"],
        metadatas=[{"source": "memory"}, {"source": "memory"}],
    )
    fast_collection.add(
        ids=["fallback-only"],
        embeddings=[[0.0] * 384],
        documents=["fallback only relevant memory"],
        metadatas=[{"source": "memory"}],
    )

    custom_collection.query = lambda **_kwargs: {
        "ids": [["old-1", "old-2"]],
        "distances": [[0.20, 0.21]],
    }
    fast_collection.query = lambda **_kwargs: {
        "ids": [["fallback-only"]],
        "distances": [[0.05]],
    }

    custom_lane = EmbeddingLane(
        name=LANE_CUSTOM,
        client=FakeEmbedder(768, "nomic", "http://embeddings/v1"),
        collection=custom_collection,
        collection_name="odysseus_memories_custom",
        model="nomic",
        url="http://embeddings/v1",
        dimension=768,
        fingerprint="custom",
    )
    fast_lane = EmbeddingLane(
        name=LANE_FASTEMBED,
        client=FakeEmbedder(384, "mini", "local://fastembed"),
        collection=fast_collection,
        collection_name="odysseus_memories_fastembed",
        model="mini",
        url="local://fastembed",
        dimension=384,
        fingerprint="fast",
    )

    from src.memory_vector import MemoryVectorStore

    store = MemoryVectorStore.__new__(MemoryVectorStore)
    store._lanes = [custom_lane, fast_lane]
    store._healthy = True

    results = store.search("fallback relevant", k=2)

    assert [row["memory_id"] for row in results] == ["fallback-only", "old-1"]


def test_qdrant_outage_falls_back_to_chroma_recall():
    collection = FakeCollection("odysseus_memories_fastembed")
    collection.add(
        ids=["memory-1"],
        embeddings=[[0.0] * 384],
        documents=["canonical memory"],
        metadatas=[{
            "source": "memory",
            "projection_schema": "jos-p3.1",
            "owner": "leo",
            "status": "approved",
            "source_ref": "session:one",
        }],
    )
    lane = EmbeddingLane(
        name=LANE_FASTEMBED,
        client=FakeEmbedder(384, "mini", "local://fastembed"),
        collection=collection,
        collection_name="odysseus_memories_fastembed",
        model="mini",
        url="local://fastembed",
        dimension=384,
        fingerprint="fast",
    )

    class FailedQdrant:
        read_enabled = True

        def search(self, *_args, **_kwargs):
            raise RuntimeError("qdrant unavailable")

    from src.memory_vector import MemoryVectorStore

    store = MemoryVectorStore.__new__(MemoryVectorStore)
    store._lanes = [lane]
    store._healthy = True
    store._qdrant = FailedQdrant()

    results = store.search("canonical", owner="leo", k=3)

    assert [row["memory_id"] for row in results] == ["memory-1"]


def test_memory_rebuild_does_not_reimport_legacy_collection(monkeypatch):
    fake = FakeChroma()
    legacy = fake.get_or_create_collection("odysseus_memories", metadata={"hnsw:space": "cosine"})
    legacy.add(
        ids=["stale-memory"],
        embeddings=[[0.0] * 384],
        documents=["stale legacy memory"],
        metadatas=[{"source": "memory"}],
    )
    inactive_custom = fake.get_or_create_collection("odysseus_memories_custom", metadata={"embedding_lane": "custom"})
    inactive_custom.add(
        ids=["stale-custom"],
        embeddings=[[0.0] * 768],
        documents=["stale inactive custom memory"],
        metadatas=[{"source": "memory"}],
    )
    patch_chroma(monkeypatch, fake)

    import src.embedding_lanes as lanes

    monkeypatch.setattr(lanes, "_build_custom_client", lambda: None)
    monkeypatch.setattr(lanes, "_build_fastembed_client", lambda: FakeEmbedder(384, "mini", "local://fastembed"))

    from src.memory_vector import MemoryVectorStore

    store = MemoryVectorStore("data")
    assert fake.collections["odysseus_memories_fastembed"].count() == 1

    store.rebuild([{"id": "current-memory", "text": "current rebuilt memory"}])

    assert "odysseus_memories" not in fake.collections
    assert "odysseus_memories_custom" not in fake.collections
    assert fake.collections["odysseus_memories_fastembed"].count() == 1
    assert fake.collections["odysseus_memories_fastembed"].get()["ids"] == ["current-memory"]


def test_memory_remove_deletes_inactive_lane_collection(monkeypatch):
    fake = FakeChroma()
    custom_collection = fake.get_or_create_collection("odysseus_memories_custom", metadata={"embedding_lane": "custom"})
    fast_collection = fake.get_or_create_collection("odysseus_memories_fastembed", metadata={"embedding_lane": "fastembed"})
    custom_collection.add(
        ids=["mem-1"],
        embeddings=[[0.0] * 768],
        documents=["custom stale memory"],
        metadatas=[{"source": "memory"}],
    )
    fast_collection.add(
        ids=["mem-1"],
        embeddings=[[0.0] * 384],
        documents=["fast memory"],
        metadatas=[{"source": "memory"}],
    )
    patch_chroma(monkeypatch, fake)

    fast_lane = EmbeddingLane(
        name=LANE_FASTEMBED,
        client=FakeEmbedder(384, "mini", "local://fastembed"),
        collection=fast_collection,
        collection_name="odysseus_memories_fastembed",
        model="mini",
        url="local://fastembed",
        dimension=384,
        fingerprint="fast",
    )

    from src.memory_vector import MemoryVectorStore

    store = MemoryVectorStore.__new__(MemoryVectorStore)
    store._lanes = [fast_lane]
    store._healthy = True

    store.remove("mem-1")

    assert custom_collection.count() == 0
    assert fast_collection.count() == 0


def test_memory_rebuild_continues_when_custom_lane_fails(monkeypatch):
    fake = FakeChroma()
    patch_chroma(monkeypatch, fake)

    import src.embedding_lanes as lanes

    monkeypatch.setattr(lanes, "_build_custom_client", lambda: FailingEmbedder(768, "nomic", "http://embeddings/v1"))
    monkeypatch.setattr(lanes, "_build_fastembed_client", lambda: FakeEmbedder(384, "mini", "local://fastembed"))

    from src.memory_vector import MemoryVectorStore

    store = MemoryVectorStore("data")
    store.rebuild([{"id": "current-memory", "text": "current rebuilt memory"}])

    assert fake.collections["odysseus_memories_custom"].count() == 0
    assert fake.collections["odysseus_memories_fastembed"].count() == 1
    assert fake.collections["odysseus_memories_fastembed"].get()["ids"] == ["current-memory"]
