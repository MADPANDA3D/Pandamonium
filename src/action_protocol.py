"""Canonical JOS-P4 action calls, catalog fingerprints, and results.

This module is deliberately an envelope around the existing runners.  It does
not duplicate dispatch, authorization, or path confinement.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping


MAX_ARGUMENT_BYTES = 64 * 1024
MAX_RESULT_BYTES = 64 * 1024
MAX_SUMMARY_CHARS = 4_000
ACTION_STATUSES = frozenset(
    {"succeeded", "failed", "denied", "cancelled", "timed_out", "unknown"}
)

_TEXT_ARGUMENTS = {
    "bash": "command",
    "python": "code",
    "web_search": "query",
    "web_fetch": "url",
    "read_file": "path",
    "grep": "query",
    "glob": "pattern",
    "ls": "path",
}
_READ_ONLY = frozenset(
    {
        "get_workspace",
        "read_file",
        "grep",
        "glob",
        "ls",
        "web_search",
        "web_fetch",
        "get_runtime_status",
        "read_agent_task",
        "search_jarvis_knowledge",
        "read_calendar",
        "list_sessions",
        "search_chats",
        "list_email_accounts",
        "list_emails",
        "read_email",
    }
)
_EVIDENCE_KEYS = (
    "task_id",
    "worker",
    "workspace",
    "event_id",
    "artifact_id",
    "artifact_key",
    "document_id",
    "doc_id",
    "path",
    "research_session_id",
    "exit_code",
    "status",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _schema_name(schema: Mapping[str, Any]) -> str:
    function = schema.get("function")
    if isinstance(function, Mapping):
        return str(function.get("name") or "").strip()
    return str(schema.get("name") or "").strip()


def compose_capability_catalog(
    schemas: Iterable[Mapping[str, Any]] = (),
    *,
    fallback_names: Iterable[str] = (),
) -> dict[str, Any]:
    """Build a unique effective catalog and stable fingerprint for one turn."""
    by_name: dict[str, dict[str, Any]] = {}
    conflicts: set[str] = set()
    for raw in schemas:
        name = _schema_name(raw)
        if not name:
            continue
        schema = dict(raw)
        if name in by_name and by_name[name] != schema:
            conflicts.add(name)
            continue
        by_name.setdefault(name, schema)
    for raw_name in fallback_names:
        name = str(raw_name or "").strip()
        if name:
            by_name.setdefault(name, {"name": name})
    for name in conflicts:
        by_name.pop(name, None)
    canonical = json.dumps(by_name, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return {
        "version": f"jos-p4:{digest[:16]}",
        "fingerprint": digest,
        "names": sorted(by_name),
        "schemas": by_name,
        "conflicts": sorted(conflicts),
    }


def _arguments_from_text(name: str, content: Any) -> Any:
    if isinstance(content, Mapping):
        return dict(content)
    text = str(content or "").strip()
    if not text:
        return {}
    if text.startswith(("{", "[")):
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError):
            return text
        return parsed
    key = _TEXT_ARGUMENTS.get(name)
    return {key: text} if key else {"content": text}


def normalize_action_call(
    *,
    request_id: str,
    call_id: str,
    agent_id: str,
    actor: str,
    capability_version: str,
    name: str,
    arguments: Any,
    target: str,
    authority_ref: str | None,
    limits: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Map native-provider or textual syntax to the JOS-P4 logical call."""
    return {
        "request_id": str(request_id),
        "call_id": str(call_id),
        "agent_id": str(agent_id),
        "actor": str(actor),
        "capability_version": str(capability_version),
        "name": str(name),
        "arguments": _arguments_from_text(str(name), arguments),
        "target": str(target),
        "authority_ref": authority_ref,
        "limits": dict(limits or {}),
    }


def classify_target(name: str, *, mcp_names: set[str], extension_names: set[str]) -> str:
    if name in extension_names:
        return "extension"
    if name in mcp_names or name.startswith("mcp__"):
        return "mcp"
    if name in {"start_agent_task", "read_agent_task"}:
        return "worker"
    if name == "ui_control":
        return "ui"
    return "tool"


def _json_size(value: Any) -> int:
    return len(json.dumps(value, separators=(",", ":"), default=str).encode("utf-8"))


