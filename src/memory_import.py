"""Preview and stage source-neutral agent migration memory candidates."""

from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any, Dict, Optional


SCHEMA_VERSION = "agent-migration.v1"
_SENSITIVE_RE = re.compile(
    r"(?i)(?:api[_ -]?key|access[_ -]?token|password|private[_ -]?key|secret)\s*[:=]"
)


def _source_ref(manifest: Dict[str, Any], item: Dict[str, Any]) -> str:
    source = str((manifest.get("source") or {}).get("name") or item.get("source") or "unknown")
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    locator = next(
        (
            str(metadata[key])
            for key in ("source_thread_id", "source_conversation_id", "source_id")
            if metadata.get(key) is not None and metadata.get(key) != ""
        ),
        str(item.get("id") or "unknown"),
    )
    return f"agent-migration:{source}:{locator}"


def _source_time(item: Dict[str, Any]) -> int:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    value = next(
        (
            metadata[key]
            for key in (
                "source_timestamp",
                "source_created_at",
                "source_updated_at",
                "created_at",
                "timestamp",
            )
            if metadata.get(key) is not None and metadata.get(key) != ""
        ),
        None,
    )
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
        except ValueError:
            pass
    return int(time.time())


def preview_manifest(
    manifest: Dict[str, Any],
    *,
    memory_manager,
    owner: Optional[str],
    memory_vector=None,
) -> Dict[str, Any]:
    """Inventory a migration manifest without writing canonical state."""
    if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported_migration_manifest")
    items = manifest.get("items")
    if not isinstance(items, list):
        raise ValueError("invalid_migration_items")

    existing = memory_manager.load(owner=owner)
    existing_text = {str(entry.get("text", "")).strip().casefold() for entry in existing}
    existing_refs = {entry.get("source_ref") for entry in memory_manager.load_all()}
    candidates = []
    counts = {
        "items": len(items),
        "memory_candidates": 0,
        "archive_documents": 0,
        "conversation_threads": 0,
        "skills": 0,
        "exact_duplicates": 0,
        "semantic_duplicates": 0,
        "privacy_sensitive": 0,
        "already_staged": 0,
    }
    kind_keys = {
        "archive_document": "archive_documents",
        "conversation_thread": "conversation_threads",
        "skill": "skills",
    }
    for item in items:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        if kind != "memory":
            key = kind_keys.get(kind)
            if key:
                counts[key] += 1
            continue
        counts["memory_candidates"] += 1
        text = str(item.get("text") or "").strip()
        source_ref = _source_ref(manifest, item)
        disposition = "candidate"
        duplicate_of = None
        if source_ref in existing_refs:
            disposition = "already_staged"
            counts["already_staged"] += 1
        elif _SENSITIVE_RE.search(text):
            disposition = "privacy_sensitive"
            counts["privacy_sensitive"] += 1
        elif text.casefold() in existing_text:
            disposition = "exact_duplicate"
            counts["exact_duplicates"] += 1
        elif memory_vector and getattr(memory_vector, "healthy", False):
            try:
                try:
                    duplicate_of = memory_vector.find_similar(text, threshold=0.92, owner=owner)
                except TypeError:
                    duplicate_of = memory_vector.find_similar(text, threshold=0.92)
            except Exception:
                duplicate_of = None
            if duplicate_of:
                disposition = "semantic_duplicate"
                counts["semantic_duplicates"] += 1
        candidates.append({
            "item_id": item.get("id"),
            "text": text,
            "category": item.get("category") or "fact",
            "source_ref": source_ref,
            "disposition": disposition,
            "duplicate_of": duplicate_of,
        })

    return {
        "schema_version": SCHEMA_VERSION,
        "source": manifest.get("source") or {},
        "counts": counts,
        "warnings": list(manifest.get("warnings") or []),
        "candidates": candidates,
        "writes": 0,
    }


def stage_manifest(
    manifest: Dict[str, Any],
    *,
    memory_manager,
    owner: Optional[str],
    memory_vector=None,
) -> Dict[str, Any]:
    """Stage safe memory items as candidates; never approve or index them."""
    preview = preview_manifest(
        manifest,
        memory_manager=memory_manager,
        owner=owner,
        memory_vector=memory_vector,
    )
    items_by_id = {
        item.get("id"): item
        for item in manifest.get("items", [])
        if isinstance(item, dict)
    }
    all_entries = memory_manager.load_all()
    staged = []
    for candidate in preview["candidates"]:
        if candidate["disposition"] != "candidate":
            continue
        item = items_by_id.get(candidate["item_id"]) or {}
        entry = memory_manager.add_entry(
            candidate["text"],
            source=str(item.get("source") or "migration"),
            category=candidate["category"],
            owner=owner,
            status="candidate",
            source_ref=candidate["source_ref"],
            source_time=_source_time(item),
            admitted_by="migration.staged",
        )
        entry["migration_item_id"] = candidate["item_id"]
        all_entries.append(entry)
        staged.append(entry)
    if staged:
        memory_manager.save(all_entries)
    return {
        "preview": preview,
        "staged_count": len(staged),
        "candidate_ids": [entry["id"] for entry in staged],
    }


def decide_candidate(
    memory_manager,
    memory_id: str,
    *,
    owner: Optional[str],
    approve: bool,
    decided_by: str = "operator",
) -> Optional[Dict[str, Any]]:
    """Apply an operator decision to one staged candidate."""
    entries = memory_manager.load_all()
    target = next((entry for entry in entries if entry.get("id") == memory_id), None)
    if target is None or target.get("status") != "candidate":
        return None
    if owner is not None and target.get("owner") != owner:
        return None
    target["status"] = "approved" if approve else "rejected"
    target["admitted_at" if approve else "rejected_at"] = int(time.time())
    target["admitted_by" if approve else "rejected_by"] = decided_by
    memory_manager.save(entries)
    return target
