"""JOS-P7 correlated traces, component versions, and rollback records."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from core.atomic_io import atomic_write_json
from core.constants import DATA_DIR
from src.authority_protocol import redact_secrets


EVENTS_FILE = Path(DATA_DIR) / "protocol_events.jsonl"
ROLLBACK_FILE = Path(DATA_DIR) / "component_rollbacks.json"
OUTCOME_STATES = frozenset(
    {"succeeded", "failed", "denied", "cancelled", "timed_out", "unknown", "degraded", "unavailable"}
)
PROTOCOL_VERSIONS = {
    "JOS-P0": "0.1",
    "JOS-P1": "0.1",
    "JOS-P2": "0.1",
    "JOS-P3": "0.1",
    "JOS-P4": "0.1",
    "JOS-P5": "0.1",
    "JOS-P6": "0.1",
    "JOS-P7": "0.1",
    "JOS-EXT-1": "0.1",
}
_EVENT_TYPES = frozenset(
    {"started", "progress", "result", "approval", "health", "recovery", "promotion", "rollback", "response"}
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ephemeral_default(path: Path) -> bool:
    return path in {EVENTS_FILE, ROLLBACK_FILE} and os.getenv("DATABASE_URL", "").lower() == "sqlite:///:memory:"


def _controlled_error(error: Any) -> dict[str, str] | None:
    if not error:
        return None
    if isinstance(error, Mapping):
        category = str(error.get("category") or "error")[:80]
        detail = str(redact_secrets(error.get("detail") or category))[:1_000]
        return {"category": category, "detail": detail}
    if isinstance(error, BaseException):
        return {"category": type(error).__name__[:80], "detail": "operation failed"}
    return {"category": "error", "detail": str(redact_secrets(error))[:1_000]}


class ProtocolEventStore:
    def __init__(self, path: Path | str = EVENTS_FILE, *, max_events: int = 10_000):
        self.path = Path(path)
        self.max_events = max(int(max_events), 100)
        self._ephemeral = _ephemeral_default(self.path)
        self._events: deque[dict[str, Any]] = deque(maxlen=self.max_events)
        self._lock = threading.RLock()

    def record(
        self,
        *,
        request_id: str | None = None,
        session_id: str | None = None,
        task_id: str | None = None,
        call_id: str | None = None,
        operator_id: str | None = None,
        agent_id: str | None = "jarvis",
        actor: str,
        component: str,
        event_type: str,
        status: str,
        duration: float | None = None,
        usage: Mapping[str, Any] | None = None,
        evidence_refs: list[Any] | None = None,
        error: Any = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if event_type not in _EVENT_TYPES:
            raise ValueError("invalid_operational_event_type")
        if status not in OUTCOME_STATES and status not in {"running", "approval_required", "healthy"}:
            raise ValueError("invalid_operational_status")
        event = {
            "event_id": str(uuid.uuid4()),
            "timestamp": _now(),
            "request_id": request_id,
            "session_id": session_id,
            "task_id": task_id,
            "call_id": call_id,
            "operator_id": operator_id,
            "agent_id": agent_id,
            "actor": str(actor)[:200],
            "component": str(component)[:100],
            "event_type": event_type,
            "status": status,
            "duration": round(max(float(duration), 0.0), 6) if duration is not None else None,
            "usage": redact_secrets(dict(usage or {})),
            "evidence_refs": redact_secrets(list(evidence_refs or [])[:50]),
            "error": _controlled_error(error),
        }
        if metadata:
            event["metadata"] = redact_secrets(dict(metadata))
        encoded = json.dumps(event, separators=(",", ":"), ensure_ascii=False, default=str)
        with self._lock:
            if self._ephemeral:
                self._events.append(event)
            else:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(encoded + "\n")
        return event

    def query(
        self,
        *,
        operator_id: str | None = None,
        request_id: str | None = None,
        session_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        limit = min(max(int(limit), 1), 1_000)
        with self._lock:
            if self._ephemeral:
                rows = list(self._events)
            else:
                try:
                    with self.path.open("r", encoding="utf-8") as handle:
                        rows = [json.loads(line) for line in deque(handle, maxlen=self.max_events) if line.strip()]
                except (OSError, ValueError, TypeError):
                    rows = []
        filtered = [
            row for row in rows
            if (operator_id is None or row.get("operator_id") == operator_id)
            and (request_id is None or row.get("request_id") == request_id)
            and (session_id is None or row.get("session_id") == session_id)
        ]
        return filtered[-limit:]

    def unresolved_unknowns(self, *, operator_id: str | None = None) -> list[dict[str, Any]]:
        rows = self.query(operator_id=operator_id, limit=1_000)
        resolved = {
            row.get("call_id") for row in rows
            if row.get("event_type") == "recovery"
            and row.get("status") in OUTCOME_STATES - {"unknown"}
        }
        return [
            row for row in rows
            if row.get("event_type") == "result"
            and row.get("status") == "unknown"
            and row.get("call_id") not in resolved
        ]

    def reconcile_unknown(
        self,
        *,
        call_id: str,
        status: str,
        actor: str,
        evidence_refs: list[Any],
        request_id: str | None = None,
        session_id: str | None = None,
        operator_id: str | None = None,
    ) -> dict[str, Any]:
        if status not in OUTCOME_STATES - {"unknown"}:
            raise ValueError("reconciliation_requires_terminal_outcome")
        return self.record(
            request_id=request_id,
            session_id=session_id,
            call_id=call_id,
            operator_id=operator_id,
            actor=actor,
            component="control_plane",
            event_type="recovery",
            status=status,
            evidence_refs=evidence_refs,
        )


class RollbackRegistry:
    """Version records for the smallest mutable component rollback unit."""

    def __init__(self, path: Path | str = ROLLBACK_FILE):
        self.path = Path(path)
        self._ephemeral = _ephemeral_default(self.path)
        self._state: dict[str, Any] = {"components": {}}
        self._lock = threading.RLock()

    def _read(self) -> dict[str, Any]:
        if self._ephemeral:
            return json.loads(json.dumps(self._state))
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {"components": {}}
        except (OSError, ValueError, TypeError):
            return {"components": {}}

    def _write(self, state: dict[str, Any]) -> None:
        if self._ephemeral:
            self._state = json.loads(json.dumps(state))
        else:
            atomic_write_json(str(self.path), state, indent=2)

    def promote(
        self,
        component: str,
        version: str,
        *,
        configuration: Mapping[str, Any] | None = None,
        compatibility: str = "passed",
        rollback_ref: str | None = None,
    ) -> dict[str, Any]:
        raw_config = dict(configuration or {})
        safe_config = redact_secrets(raw_config)
        fingerprint = hashlib.sha256(
            json.dumps(raw_config, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()
        with self._lock:
            state = self._read()
            slot = state.setdefault("components", {}).setdefault(component, {"active": None, "history": []})
            record = {
                "record_id": str(uuid.uuid4()),
                "component": component,
                "version": str(version),
                "configuration": safe_config,
                "configuration_fingerprint": fingerprint,
                "compatibility": compatibility,
                "rollback_ref": rollback_ref,
                "previous_record_id": (slot.get("active") or {}).get("record_id"),
                "promoted_at": _now(),
                "status": "active",
            }
            if slot.get("active"):
                previous = dict(slot["active"])
                previous["status"] = "superseded"
                slot["history"].append(previous)
            slot["active"] = record
            self._write(state)
            return dict(record)

    def rollback(self, component: str, target_record_id: str) -> dict[str, Any]:
        with self._lock:
            state = self._read()
            slot = state.get("components", {}).get(component)
            if not slot:
                raise KeyError("rollback_component_not_found")
            candidates = [slot.get("active"), *(slot.get("history") or [])]
            target = next((row for row in candidates if row and row.get("record_id") == target_record_id), None)
            if not target:
                raise KeyError("rollback_record_not_found")
            current = dict(slot["active"])
            current["status"] = "rolled_back"
            slot["history"].append(current)
            restored = dict(target)
            restored.update({
                "record_id": str(uuid.uuid4()),
                "previous_record_id": current["record_id"],
                "promoted_at": _now(),
                "status": "active",
                "rollback_of": current["record_id"],
                "restored_from": target_record_id,
            })
            slot["active"] = restored
            self._write(state)
            return dict(restored)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._read()


def backup_status(repo_root: Path | None = None) -> dict[str, Any]:
    root = repo_root or Path(__file__).resolve().parents[1]
    backup_dir = root / "backups"
    archives = sorted(backup_dir.glob("*.tar.gz"), key=lambda path: path.stat().st_mtime, reverse=True) if backup_dir.exists() else []
    if not archives:
        return {"status": "unavailable", "latest": None, "verified": False, "rollback_available": False}
    latest = archives[0]
    sidecar = Path(str(latest) + ".verified.json")
    verified = False
    verified_at = None
    if sidecar.exists():
        try:
            proof = json.loads(sidecar.read_text(encoding="utf-8"))
            verified = bool(proof.get("ok") and proof.get("sha256"))
            verified_at = proof.get("verified_at")
        except (OSError, ValueError, TypeError):
            pass
    return {
        "status": "succeeded" if verified else "degraded",
        "latest": latest.name,
        "created_at": datetime.fromtimestamp(latest.stat().st_mtime, timezone.utc).isoformat(),
        "verified": verified,
        "verified_at": verified_at,
        "rollback_available": verified,
    }


def protocol_status(rollback_registry: RollbackRegistry | None = None) -> dict[str, Any]:
    registry = rollback_registry or rollbacks
    try:
        from src.learning_protocol import learning_candidates
        learning_state = learning_candidates.snapshot()
        learning = {
            "candidates": len(learning_state.get("candidates") or {}),
            "active_promotions": sum(
                1 for row in (learning_state.get("promotions") or []) if row.get("status") == "active"
            ),
            "monitored_candidates": len(learning_state.get("monitoring") or {}),
        }
    except Exception:
        learning = {"status": "unavailable"}
    return {
        "protocol_versions": dict(PROTOCOL_VERSIONS),
        "outcome_taxonomy": sorted(OUTCOME_STATES),
        "rollback_units": registry.snapshot().get("components", {}),
        "backup": backup_status(),
        "unresolved_unknown_actions": len(events.unresolved_unknowns()),
        "learning": learning,
        "canonical_recovery": {
            "sessions": True,
            "worker_tasks": True,
            "memory_projection_rebuildable": True,
            "engine_native_cache_required": False,
        },
    }


events = ProtocolEventStore()
rollbacks = RollbackRegistry()


def record_operational_event(**values: Any) -> dict[str, Any] | None:
    """Observability is fail-soft and can never authorize or block execution."""
    try:
        return events.record(**values)
    except Exception:
        return None
