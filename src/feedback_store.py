"""Durable store for in-app bug report submissions and screenshot attachments (MAD-856).

The store exists so that two guarantees hold without a database migration:

* **exact-once submission** — a client-generated idempotency key is claimed
  under a lock before GitHub is called; a retry with the same key returns the
  already-created issue instead of posting a duplicate;
* **retryable drafts** — validated screenshot bytes live under
  ``data/feedback/`` keyed by the client draft id, so a failed submission can
  be retried after navigation without re-uploading (or losing) evidence.

The index is a single JSON document written atomically. Attachment bytes are
written owner-only. Nothing in this module performs network I/O.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from core.atomic_io import atomic_write_json
from core.platform_compat import safe_chmod

# Imported as module attributes so tests can point the store at a temp dir.
import src.constants as constants

_STATE_LOCK = threading.RLock()
_PENDING_TTL_SECONDS = 10 * 60

_ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def _feedback_dir() -> Path:
    return Path(constants.FEEDBACK_DIR)


def _index_path() -> Path:
    return _feedback_dir() / "index.json"


def _empty_state() -> dict[str, Any]:
    return {"attachments": {}, "submissions": {}}


def _read_state() -> dict[str, Any]:
    try:
        import json

        value = json.loads(_index_path().read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            return _empty_state()
        value.setdefault("attachments", {})
        value.setdefault("submissions", {})
        return value
    except (OSError, ValueError, TypeError):
        return _empty_state()


def _write_state(state: dict[str, Any]) -> None:
    directory = _feedback_dir()
    directory.mkdir(parents=True, exist_ok=True)
    atomic_write_json(str(_index_path()), state, indent=2)


def reset_store_for_tests() -> None:
    """Drop the on-disk index and draft files (tests only)."""
    import shutil

    with _STATE_LOCK:
        shutil.rmtree(_feedback_dir(), ignore_errors=True)


# ── attachments ──────────────────────────────────────────────────────────


def _draft_dir(draft_id: str) -> Path:
    if not draft_id or "/" in draft_id or "\\" in draft_id or ".." in draft_id:
        raise ValueError("invalid draft id")
    return _feedback_dir() / "drafts" / draft_id


def save_attachment(
    *,
    draft_id: str,
    owner: str,
    data: bytes,
    content_type: str,
    suffix: str,
    name: str,
    label: str = "",
    route: str = "",
) -> dict[str, Any]:
    """Persist one validated screenshot and return its manifest entry."""
    if suffix not in _ALLOWED_SUFFIXES:
        raise ValueError("invalid attachment suffix")
    attachment_id = f"att-{uuid.uuid4().hex[:16]}"
    directory = _draft_dir(draft_id)
    directory.mkdir(parents=True, exist_ok=True)
    safe_chmod(directory, 0o700)
    path = directory / f"{attachment_id}{suffix}"
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
    except Exception:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    record = {
        "id": attachment_id,
        "draft_id": draft_id,
        "owner": str(owner or ""),
        "name": str(name or path.name)[:160],
        "content_type": str(content_type)[:64],
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "label": str(label or "")[:80],
        "route": str(route or "")[:160],
        "created_at": time.time(),
        "file": path.name,
    }
    with _STATE_LOCK:
        state = _read_state()
        state["attachments"][attachment_id] = record
        _write_state(state)
    return record


def list_attachments(draft_id: str, *, owner: str = "") -> list[dict[str, Any]]:
    owner_value = str(owner or "")
    with _STATE_LOCK:
        state = _read_state()
    rows = [
        dict(row)
        for row in state["attachments"].values()
        if row.get("draft_id") == draft_id and str(row.get("owner") or "") == owner_value
    ]
    rows.sort(key=lambda row: float(row.get("created_at") or 0))
    return rows


def get_attachment(draft_id: str, attachment_id: str, *, owner: str = "") -> Optional[dict[str, Any]]:
    with _STATE_LOCK:
        state = _read_state()
    row = state["attachments"].get(attachment_id)
    if not row or row.get("draft_id") != draft_id:
        return None
    if str(row.get("owner") or "") != str(owner or ""):
        return None
    return dict(row)


def attachment_path(record: dict[str, Any]) -> Optional[Path]:
    try:
        path = _draft_dir(str(record.get("draft_id") or "")) / str(record.get("file") or "")
    except ValueError:
        return None
    return path


# ── submissions (exact-once) ─────────────────────────────────────────────


def claim_submission(idempotency_key: str, *, draft_id: str, fingerprint: str) -> dict[str, Any]:
    """Claim a submission key before calling GitHub.

    Returns ``{"state": "claimed"|"submitted"|"in_progress"|"failed", ...}``.
    A ``submitted`` claim is the idempotent replay: the caller must return the
    recorded issue instead of creating another one.
    """
    with _STATE_LOCK:
        state = _read_state()
        record = state["submissions"].get(idempotency_key)
        now = time.time()
        if record:
            status = str(record.get("status") or "")
            if status == "submitted":
                return {"state": "submitted", **record}
            if status == "pending" and (now - float(record.get("claimed_at") or 0)) < _PENDING_TTL_SECONDS:
                return {"state": "in_progress", **record}
            # Failed or stale pending: reuse the key for a retry.
            record.update({"status": "pending", "claimed_at": now, "draft_id": draft_id, "fingerprint": fingerprint})
            state["submissions"][idempotency_key] = record
            _write_state(state)
            return {"state": "claimed", **record}
        record = {
            "status": "pending",
            "draft_id": draft_id,
            "fingerprint": fingerprint,
            "claimed_at": now,
            "created_at": now,
        }
        state["submissions"][idempotency_key] = record
        _write_state(state)
        return {"state": "claimed", **record}


def mark_submitted(idempotency_key: str, *, issue_url: str, issue_number: int) -> dict[str, Any]:
    with _STATE_LOCK:
        state = _read_state()
        record = state["submissions"].get(idempotency_key) or {}
        record.update(
            {
                "status": "submitted",
                "issue_url": str(issue_url),
                "issue_number": int(issue_number),
                "submitted_at": time.time(),
            }
        )
        state["submissions"][idempotency_key] = record
        _write_state(state)
        return dict(record)


def mark_failed(idempotency_key: str, *, error: str) -> dict[str, Any]:
    with _STATE_LOCK:
        state = _read_state()
        record = state["submissions"].get(idempotency_key) or {}
        record.update({"status": "failed", "error": str(error)[:200], "failed_at": time.time()})
        state["submissions"][idempotency_key] = record
        _write_state(state)
        return dict(record)


def get_submission(idempotency_key: str) -> Optional[dict[str, Any]]:
    with _STATE_LOCK:
        state = _read_state()
    record = state["submissions"].get(idempotency_key)
    return dict(record) if record else None