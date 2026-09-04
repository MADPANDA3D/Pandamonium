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
ACTION_EFFECTS = frozenset(
    {
        "read",
        "reversible_write",
        "destructive_or_difficult_to_recover",
        "external_publication_or_communication",
        "purchase",
        "credential_or_auth_change",
        "privilege_expansion",
        "outside_workspace_boundary",
    }
)
SEPARATE_GATE_EFFECTS = frozenset(
    {
        "destructive_or_difficult_to_recover",
        "external_publication_or_communication",
        "purchase",
        "credential_or_auth_change",
        "privilege_expansion",
        "outside_workspace_boundary",
    }
)
_SECRET_KEY = re.compile(r"token|secret|password|credential|private.?key|authorization|api.?key", re.I)
_SECRET_TEXT_PATTERNS = (
    re.compile(r"(?i)\b(Bearer\s+)[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{12,}|xox[baprs]-[A-Za-z0-9-]{12,})\b"),
    re.compile(r"(?i)\b((?:api[_-]?key|token|secret|password|credential)\s*[:=]\s*['\"]?)[^\s'\",;}]{4,}"),
)

_DEFAULT_EFFECT_BY_CAPABILITY = {
    **{
        name: "read"
        for name in {
            "get_workspace", "read_file", "grep", "glob", "ls", "web_search", "web_fetch",
            "get_runtime_status", "read_agent_task", "search_jarvis_knowledge", "read_calendar",
            "list_sessions", "search_chats", "list_email_accounts", "list_emails", "read_email",
            "list_models", "list_cached_models", "list_downloads", "list_serve_presets",
            "list_served_models", "list_cookbook_servers", "search_hf_models", "vault_search",
            "vault_get", "resolve_contact", "manage_books",
        }
    },
    **{
        name: "reversible_write"
        for name in {
            "create_document", "edit_document", "update_document", "suggest_document",
            "write_file", "edit_file", "manage_notes", "manage_tasks", "manage_memory",
            "manage_skills", "manage_research", "manage_contact", "manage_session",
            "create_session", "send_to_session", "generate_image", "edit_image", "ui_control",
            "update_plan", "ask_user", "trigger_research", "start_agent_task", "manage_bg_jobs",
            "bash", "python", "download_model", "serve_model", "serve_preset", "adopt_served_model",
            "manage_settings", "manage_endpoints", "manage_mcp", "manage_webhooks",
        }
    },
    **{
        name: "external_publication_or_communication"
        for name in {"send_email", "reply_to_email", "bulk_email", "manage_calendar", "api_call", "app_api"}
    },
    **{
        name: "destructive_or_difficult_to_recover"
        for name in {"delete_email", "archive_email", "mark_email_read", "cancel_download", "stop_served_model"}
    },
    "manage_tokens": "credential_or_auth_change",
    "vault_unlock": "credential_or_auth_change",
}
_READ_ACTIONS = frozenset({"inventory", "list", "get", "read", "view", "search", "find", "status", "health"})
_PUBLIC_READS = frozenset({"web_search", "web_fetch", "get_runtime_status"})
_LEGACY_EFFECTS = {
    "read_only": "read",
    "bounded_write": "reversible_write",
    "controlled_administrative": "reversible_write",
    "external_side_effect": "external_publication_or_communication",
    "destructive": "destructive_or_difficult_to_recover",
}
_EFFECT_STRICTNESS = {
    "read": 0,
    "reversible_write": 1,
    "destructive_or_difficult_to_recover": 2,
    "external_publication_or_communication": 3,
    "purchase": 4,
    "credential_or_auth_change": 5,
    "privilege_expansion": 6,
    "outside_workspace_boundary": 7,
}
_DESTRUCTIVE_ACTIONS = frozenset(
    {"delete", "remove", "purge", "revoke", "restore", "overwrite", "bulk_delete", "drop", "reset"}
)
_PURCHASE_ACTIONS = frozenset({"buy", "purchase", "subscribe", "checkout", "order", "pay"})
_EXTERNAL_ACTIONS = frozenset({"send", "publish", "post", "submit", "invite", "communicate"})
_CREDENTIAL_ACTIONS = frozenset(
    {"authenticate", "login", "logout", "oauth", "rotate_token", "set_token", "add_credential", "remove_credential", "change_password", "mfa"}
)
_PRIVILEGE_ACTIONS = frozenset(
    {"grant", "elevate", "enable_privileged", "assign_role", "widen_permissions", "change_permissions"}
)
_PATH_ARGUMENT_KEYS = frozenset(
    {"path", "paths", "cwd", "directory", "root", "source_path", "target_path", "destination_path"}
)


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


