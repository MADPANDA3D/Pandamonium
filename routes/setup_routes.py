"""Setup status routes — read-only first-run setup truth for the guide/wizard.

Every field is derived from an existing owner (identity diagnostics, model
registry, settings, integration store, extension registry, local updater
state). This module stores no state of its own and never exposes the private
constitution body or secrets.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Request

from src.constants import APP_VERSION

logger = logging.getLogger(__name__)

# Stable id of the MAD MCP Portal server row (kept in sync with
# routes/mcp_routes.py; duplicated here so this read path does not import the
# full MCP route module).
PORTAL_SERVER_ID = "mad-mcp-portal"


def _current_user(request: Request) -> str:
    return str(getattr(request.state, "current_user", "") or "")


def _is_admin(request: Request) -> bool:
    auth_mgr = getattr(getattr(request, "app", None), "state", None)
    auth_mgr = getattr(auth_mgr, "auth_manager", None)
    if auth_mgr is None or not getattr(auth_mgr, "is_configured", False):
        return True
    user = _current_user(request)
    if not user:
        return False
    try:
        return bool(auth_mgr.is_admin(user))
    except Exception:
        return False


def _project_identity() -> Dict[str, Any]:
    from src.agent_identity import agent_identity_status

    status = agent_identity_status()
    return {
        "configured": status.get("source") == "configured",
        "display_name": str(status.get("display_name") or ""),
        "status": str(status.get("status") or "unknown"),
    }


def _visible_model_ids(cached_models: Any, hidden_models: Any) -> set[str]:
    def _load(raw: Any) -> list[Any]:
        if not raw:
            return []
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            return []
        return value if isinstance(value, list) else []

    hidden = {str(item) for item in _load(hidden_models)}
    return {str(item) for item in _load(cached_models) if str(item) not in hidden}


def _is_chat_capable(endpoint: Any) -> bool:
    """True for chat/LLM endpoints; legacy rows have no model_type."""
    model_type = getattr(endpoint, "model_type", None)
    return model_type in (None, "", "llm")


def _project_model(user: str, is_admin: bool) -> Dict[str, Any]:
    from core.database import ModelEndpoint, SessionLocal
    from src.model_response_diagnosis import recovery_guidance, validation_entry

    db = SessionLocal()
    try:
        query = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True)  # noqa: E712
        if user and not is_admin:
            query = query.filter(
                (ModelEndpoint.owner == user) | (ModelEndpoint.owner.is_(None))
            )
        endpoints = [endpoint for endpoint in query.all() if _is_chat_capable(endpoint)]
        models: set[str] = set()
        visible_by_endpoint: Dict[str, set] = {}
        for endpoint in endpoints:
            visible = _visible_model_ids(
                getattr(endpoint, "cached_models", None),
                getattr(endpoint, "hidden_models", None),
            )
            visible.update(_visible_model_ids(getattr(endpoint, "pinned_models", None), None))
            models.update(visible)
            visible_by_endpoint[str(getattr(endpoint, "id", ""))] = visible

        # Only a successful minimal completion means "ready". Saved endpoint or
        # discovered model fields alone are not validation evidence.
        validated = 0
        failed: list[Dict[str, Any]] = []
        for endpoint_id, visible in visible_by_endpoint.items():
            for model_id in visible:
                entry = validation_entry(endpoint_id, model_id)
                if not entry:
                    continue
                if entry.get("state") == "validated" or entry.get("category") == "ok":
                    validated += 1
                    continue
                failed.append({
                    "endpoint_id": endpoint_id,
                    "model": model_id,
                    "category": str(entry.get("category") or "url_http"),
                    "action": str(entry.get("action") or ""),
                    "guidance": recovery_guidance(
                        str(entry.get("category") or "url_http")
                    ),
                    "at": str(entry.get("validated_at") or ""),
                })

        if not endpoints:
            state = "unconfigured"
        elif validated:
            state = "validated"
        elif failed:
            state = "failed"
        elif models:
            state = "discovered"
        else:
            state = "configured"

        last_failure = None
        if failed:
            last_failure = max(failed, key=lambda item: item["at"])

        if state == "validated":
            guidance = ""
        elif state == "failed" and last_failure:
            guidance = last_failure["guidance"]
        elif state == "discovered":
            guidance = (
                "Run the model test to validate a model with one minimal completion "
                "before chat is marked ready."
            )
        elif state == "configured":
            guidance = (
                "Discover or add a model on the endpoint, then run the model test "
                "to validate it."
            )
        else:
            guidance = "Connect a model engine in Settings → Models to get started."

        return {
            "usable": validated > 0,
            "state": state,
            "endpoints": len(endpoints),
            "models": len(models),
            "validated": validated,
            "failed": len(failed),
            "last_failure": last_failure,
            "guidance": guidance,
        }
    finally:
        db.close()


def _project_voice() -> Dict[str, Any]:
    from src.settings import load_settings

    settings = load_settings()
    provider = str(settings.get("tts_provider") or "disabled")
    enabled = bool(settings.get("tts_enabled", True))
    return {
        "ready": enabled and provider != "disabled",
        "enabled": enabled,
        "provider": provider,
    }


def _project_integrations() -> Dict[str, Any]:
    configured = 0
    try:
        from src.integrations import load_integrations

        configured = len(load_integrations() or [])
    except Exception:
        logger.debug("setup status: integration store unavailable", exc_info=True)

    portal_connected = False
    try:
        from core.database import McpServer, SessionLocal

        db = SessionLocal()
        try:
            server = (
                db.query(McpServer)
                .filter(McpServer.id == PORTAL_SERVER_ID)
                .first()
            )
            portal_connected = bool(server and server.is_enabled)
        finally:
            db.close()
    except Exception:
        logger.debug("setup status: portal state unavailable", exc_info=True)

    return {"configured": configured, "portal_connected": portal_connected}


def _project_extensions() -> Dict[str, Any]:
    try:
        from src.extension_registry import ExtensionRegistry

        records = (ExtensionRegistry().snapshot().get("extensions") or {})
        enabled = sum(1 for record in records.values() if record.get("enabled"))
        return {"installed": len(records), "enabled": enabled}
    except Exception:
        logger.debug("setup status: extension registry unavailable", exc_info=True)
        return {"installed": 0, "enabled": 0}


def _project_update(is_admin: bool) -> Optional[Dict[str, Any]]:
    if not is_admin:
        return None
    state: Dict[str, Any] = {}
    try:
        from src.release_updater import public_update_state

        state = public_update_state() or {}
    except Exception:
        logger.debug("setup status: updater state unavailable", exc_info=True)
    return {
        "version": APP_VERSION,
        "state": str(state.get("status") or "idle"),
        "target_version": state.get("target_version"),
        "rollback_available": bool(state.get("rollback_available")),
    }


def setup_setup_routes() -> APIRouter:
    router = APIRouter(tags=["setup"])

    @router.get("/api/setup/status")
    async def get_setup_status(request: Request) -> Dict[str, Any]:
        """Live first-run setup truth derived from existing owners only."""
        is_admin = _is_admin(request)
        user = _current_user(request)
        return {
            "is_admin": is_admin,
            "identity": _project_identity(),
            "model": _project_model(user, is_admin),
            "voice": _project_voice(),
            "integrations": _project_integrations(),
            "extensions": _project_extensions(),
            "update": _project_update(is_admin),
        }

    return router
