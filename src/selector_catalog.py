"""Owner-safe model, agent, and worker selector discovery."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any

from src.agent_identity import agent_identity_status
from src.model_discovery import installation_capabilities


def _stable_id(kind: str, *parts: object) -> str:
    digest = hashlib.sha256("\0".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:20]
    return f"{kind}:{digest}"


def _bounded_text(value: object, fallback: str, limit: int = 200) -> str:
    text = " ".join(str(value or "").split())[:limit]
    return text or fallback


def _permissions(scopes: list[str], delegation: str = "none") -> dict[str, Any]:
    return {
        "requires_authenticated_request": True,
        "configured_scopes": list(dict.fromkeys(scopes)) or ["owner:current"],
        "delegation": delegation,
    }


def _action(name: str, effect: str = "read") -> dict[str, Any]:
    return {
        "name": name,
        "effect": effect,
        "authorization": "explicit_request",
        "reversible": True,
    }


def build_selector_catalog(
    model_payload: dict[str, Any],
    worker_statuses: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build a strict discovery document plus non-secret UI selection refs."""
    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    entities: list[dict[str, Any]] = []
    selections: list[dict[str, Any]] = []

    identity = agent_identity_status()
    agent_id = _stable_id("agent", identity.get("agent_id") or "configured")
    identity_health = "healthy" if identity.get("status") == "healthy" else "degraded"
    entities.append({
        "kind": "agent",
        "id": agent_id,
        "display_name": _bounded_text(identity.get("display_name"), "Configured agent"),
        "availability": "available",
        "ownership": {"scope": "owner", "id": "owner:current"},
        "health": {"state": identity_health},
        "permissions": _permissions(["owner:current"], "narrower_only"),
        "source": {"type": "configuration", "ref": "src/agent_identity.py#agent_identity_status"},
        "actions": [_action("converse")],
    })
    selections.append({
        "entity_id": agent_id,
        "kind": "agent",
        "target": "jarvis",
        "capabilities": ["model"],
        "selectable": True,
        "reason": None,
    })

    for endpoint in model_payload.get("items") or []:
        if not isinstance(endpoint, dict) or (endpoint.get("model_type") or "llm") != "llm":
            continue
        endpoint_id = str(endpoint.get("endpoint_id") or "")
        models = list(endpoint.get("models") or []) + list(endpoint.get("models_extra") or [])
        displays = list(endpoint.get("models_display") or []) + list(endpoint.get("models_extra_display") or [])
        unavailable = endpoint.get("offline") is True
        for index, model_id_value in enumerate(models):
            model_id = str(model_id_value or "").strip()
            if not model_id:
                continue
            entity_id = _stable_id("model", endpoint_id, model_id)
            connection_id = _stable_id("connection", endpoint_id or "runtime")
            display = displays[index] if index < len(displays) else model_id.split("/")[-1]
            entities.append({
                "kind": "model",
                "id": entity_id,
                "display_name": _bounded_text(display, "Configured model"),
                "availability": "unavailable" if unavailable else "available",
                "ownership": {"scope": "connection", "id": connection_id},
                "health": {
                    "state": "unavailable" if unavailable else "healthy",
                    **({"reason": "endpoint_offline"} if unavailable else {}),
                },
                "permissions": _permissions(["owner:current"]),
                "source": {"type": "runtime_discovery", "ref": "routes/model_routes.py#api_models"},
                "actions": [_action("infer")],
            })
            selections.append({
                "entity_id": entity_id,
                "kind": "model",
                "model_id": model_id,
                "endpoint_id": endpoint_id,
                "capabilities": ["model"],
                "selectable": not unavailable,
                "reason": "endpoint_offline" if unavailable else None,
            })

    for worker_id, details in worker_statuses.items():
        if (
            not isinstance(details, dict)
            or details.get("configured") is not True
            or details.get("ready") is not True
        ):
            continue
        capabilities = installation_capabilities(details)
        if not capabilities or capabilities == ["model"]:
            continue
        kind = "worker" if "codex" in capabilities else "agent"
        entity_id = _stable_id(kind, worker_id)
        workspace_scopes = [
            f"workspace:{workspace}"
            for workspace in details.get("workspaces") or []
            if re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", str(workspace))
        ]
        entities.append({
            "kind": kind,
            "id": entity_id,
            "display_name": _bounded_text(details.get("label"), "Configured peer"),
            "availability": "available",
            "ownership": {"scope": "installation", "id": "installation:current"},
            "health": {
                "state": "healthy",
                "checked_at": generated_at,
            },
            "permissions": _permissions(workspace_scopes or ["owner:current"], "narrower_only"),
            "source": {"type": "worker", "ref": "src/jarvis_agent.py#worker_statuses"},
            "actions": [
                _action("start_task" if kind == "worker" else "converse"),
                *([_action("cancel_task")] if kind == "worker" else []),
            ],
        })
        selections.append({
            "entity_id": entity_id,
            "kind": kind,
            "target": worker_id,
            "capabilities": capabilities,
            "selectable": True,
            "reason": None,
        })

    return {
        "discovery": {
            "schema_version": "pandamonium.discovery.v1",
            "generated_at": generated_at,
            "entities": entities,
        },
        "selections": selections,
    }