def _declared_effect(call: Mapping[str, Any]) -> str | None:
    policy = call.get("capability_policy") if isinstance(call.get("capability_policy"), Mapping) else {}
    declared = str(policy.get("action_effect") or "")
    if declared in ACTION_EFFECTS:
        return declared
    return _LEGACY_EFFECTS.get(str(policy.get("permission_mode") or ""))


def _path_values(value: Any, *, key: str = ""):
    if isinstance(value, Mapping):
        for child_key, child in value.items():
            yield from _path_values(child, key=str(child_key).lower())
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _path_values(child, key=key)
    elif key in _PATH_ARGUMENT_KEYS and isinstance(value, str):
        yield value


def _contains_credential_field(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            _SECRET_KEY.search(str(key)) or _contains_credential_field(child)
            for key, child in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_credential_field(child) for child in value)
    return False


def _outside_configured_workspace(call: Mapping[str, Any]) -> bool:
    arguments = call.get("arguments") if isinstance(call.get("arguments"), Mapping) else {}
    policy = call.get("capability_policy") if isinstance(call.get("capability_policy"), Mapping) else {}
    if arguments.get("outside_workspace") is True:
        return True
    configured_scopes = policy.get("configured_scopes")
    requested_scope = str(arguments.get("workspace") or "")
    if requested_scope and isinstance(configured_scopes, (list, tuple, set)):
        if requested_scope not in {str(item) for item in configured_scopes}:
            return True
    workspace = str(policy.get("configured_workspace") or "").strip()
    if not workspace:
        return False
    base = os.path.realpath(os.path.expanduser(workspace))
    for raw_path in _path_values(arguments):
        if not raw_path or "://" in raw_path:
            continue
        expanded = os.path.expanduser(raw_path)
        resolved = os.path.realpath(expanded if os.path.isabs(expanded) else os.path.join(base, expanded))
        try:
            if os.path.commonpath([base, resolved]) != base:
                return True
        except ValueError:
            return True
    return False


def _shell_effect(command: str) -> str:
    value = str(command or "").strip().lower()
    if re.search(
        r"(?:^|[;&|]\s*)(?:rm|shred|mkfs|wipefs|dd)\b"
        r"|git\s+(?:reset\s+--hard|clean\s+-[a-z]*f)"
        r"|(?:os\.)?(?:remove|unlink)\s*\(|shutil\.rmtree\s*\(",
        value,
    ):
        return "destructive_or_difficult_to_recover"
    if re.search(r"\b(?:sudo|su)\b|\bchmod\s+(?:[0-7]*[2367]|[ugo]*\+.*[wx])|\bchown\b", value):
        return "privilege_expansion"
    if re.search(r"\b(?:login|oauth|passwd|chpasswd)\b|\b(?:token|credential|api[_-]?key)\b", value):
        return "credential_or_auth_change"
    if re.search(r"\b(?:curl|wget)\b.*(?:-x\s+(?:post|put|patch|delete)|--data|-d\s)", value):
        return "external_publication_or_communication"
    if re.fullmatch(r"(?:pwd|ls(?:\s+[^;&|]+)?|git\s+(?:status|diff|log)(?:\s+[^;&|]+)?|rg(?:\s+[^;&|]+)?)", value):
        return "read"
    return "reversible_write"