def _matches_type(value: Any, expected: Any) -> bool:
    if isinstance(expected, list):
        return any(_matches_type(value, item) for item in expected)
    return {
        "object": isinstance(value, Mapping),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(str(expected), True)


def _validate_value(value: Any, schema: Mapping[str, Any], path: str) -> str | None:
    expected = schema.get("type")
    if expected and not _matches_type(value, expected):
        return f"{path} must be {expected}"
    if "enum" in schema and value not in schema["enum"]:
        return f"{path} is not an allowed value"
    if isinstance(value, str) and schema.get("maxLength") is not None:
        if len(value) > int(schema["maxLength"]):
            return f"{path} exceeds maxLength"
    if isinstance(value, list):
        if schema.get("maxItems") is not None and len(value) > int(schema["maxItems"]):
            return f"{path} exceeds maxItems"
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, item in enumerate(value):
                error = _validate_value(item, item_schema, f"{path}[{index}]")
                if error:
                    return error
    if isinstance(value, Mapping):
        for required in schema.get("required") or ():
            if required not in value:
                return f"{path}.{required} is required"
        properties = schema.get("properties")
        if isinstance(properties, Mapping):
            for key, item in value.items():
                child = properties.get(key)
                if isinstance(child, Mapping):
                    error = _validate_value(item, child, f"{path}.{key}")
                    if error:
                        return error
    return None


def validate_action_call(call: Mapping[str, Any], catalog: Mapping[str, Any]) -> dict[str, str] | None:
    """Fail closed for absent, conflicting, malformed, or oversized calls."""
    name = str(call.get("name") or "")
    if name in set(catalog.get("conflicts") or ()):
        return {"category": "catalog_conflict", "detail": f"Conflicting capability: {name}"}
    schema = (catalog.get("schemas") or {}).get(name)
    if not schema:
        return {"category": "unknown_capability", "detail": f"Capability is unavailable: {name}"}
    arguments = call.get("arguments")
    if not isinstance(arguments, Mapping):
        return {"category": "malformed_arguments", "detail": "Action arguments must be a JSON object"}
    if _json_size(arguments) > MAX_ARGUMENT_BYTES:
        return {"category": "arguments_too_large", "detail": "Action arguments exceed the server limit"}
    parameters = schema.get("parameters")
    function = schema.get("function")
    if isinstance(function, Mapping):
        parameters = function.get("parameters")
    if isinstance(parameters, Mapping):
        error = _validate_value(arguments, parameters, "arguments")
        if error:
            return {"category": "schema_validation", "detail": error}
    return None


def denied_action_result(
    call: Mapping[str, Any], error: Mapping[str, Any], *, at: str | None = None
) -> dict[str, Any]:
    moment = at or utc_now()
    return {
        "request_id": call.get("request_id"),
        "call_id": call.get("call_id"),
        "status": "denied",
        "summary": str(error.get("detail") or "Action denied")[:MAX_SUMMARY_CHARS],
        "structured": {},
        "evidence": {"kind": "policy_decision", "verified": True},
        "started_at": moment,
        "finished_at": moment,
        "retry_safe": False,
        "error": {"category": str(error.get("category") or "denied"), "detail": str(error.get("detail") or "Action denied")},
    }


def _status_for(result: Mapping[str, Any]) -> str:
    explicit = str(result.get("status") or "").lower().replace("-", "_")
    if explicit in ACTION_STATUSES:
        return explicit
    if explicit in {"timeout", "timedout"} or result.get("timed_out"):
        return "timed_out"
    if explicit in {"canceled", "cancelled"} or result.get("cancelled") or result.get("canceled"):
        return "cancelled"
    if explicit in {"outcome_unknown", "indeterminate"} or result.get("outcome_unknown"):
        return "unknown"
    if result.get("blocked") or result.get("denied"):
        return "denied"
    if result.get("error") or result.get("success") is False or result.get("ok") is False:
        return "failed"
    exit_code = result.get("exit_code")
    if exit_code not in (None, 0):
        return "failed"
    return "succeeded"


def _bounded_structured(result: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(result)
    if _json_size(value) <= MAX_RESULT_BYTES:
        return value
    kept = {key: value[key] for key in _EVIDENCE_KEYS if key in value}
    kept["truncated"] = True
    kept["original_bytes"] = _json_size(value)
    return kept


def _summary(result: Mapping[str, Any], status: str, description: str) -> str:
    for key in ("summary", "error", "output", "stdout", "response", "content", "results"):
        value = result.get(key)
        if value not in (None, "", [], {}):
            if not isinstance(value, str):
                value = json.dumps(value, default=str)
            return str(value)[:MAX_SUMMARY_CHARS]
    return (description or status.replace("_", " "))[:MAX_SUMMARY_CHARS]


def build_action_result(
    call: Mapping[str, Any],
    raw_result: Any,
    *,
    started_at: str,
    finished_at: str,
    description: str = "",
) -> dict[str, Any]:
    """Normalize heterogeneous native runner output without upgrading claims."""
    result = dict(raw_result) if isinstance(raw_result, Mapping) else {"error": "Runner returned a non-object result"}
    status = _status_for(result)
    evidence_values = {key: result[key] for key in _EVIDENCE_KEYS if key in result}
    evidence = {
        "kind": "native_result",
        "verified": status in {"succeeded", "failed", "denied", "cancelled", "timed_out"},
        **evidence_values,
    }
    explicit_retry = result.get("retry_safe")
    retry_safe = bool(explicit_retry) if explicit_retry is not None else (
        status == "failed" and str(call.get("name") or "") in _READ_ONLY
    )
    if status in {"succeeded", "denied", "cancelled", "unknown"}:
        retry_safe = False
    error = None
    if status != "succeeded":
        detail = str(result.get("error") or result.get("detail") or status.replace("_", " "))
        error = {"category": status, "detail": detail[:MAX_SUMMARY_CHARS]}
    return {
        "request_id": call.get("request_id"),
        "call_id": call.get("call_id"),
        "status": status,
        "summary": _summary(result, status, description),
        "structured": _bounded_structured(result),
        "evidence": evidence,
        "started_at": started_at,
        "finished_at": finished_at,
        "retry_safe": retry_safe,
        "error": error,
    }
