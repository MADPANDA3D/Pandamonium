import httpx

from src.qdrant_projection import QdrantProjection
from src.qdrant_parity import memory_projection_parity


def test_shadow_parity_can_read_without_promoting_production_reads(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={"result": {"points": [{"id": "point-1", "score": 0.99, "payload": {"canonical_id": "memory-1"}}]}},
            request=request,
        )

    monkeypatch.setenv("ODYSSEUS_QDRANT_READS_ENABLED", "false")
    projection = QdrantProjection(
        url="http://qdrant.test",
        client=httpx.Client(
            base_url="http://qdrant.test",
            transport=httpx.MockTransport(handler),
        ),
    )

    assert projection.read_enabled is False
    assert projection.search([0.1, 0.2], owner="leo", limit=3) == []
    assert projection.search_for_parity([0.1, 0.2], owner="leo", limit=3) == [{
        "memory_id": "memory-1",
        "score": 0.99,
        "embedding_lane": "qdrant",
    }]
    assert projection.read_enabled is False
    assert len(requests) == 1


def test_shadow_parity_compares_counts_ids_and_scores_without_enabling_reads():
    class Collection:
        def get(self, **_kwargs):
            return {"ids": ["memory-1", "memory-2"]}

        def query(self, **_kwargs):
            return {"ids": [["memory-1", "memory-2"]], "distances": [[0.1, 0.2]]}

    class Lane:
        collection = Collection()

        @staticmethod
        def encode(_texts):
            return [[0.1, 0.2]]

    class Projection:
        enabled = True
        read_enabled = False

        @staticmethod
        def count_for_parity(*, owner):
            assert owner == "leo"
            return 2

        @staticmethod
        def search_for_parity(_vector, *, owner, limit):
            assert owner == "leo"
            assert limit == 5
            return [
                {"memory_id": "memory-1", "score": 0.9, "embedding_lane": "qdrant"},
                {"memory_id": "memory-2", "score": 0.8, "embedding_lane": "qdrant"},
            ]

    store = type("Store", (), {"_qdrant": Projection(), "_lanes": [Lane()]})()

    report = memory_projection_parity(
        store,
        owner="leo",
        queries=["local first systems"],
    )

    assert report["passed"] is True
    assert report["production_reads_enabled"] is False
    assert report["chroma_count"] == report["qdrant_count"] == 2
