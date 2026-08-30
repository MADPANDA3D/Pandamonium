import json

import httpx

from src.qdrant_projection import QdrantProjection


def test_qdrant_projection_creates_collection_and_projects_provenance(monkeypatch):
    monkeypatch.delenv("ODYSSEUS_QDRANT_MEMORY_COLLECTION", raising=False)
    monkeypatch.delenv("JARVIS_QDRANT_MEMORY_COLLECTION", raising=False)
    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(404, request=request)
        return httpx.Response(200, json={"result": True}, request=request)

    client = httpx.Client(
        base_url="http://qdrant.test",
        transport=httpx.MockTransport(handler),
    )
    projection = QdrantProjection(url="http://qdrant.test", client=client)
    memory = {
        "id": "memory-1",
        "text": "Leo prefers local-first systems",
        "owner": "leo",
        "status": "approved",
        "source_ref": "session:abc",
    }

    projection.upsert(memory, [0.1, 0.2, 0.3])

    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/collections/odysseus_memory"),
        ("PUT", "/collections/odysseus_memory"),
        ("PUT", "/collections/odysseus_memory/points"),
    ]
    payload = json.loads(requests[-1].content)
    assert payload["points"][0]["payload"] == {
        "text": "Leo prefers local-first systems",
        "owner": "leo",
        "status": "approved",
        "source_ref": "session:abc",
        "projection_schema": "jos-p3.1",
        "canonical_id": "memory-1",
    }


def test_qdrant_projection_is_disabled_without_configuration(monkeypatch):
    monkeypatch.delenv("QDRANT_URL", raising=False)
    projection = QdrantProjection()

    projection.upsert({"id": "memory-1", "status": "approved"}, [0.1])

    assert projection.stats()["enabled"] is False


def test_legacy_qdrant_environment_names_remain_compatible(monkeypatch):
    monkeypatch.delenv("ODYSSEUS_QDRANT_MEMORY_COLLECTION", raising=False)
    monkeypatch.delenv("ODYSSEUS_QDRANT_READS_ENABLED", raising=False)
    monkeypatch.setenv("JARVIS_QDRANT_MEMORY_COLLECTION", "existing_memories")
    monkeypatch.setenv("JARVIS_QDRANT_READS_ENABLED", "true")

    projection = QdrantProjection(url="http://qdrant.test", client=httpx.Client(
        base_url="http://qdrant.test",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"result": []}, request=request)),
    ))

    assert projection.collection == "existing_memories"
    assert projection.read_enabled is True


def test_qdrant_search_filters_owner_and_status_before_ranking(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={"result": {"points": [{"id": "memory-1", "score": 0.91}]}},
            request=request,
        )

    monkeypatch.setenv("JARVIS_QDRANT_READS_ENABLED", "true")
    client = httpx.Client(
        base_url="http://qdrant.test",
        transport=httpx.MockTransport(handler),
    )
    projection = QdrantProjection(url="http://qdrant.test", client=client)

    results = projection.search([0.1, 0.2], owner="leo", limit=3)

    assert results == [{
        "memory_id": "memory-1",
        "score": 0.91,
        "embedding_lane": "qdrant",
    }]
    payload = json.loads(requests[0].content)
    assert payload["filter"]["must"] == [
        {"key": "owner", "match": {"value": "leo"}},
        {"key": "status", "match": {"value": "approved"}},
    ]