def action_effect_for(call: Mapping[str, Any]) -> str:
    """Classify the concrete action; delegated metadata can only make it stricter."""
    name = str(call.get("name") or "")
    arguments = call.get("arguments") if isinstance(call.get("arguments"), Mapping) else {}
    action = str(arguments.get("action") or "").lower()
    method = str(arguments.get("method") or "").upper()
    target = str(call.get("target") or "")
    selector = " ".join(
        str(arguments.get(key) or "").lower()
        for key in ("key", "setting", "field", "kind", "operation")
    )
    candidates = []
    declared = _declared_effect(call)
    if declared:
        candidates.append(declared)
    if _outside_configured_workspace(call):
        candidates.append("outside_workspace_boundary")
    elif name in {"bash", "python"}:
        candidates.append(_shell_effect(str(arguments.get("command") or arguments.get("code") or "")))
    elif action in _PURCHASE_ACTIONS or re.search(r"(?:buy|purchase|subscribe|checkout|order|payment)", name):
        candidates.append("purchase")
    elif (
        action in _CREDENTIAL_ACTIONS
        or name in {"manage_tokens", "vault_unlock"}
        or (action not in _READ_ACTIONS and _contains_credential_field(arguments))
        or (action not in _READ_ACTIONS and re.search(r"(?:auth|oauth|login|mfa|password|credential|token|api[_-]?key)", selector))
    ):
        candidates.append("credential_or_auth_change")
    elif (
        action in _PRIVILEGE_ACTIONS
        or (name == "manage_mcp" and action in {"add", "connect", "enable", "install"})
        or (
            action not in _READ_ACTIONS
            and re.search(r"(?:privilege|permission|role|sudo|root|shell|bash)", selector)
        )
    ):
        candidates.append("privilege_expansion")
    elif action in _DESTRUCTIVE_ACTIONS:
        candidates.append("destructive_or_difficult_to_recover")
    elif name == "api_call":
        candidates.append("read" if method == "GET" else "external_publication_or_communication")
    elif action in _EXTERNAL_ACTIONS:
        candidates.append("external_publication_or_communication")
    elif action in _READ_ACTIONS:
        candidates.append("read")
    elif name in _DEFAULT_EFFECT_BY_CAPABILITY:
        candidates.append(_DEFAULT_EFFECT_BY_CAPABILITY[name])
    elif not target.startswith("extension:"):
        return "unclassified"
    return max(candidates, key=lambda effect: _EFFECT_STRICTNESS[effect]) if candidates else "unclassified"


def permission_mode_for(call: Mapping[str, Any]) -> str:
    """Legacy compatibility projection; authorization uses ``action_effect_for``."""
    effect = action_effect_for(call)
    return {
        "read": "read_only",
        "reversible_write": "bounded_write",
        "destructive_or_difficult_to_recover": "destructive",
        "external_publication_or_communication": "external_side_effect",
    }.get(effect, effect if effect == "unclassified" else "separate_gate")


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
        configured_workspace: str | None = None,
    ) -> dict[str, Any]:
        now = _now()
        operator = str(operator_id or "").strip()
        agent_id = str(call.get("agent_id") or configured_agent_id())
        session = str(session_id or "")
        classified_call = dict(call)
        if configured_workspace:
            policy = dict(call.get("capability_policy") or {})
            policy["configured_workspace"] = configured_workspace
            classified_call["capability_policy"] = policy
        effect = action_effect_for(classified_call)
        mode = permission_mode_for(call)
        fingerprint = argument_fingerprint(call)
        decision_id = str(uuid.uuid4())
        decision = "allow"
        basis = "owner_scope"
        receipt_id = None

        if not operator and effect == "read" and str(call.get("name") or "") in _PUBLIC_READS:
            decision, basis = "allow", "public_non_owner_read"
        elif not operator:
            decision, basis = "deny", "authenticated_operator_required"
        elif disabled_reason:
            decision, basis = "deny", disabled_reason
        elif effect == "unclassified":
            decision, basis = "deny", "unclassified_capability"
        elif effect == "read":
            decision, basis = "allow", "owner_scoped_read"
        elif native_approval_gate:
            decision, basis = "allow", "native_staged_approval"
        elif effect == "reversible_write":
            decision, basis = "allow", "authenticated_explicit_request"
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
            "workspace": (
                str((call.get("arguments") or {}).get("workspace") or "").strip()
                if isinstance(call.get("arguments"), Mapping)
                else ""
            ) or (Path(configured_workspace).name if configured_workspace else None),
            "argument_fingerprint": fingerprint,
            "action_effect": effect,
            "gate_reason": effect if effect in SEPARATE_GATE_EFFECTS else None,
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
            now = _now()
            dirty = False
            for decision in state["decisions"].values():
                if decision.get("decision") != "approval_required" or decision.get("status") in {"resolved", "expired"}:
                    continue
                expires = _parse_time(decision.get("expires_at"))
                if not expires or expires <= now:
                    decision["status"] = "expired"
                    dirty = True
            if dirty:
                self._write(state)
        return {
            "decisions": [dict(row) for row in state["decisions"].values() if row.get("operator_id") == operator_id],
            "receipts": [dict(row) for row in state["receipts"].values() if row.get("operator_id") == operator_id],
        }


authority_store = AuthorityStore()
