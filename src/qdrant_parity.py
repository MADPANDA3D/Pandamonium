"""Diagnostic Chroma/Qdrant memory parity with production reads kept off."""

from __future__ import annotations

from typing import Any, Iterable


def _where(owner: str) -> dict[str, Any]:
    return {
        "$and": [
            {"owner": {"$eq": owner}},
            {"status": {"$eq": "approved"}},
        ]
    }


def memory_projection_parity(
    store,
    *,
    owner: str,
    queries: Iterable[str],
    limit: int = 5,
    score_tolerance: float = 0.001,
) -> dict[str, Any]:
    """Compare the canonical first Chroma lane with the Qdrant shadow."""
    qdrant = getattr(store, "_qdrant", None)
    lanes = list(getattr(store, "_lanes", []) or [])
    if not owner or qdrant is None or not qdrant.enabled or not lanes:
        raise ValueError("qdrant_parity_unavailable")
    if qdrant.read_enabled:
        raise ValueError("qdrant_reads_must_remain_disabled_during_parity")
    lane = lanes[0]
    bounded_limit = min(max(int(limit), 1), 20)

    chroma_ids = lane.collection.get(where=_where(owner), include=[]).get("ids") or []
    qdrant_count = qdrant.count_for_parity(owner=owner)
    rows = []
    for raw_query in queries:
        query = str(raw_query or "").strip()
        if not query or len(query) > 2_000:
            raise ValueError("qdrant_parity_query_invalid")
        vector = lane.encode([query])[0]
        chroma_raw = lane.collection.query(
            query_embeddings=[vector],
            n_results=min(bounded_limit, max(1, len(chroma_ids))),
            where=_where(owner),
            include=["distances"],
        )
        ids = (chroma_raw.get("ids") or [[]])[0]
        distances = (chroma_raw.get("distances") or [[]])[0]
        chroma = [
            {"memory_id": memory_id, "score": 1.0 - float(distances[index])}
            for index, memory_id in enumerate(ids)
        ]
        shadow = qdrant.search_for_parity(vector, owner=owner, limit=bounded_limit)
        same_ids = [item["memory_id"] for item in chroma] == [
            item["memory_id"] for item in shadow
        ]
        score_deltas = [
            abs(chroma[index]["score"] - shadow[index]["score"])
            for index in range(min(len(chroma), len(shadow)))
        ]
        rows.append({
            "query": query,
            "chroma_ids": [item["memory_id"] for item in chroma],
            "qdrant_ids": [item["memory_id"] for item in shadow],
            "same_ids": same_ids,
            "max_score_delta": round(max(score_deltas, default=0.0), 6),
            "passed": same_ids
            and len(chroma) == len(shadow)
            and all(delta <= score_tolerance for delta in score_deltas),
        })
    return {
        "schema_version": "jos-p3.qdrant-parity.v1",
        "production_reads_enabled": qdrant.read_enabled,
        "owner": owner,
        "chroma_count": len(chroma_ids),
        "qdrant_count": qdrant_count,
        "queries": rows,
        "passed": len(chroma_ids) == qdrant_count and bool(rows) and all(
            row["passed"] for row in rows
        ),
    }
