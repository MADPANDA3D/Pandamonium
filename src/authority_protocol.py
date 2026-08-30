"""JOS-P5 authority decisions and exact, revocable approval receipts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from src.agent_identity import configured_agent_id

from core.atomic_io import atomic_write_json
from core.constants import DATA_DIR


AUTHORITY_FILE = Path(DATA_DIR) / "authority_receipts.json"
APPROVAL_SCOPES = frozenset({"once", "session", "time_bounded", "persistent"})
_SECRET_KEY = re.compile(r"token|secret|password|credential|private.?key|authorization|api.?key", re.I)
_SECRET_TEXT_PATTERNS = (
    re.compile(r"(?i)\b(Bearer\s+)[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{12,}|xox[baprs]-[A-Za-z0-9-]{12,})\b"),
    re.compile(r"(?i)\b((?:api[_-]?key|token|secret|password|credential)\s*[:=]\s*['\"]?)[^\s'\",;}]{4,}"),
)

_READ_ONLY_NAMES = frozenset(
    {
        "get_workspace", "read_file", "grep", "glob", "ls", "web_search", "web_fetch",
        "get_runtime_status", "read_agent_task", "search_jarvis_knowledge", "read_calendar",
        "list_sessions", "search_chats", "list_email_accounts", "list_emails", "read_email",
        "list_models", "list_cached_models", "list_downloads", "list_serve_presets",
        "list_served_models", "list_cookbook_servers", "search_hf_models", "vault_search",
        "vault_get", "resolve_contact",
    }
)
_LOCAL_WRITES = frozenset(
    {
        "create_document", "edit_document", "update_document", "suggest_document",
        "write_file", "edit_file", "manage_notes", "manage_tasks", "manage_memory",
        "manage_skills", "manage_research", "manage_contact", "manage_session",
        "create_session", "send_to_session", "generate_image", "edit_image", "ui_control",
        "update_plan", "ask_user", "trigger_research", "start_agent_task", "manage_bg_jobs",
    }
)
_EXTERNAL = frozenset(
    {"send_email", "reply_to_email", "bulk_email", "manage_calendar", "api_call", "app_api"}
)
_DESTRUCTIVE = frozenset(
    {"delete_email", "archive_email", "mark_email_read", "cancel_download", "stop_served_model"}
)
_ADMINISTRATIVE = frozenset(
    {
        "bash", "python", "download_model", "serve_model", "serve_preset", "adopt_served_model",
        "manage_settings", "manage_endpoints", "manage_mcp", "manage_tokens", "manage_webhooks",
        "vault_unlock",
    }
)
_READ_ACTIONS = frozenset({"list", "get", "read", "view", "search", "find", "status", "health"})
_PUBLIC_READS = frozenset({"web_search", "web_fetch", "get_runtime_status"})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def operator_identity(owner: str | None) -> str | None:
    identity = str(owner or "").strip()
    if identity:
        return identity
    if os.getenv("AUTH_ENABLED", "true").lower() == "false":
        return "local-operator"
    return None


def argument_fingerprint(call: Mapping[str, Any]) -> str:
    material = {
        "name": call.get("name"),
        "target": call.get("target"),
        "arguments": call.get("arguments"),
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def redact_secret_text(value: str) -> str:
    redacted = value
    redacted = _SECRET_TEXT_PATTERNS[0].sub(r"\1[redacted]", redacted)
    redacted = _SECRET_TEXT_PATTERNS[1].sub("[redacted]", redacted)
    redacted = _SECRET_TEXT_PATTERNS[2].sub(r"\1[redacted]", redacted)
    return redacted


def redact_secrets(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: "[redacted]" if _SECRET_KEY.search(str(key)) else redact_secrets(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, tuple):
        return [redact_secrets(item) for item in value]
    if isinstance(value, str):
        return redact_secret_text(value)
    return value


def safe_preview(value: Any, *, depth: int = 0) -> Any:
    """Bound and redact action arguments before an approval is displayed."""
    if depth > 5:
        return "[truncated]"
    if isinstance(value, Mapping):
        return {
            str(key)[:100]: "[redacted]" if _SECRET_KEY.search(str(key)) else safe_preview(item, depth=depth + 1)
            for key, item in list(value.items())[:50]
        }
    if isinstance(value, list):
        return [safe_preview(item, depth=depth + 1) for item in value[:50]]
    if isinstance(value, str):
        redacted = redact_secret_text(value)
        return redacted[:1_000] + ("..." if len(redacted) > 1_000 else "")
    return value


def audit_safe_action_call(call: Mapping[str, Any]) -> dict[str, Any]:
    safe = dict(call)
    safe["arguments"] = redact_secrets(call.get("arguments") or {})
    return safe


def permission_mode_for(call: Mapping[str, Any]) -> str:
    name = str(call.get("name") or "")
    arguments = call.get("arguments") if isinstance(call.get("arguments"), Mapping) else {}
    action = str(arguments.get("action") or "").lower()
    if name in _READ_ONLY_NAMES or action in _READ_ACTIONS:
        return "read_only"
    if name in _LOCAL_WRITES or str(call.get("target") or "") == "extension:oracle":
        return "bounded_write"
    if name in _EXTERNAL:
        return "external_side_effect"
    if name in _DESTRUCTIVE or action in {"delete", "remove", "revoke", "restore", "overwrite", "bulk_delete"}:
        return "destructive"
    if name in _ADMINISTRATIVE:
        return "controlled_administrative"
    return "unclassified"


class AuthorityStore:
    """Atomic JSON authority state; approvals are never accepted from a model call."""

    def __init__(self, path: Path | str = AUTHORITY_FILE):
        self.path = Path(path)
        self._ephemeral = (
            self.path == AUTHORITY_FILE
            and os.getenv("DATABASE_URL", "").lower() == "sqlite:///:memory:"
        )
        self._memory_state: dict[str, Any] = {"decisions": {}, "receipts": {}}
        self._lock = threading.RLock()

    def _read(self) -> dict[str, Any]:
        if self._ephemeral:
            return json.loads(json.dumps(self._memory_state))
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                value.setdefault("decisions", {})
                value.setdefault("receipts", {})
                return value
        except (OSError, ValueError, TypeError):
            pass
        return {"decisions": {}, "receipts": {}}

    def _write(self, state: Mapping[str, Any]) -> None:
        if self._ephemeral:
            self._memory_state = json.loads(json.dumps(dict(state)))
            return
        atomic_write_json(str(self.path), dict(state), indent=2)

    def _matching_receipt(
        self,
        state: dict[str, Any],
        *,
        operator_id: str,
        agent_id: str,
        session_id: str,
        request_id: str,
        fingerprint: str,
        now: datetime,
    ) -> dict[str, Any] | None:
        matches = []
        dirty = False
        for receipt in state["receipts"].values():
            if receipt.get("status") != "active":
                continue
            expires = _parse_time(receipt.get("expires_at"))
            if expires and expires <= now:
                receipt["status"] = "expired"
                dirty = True
                continue
            if receipt.get("operator_id") != operator_id or receipt.get("agent_id") != agent_id:
                continue
            if receipt.get("argument_fingerprint") != fingerprint:
                continue
            if receipt.get("decision") == "deny" and receipt.get("request_id") != request_id:
                continue
            if receipt.get("scope") == "session" and receipt.get("session_id") != session_id:
                continue
            matches.append(receipt)
        if dirty:
            self._write(state)
        return sorted(matches, key=lambda row: str(row.get("issued_at") or ""), reverse=True)[0] if matches else None

    def decide(
        self,
        call: Mapping[str, Any],
        *,
        operator_id: str | None,
        session_id: str | None,
        disabled_reason: str | None = None,
        native_approval_gate: bool = False,
    ) -> dict[str, Any]:
        now = _now()
        operator = str(operator_id or "").strip()
        agent_id = str(call.get("agent_id") or configured_agent_id())
        session = str(session_id or "")
        mode = permission_mode_for(call)
        fingerprint = argument_fingerprint(call)
        decision_id = str(uuid.uuid4())
        decision = "allow"
        basis = "owner_scope"
        receipt_id = None

        if not operator and mode == "read_only" and str(call.get("name") or "") in _PUBLIC_READS:
            decision, basis = "allow", "public_non_owner_read"
        elif not operator:
            decision, basis = "deny", "authenticated_operator_required"
        elif disabled_reason:
            decision, basis = "deny", disabled_reason
        elif mode == "unclassified":
            decision, basis = "deny", "unclassified_capability"
        elif mode == "read_only":
            decision, basis = "allow", "owner_scoped_read"
        elif str(call.get("target") or "") == "extension:oracle":
            decision, basis = "allow", "engaged_oracle_scope"
        elif native_approval_gate:
            decision, basis = "allow", "native_staged_approval"
        elif mode in {"bounded_write", "controlled_administrative"}:
            decision, basis = "allow", "existing_scoped_policy"
        else:
            with self._lock:
                state = self._read()
                receipt = self._matching_receipt(
                    state,
                    operator_id=operator,
                    agent_id=agent_id,
                    session_id=session,
                    request_id=str(call.get("request_id") or ""),
                    fingerprint=fingerprint,
                    now=now,
                )
                if receipt:
                    receipt_id = receipt["receipt_id"]
                    if receipt.get("decision") == "deny":
                        decision, basis = "deny", "denial_receipt"
                    else:
                        decision, basis = "allow", f"approval_receipt:{receipt.get('scope')}"
                        if receipt.get("scope") == "once":
                            receipt["status"] = "consumed"
                            receipt["consumed_at"] = _iso(now)
                            self._write(state)
                else:
                    decision, basis = "approval_required", "exact_operator_approval"

        record = {
            "decision_id": decision_id,
            "operator_id": operator or None,
            "agent_id": agent_id,
            "session_id": session or None,
            "request_id": call.get("request_id"),
            "call_id": call.get("call_id"),
            "capability": {"name": call.get("name"), "target": call.get("target")},
            "argument_fingerprint": fingerprint,
            "permission_mode": mode,
            "decision": decision,
            "policy_basis": basis,
            "receipt_id": receipt_id,
            "preview": safe_preview(call.get("arguments") or {}),
            "created_at": _iso(now),
            "expires_at": _iso(now + timedelta(minutes=10)),
        }
        with self._lock:
            state = self._read()
            state["decisions"][decision_id] = record
            self._write(state)
        return record

    def resolve(
        self,
        decision_id: str,
        *,
        operator_id: str,
        choice: str,
        scope: str = "once",
        ttl_seconds: int = 900,
    ) -> dict[str, Any]:
        with self._lock:
            state = self._read()
            decision = state["decisions"].get(decision_id)
            if not decision or decision.get("operator_id") != operator_id:
                raise KeyError("authority_decision_not_found")
            if decision.get("decision") != "approval_required" or decision.get("status") in {"resolved", "expired"}:
                raise ValueError("authority_decision_not_pending")
            expires = _parse_time(decision.get("expires_at"))
            now = _now()
            if not expires or expires <= now:
                decision["status"] = "expired"
                self._write(state)
                raise ValueError("authority_decision_expired")
            if choice not in {"approve", "deny"}:
                raise ValueError("invalid_authority_choice")
            if scope not in APPROVAL_SCOPES:
                raise ValueError("invalid_authority_scope")
            receipt_id = str(uuid.uuid4())
            receipt_expires = None
            if scope == "time_bounded":
                ttl = min(max(int(ttl_seconds), 1), 86_400)
                receipt_expires = _iso(now + timedelta(seconds=ttl))
            receipt = {
                "receipt_id": receipt_id,
                "decision_id": decision_id,
                "operator_id": operator_id,
                "agent_id": decision["agent_id"],
                "session_id": decision.get("session_id"),
                "request_id": decision.get("request_id"),
                "call_id": decision.get("call_id"),
                "capability": decision.get("capability"),
                "argument_fingerprint": decision["argument_fingerprint"],
                "decision": "allow" if choice == "approve" else "deny",
                "scope": scope,
                "status": "active",
                "issued_at": _iso(now),
                "expires_at": receipt_expires,
            }
            decision["status"] = "resolved"
            decision["resolved_at"] = _iso(now)
            decision["receipt_id"] = receipt_id
            state["receipts"][receipt_id] = receipt
            self._write(state)
            return receipt

    def revoke(self, receipt_id: str, *, operator_id: str) -> dict[str, Any]:
        with self._lock:
            state = self._read()
            receipt = state["receipts"].get(receipt_id)
            if not receipt or receipt.get("operator_id") != operator_id:
                raise KeyError("authority_receipt_not_found")
            receipt["status"] = "revoked"
            receipt["revoked_at"] = _iso(_now())
            self._write(state)
            return dict(receipt)

    def list_state(self, *, operator_id: str) -> dict[str, list[dict[str, Any]]]:
        with self._lock:
            state = self._read()
        return {
            "decisions": [dict(row) for row in state["decisions"].values() if row.get("operator_id") == operator_id],
            "receipts": [dict(row) for row in state["receipts"].values() if row.get("operator_id") == operator_id],
        }


authority_store = AuthorityStore()
