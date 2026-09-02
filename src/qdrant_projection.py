"""Optional Qdrant projection for canonical Odysseus memory records.

The JSON memory ledger remains authoritative. This adapter only mirrors
approved records and can be rebuilt or disabled without data loss.
"""

from __future__ import annotations

import os
import uuid
from typing import Dict, Iterable, List, Optional

import httpx


class QdrantProjection:
    DEFAULT_COLLECTION = "odysseus_memory"

    def __init__(
        self,
        url: Optional[str] = None,
        *,
        api_key: Optional[str] = None,
        collection: Optional[str] = None,
        client: Optional[httpx.Client] = None,
    ):
        self.url = (url if url is not None else os.getenv("QDRANT_URL", "")).rstrip("/")
        self.collection = (
            collection
            or os.getenv("ODYSSEUS_QDRANT_MEMORY_COLLECTION")
            or os.getenv("JARVIS_QDRANT_MEMORY_COLLECTION")
            or self.DEFAULT_COLLECTION
        )
        self.enabled = bool(self.url)
        read_setting = (
            os.getenv("ODYSSEUS_QDRANT_READS_ENABLED")
            or os.getenv("JARVIS_QDRANT_READS_ENABLED")
            or "false"
        )
        self.read_enabled = self.enabled and read_setting.strip().lower() in {"1", "true", "yes", "on"}
        self.healthy = self.enabled
        self.last_error = ""
        self._vector_size: Optional[int] = None
        headers = {}
        resolved_key = api_key if api_key is not None else os.getenv("QDRANT_API_KEY", "")
        if resolved_key:
            headers["api-key"] = resolved_key
        self._client = client or (
            httpx.Client(base_url=self.url, headers=headers, timeout=10.0)
            if self.enabled
            else None
        )

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        if not self.enabled or self._client is None:
            raise RuntimeError("Qdrant projection is disabled")
        try:
            response = self._client.request(method, path, **kwargs)
            response.raise_for_status()
            self.healthy = True
            self.last_error = ""
            return response
        except Exception as exc:
            self.healthy = False
            self.last_error = str(exc)
            raise

    def ensure_collection(self, vector_size: int) -> None:
        if not self.enabled:
            return
        if self._vector_size is not None:
            if self._vector_size != vector_size:
                raise ValueError("Qdrant projection vector dimension changed")
            return
        try:
            response = self._client.get(f"/collections/{self.collection}")
            if response.status_code == 404:
                self._request(
                    "PUT",
                    f"/collections/{self.collection}",
                    json={"vectors": {"size": vector_size, "distance": "Cosine"}},
                )
            else:
                response.raise_for_status()
                result = response.json().get("result") or {}
                vectors = ((result.get("config") or {}).get("params") or {}).get("vectors") or {}
                configured_size = vectors.get("size") if isinstance(vectors, dict) else None
                if configured_size is not None and int(configured_size) != vector_size:
                    raise ValueError("Qdrant projection collection dimension mismatch")
            self.healthy = True
            self.last_error = ""
        except Exception as exc:
            self.healthy = False
            self.last_error = str(exc)
            raise
        self._vector_size = vector_size

    @staticmethod
    def _payload(memory: Dict) -> Dict:
        payload = dict(memory.get("payload") or {})
        payload.update({
            "text": memory.get("text", ""),
            "owner": memory.get("owner") or "__ownerless__",
            "status": memory.get("status", "approved"),
            "source_ref": memory.get("source_ref", "unknown"),
            "projection_schema": "jos-p3.1",
        })
        return payload

    @staticmethod
    def _point_id(canonical_id: str) -> str:
        try:
            return str(uuid.UUID(canonical_id))
        except (ValueError, TypeError, AttributeError):
            # Keep the original namespace so a rebuilt generic projection
            # addresses the same canonical points as existing installations.
            return str(uuid.uuid5(uuid.NAMESPACE_URL, f"jarvis:{canonical_id}"))

    def upsert(self, memory: Dict, vector: List[float]) -> None:
        self.upsert_many([memory], [vector])

    def upsert_many(self, memories: Iterable[Dict], vectors: Iterable[List[float]]) -> None:
        if not self.enabled:
            return
        points = [
            {
                "id": self._point_id(memory["id"]),
                "vector": vector,
                "payload": {**self._payload(memory), "canonical_id": memory["id"]},
            }
            for memory, vector in zip(memories, vectors)
            if memory.get("id") and memory.get("status", "approved") == "approved"
        ]
        if not points:
            return
        self.ensure_collection(len(points[0]["vector"]))
        self._request(
            "PUT",
            f"/collections/{self.collection}/points",
            params={"wait": "true"},
            json={"points": points},
        )

    def remove(self, memory_id: str) -> None:
        if not self.enabled:
            return
        self._request(
            "POST",
            f"/collections/{self.collection}/points/delete",
            params={"wait": "true"},
            json={"points": [self._point_id(memory_id)]},
        )

    def _search(self, vector: List[float], *, owner: Optional[str], limit: int) -> List[Dict]:
        must = [{"key": "status", "match": {"value": "approved"}}]
        if owner is not None:
            must.insert(0, {"key": "owner", "match": {"value": owner}})
        response = self._request(
            "POST",
            f"/collections/{self.collection}/points/query",
            json={
                "query": vector,
                "filter": {"must": must},
                "limit": limit,
                "with_payload": True,
            },
        )
        result = response.json().get("result") or {}
        points = result.get("points", []) if isinstance(result, dict) else result
        return [
            {
                "memory_id": str((point.get("payload") or {}).get("canonical_id") or point.get("id", "")),
                "score": float(point.get("score", 0.0)),
                "embedding_lane": "qdrant",
            }
            for point in points or []
            if isinstance(point, dict) and point.get("id")
        ]

    def search(self, vector: List[float], *, owner: Optional[str], limit: int) -> List[Dict]:
        if not self.read_enabled:
            return []
        return self._search(vector, owner=owner, limit=limit)

    def search_for_parity(
        self, vector: List[float], *, owner: Optional[str], limit: int
    ) -> List[Dict]:
        """Read the shadow projection without promoting production retrieval."""
        if not self.enabled:
            return []
        return self._search(vector, owner=owner, limit=limit)

    def count_for_parity(self, *, owner: Optional[str]) -> int:
        """Count approved shadow points for one owner without changing read state."""
        if not self.enabled:
            return 0
        must = [{"key": "status", "match": {"value": "approved"}}]
        if owner is not None:
            must.insert(0, {"key": "owner", "match": {"value": owner}})
        response = self._request(
            "POST",
            f"/collections/{self.collection}/points/count",
            json={"filter": {"must": must}, "exact": True},
        )
        result = response.json().get("result") or {}
        return max(0, int(result.get("count", 0)))

    def reset(self, vector_size: int) -> None:
        if not self.enabled:
            return
        response = self._client.delete(f"/collections/{self.collection}")
        if response.status_code not in {200, 404}:
            response.raise_for_status()
        self._vector_size = None
        self.ensure_collection(vector_size)

    def stats(self) -> Dict:
        return {
            "enabled": self.enabled,
            "read_enabled": self.read_enabled,
            "healthy": self.healthy,
            "collection": self.collection,
            "last_error": self.last_error or None,
        }
