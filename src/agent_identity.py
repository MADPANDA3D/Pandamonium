"""Installation-scoped agent identity, independent of the reasoning backend."""

from __future__ import annotations

import re
from typing import Any, Mapping

from src.settings import DEFAULT_SETTINGS, load_settings


_AGENT_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_IDENTITY_KEYS = (
    "agent_id",
    "agent_display_name",
    "agent_constitution",
    "agent_constitution_version",
)


def validate_agent_identity_setting(key: str, value: Any) -> str:
    """Validate and normalize one identity setting for the authenticated API."""
    if key not in _IDENTITY_KEYS:
        raise ValueError("unknown agent identity setting")
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{key} must not be empty")
    if key == "agent_id" and not _AGENT_ID_RE.fullmatch(normalized):
        raise ValueError("agent_id must start with a lowercase letter and contain only lowercase letters, numbers, _ or -")
    if key == "agent_display_name":
        if len(normalized) > 80 or any(ord(char) < 32 for char in normalized):
            raise ValueError("agent_display_name must be 80 printable characters or fewer")
    if key == "agent_constitution" and len(normalized) > 16_000:
        raise ValueError("agent_constitution must be 16000 characters or fewer")
    if key == "agent_constitution_version" and not _VERSION_RE.fullmatch(normalized):
        raise ValueError("agent_constitution_version must be 64 letters, numbers, dots, _ or - or fewer")
    return normalized


def resolve_agent_identity(settings: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Resolve identity, falling back field-by-field when stored values are invalid."""
    values = settings if settings is not None else load_settings()
    resolved: dict[str, str] = {}
    fallback_reasons: list[str] = []
    for key in _IDENTITY_KEYS:
        try:
            resolved[key] = validate_agent_identity_setting(key, values.get(key))
        except (AttributeError, ValueError):
            resolved[key] = str(DEFAULT_SETTINGS[key])
            fallback_reasons.append(f"invalid_{key}")
    customized = any(resolved[key] != DEFAULT_SETTINGS[key] for key in _IDENTITY_KEYS)
    return {
        **resolved,
        "status": "degraded" if fallback_reasons else "healthy",
        "source": "configured" if customized else "default",
        "fallback_reasons": fallback_reasons,
    }


def configured_agent_id() -> str:
    return str(resolve_agent_identity()["agent_id"])


def configured_agent_name() -> str:
    return str(resolve_agent_identity()["agent_display_name"])


def agent_system_prompt(preset_prompt: str | None = None) -> str:
    """Mount the configured identity and preserve the active behavior preset."""
    identity = resolve_agent_identity()
    prompt = (
        f"You are {identity['agent_display_name']}, the assistant configured for this Odysseus installation "
        f"(stable agent id: {identity['agent_id']}; constitution version: "
        f"{identity['agent_constitution_version']}).\n\n{identity['agent_constitution']}"
    )
    return f"{prompt}\n\n{preset_prompt}" if preset_prompt else prompt


def agent_identity_status() -> dict[str, Any]:
    """Return diagnostics without exposing the private constitution body."""
    identity = resolve_agent_identity()
    return {
        "agent_id": identity["agent_id"],
        "display_name": identity["agent_display_name"],
        "constitution_version": identity["agent_constitution_version"],
        "status": identity["status"],
        "source": identity["source"],
        "fallback_reasons": list(identity["fallback_reasons"]),
    }
