"""Allowlisted, redacted diagnostic bundle for in-app bug reports (MAD-856).

The bundle is built server-side from a fixed allowlist of fields. It never
includes message contents, prompt or completion text, API keys, cookies,
authorization headers, raw logs, local file paths, private hostnames, or URLs.
Everything that is not explicitly constructed here is omitted by design.

The report review screen displays this exact object (and the resulting issue
body) before the user confirms, so collection is transparent.
"""

from __future__ import annotations

import platform
import re
import sys
import time
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from src.feedback_report import redact_automatic_text, safe_route


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _platform_class() -> str:
    if sys.platform.startswith("linux"):
        try:
            if __import__("pathlib").Path("/.dockerenv").exists():
                return "linux-container"
        except OSError:
            pass
        return "linux"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("win"):
        return "windows"
    return "other"


def _app_info() -> dict[str, Any]:
    """Version/revision/installation method — the same facts the updater uses."""
    from src.constants import APP_VERSION

    revision = ""
    installation_method = "unknown"
    try:
        from src.release_updater import current_revision, installation_status

        revision = str(current_revision() or "")
        installation_method = str(installation_status().get("kind") or "unknown")
    except Exception:
        pass
    return {
        "version": str(APP_VERSION),
        "revision": revision[:40],
        "installation_method": installation_method,
        "runtime": f"python {sys.version_info.major}.{sys.version_info.minor}",
        "platform_class": _platform_class(),
        "platform_release": redact_automatic_text(platform.release(), limit=40),
    }


def _latest_error(session_id: str, owner: Optional[str]) -> Optional[dict[str, Any]]:
    """Most recent controlled error classification from operational events."""
    try:
        from src.operational_protocol import events

        rows = events.query(session_id=session_id or None, operator_id=owner, limit=200)
    except Exception:
        return None
    for row in reversed(rows):
        error = row.get("error")
        status = str(row.get("status") or "")
        if isinstance(error, Mapping) and error:
            return {
                "category": redact_automatic_text(error.get("category"), limit=80),
                "detail": redact_automatic_text(error.get("detail"), limit=200),
                "component": redact_automatic_text(row.get("component"), limit=80),
                "status": status[:40],
                "at": str(row.get("timestamp") or "")[:40],
            }
        if status in {"failed", "denied", "timed_out", "unavailable"}:
            return {
                "category": redact_automatic_text(row.get("component") or "operation", limit=80),
                "detail": status,
                "component": redact_automatic_text(row.get("component"), limit=80),
                "status": status[:40],
                "at": str(row.get("timestamp") or "")[:40],
            }
    return None


def _service_health_timestamps(session_id: str, owner: Optional[str]) -> dict[str, Any]:
    """Last recorded health events plus the live API liveness timestamp.

    Deliberately does not run the full health probe: the report must be fast
    and must not depend on configured integrations. It answers "when was
    health last known?" rather than "is everything healthy now?".
    """
    checks: list[dict[str, Any]] = []
    try:
        from src.operational_protocol import events

        rows = events.query(session_id=session_id or None, operator_id=owner, limit=500)
        for row in reversed(rows):
            if str(row.get("event_type") or "") != "health":
                continue
            checks.append(
                {
                    "component": redact_automatic_text(row.get("component"), limit=80),
                    "status": str(row.get("status") or "")[:40],
                    "at": str(row.get("timestamp") or "")[:40],
                }
            )
            if len(checks) >= 5:
                break
    except Exception:
        checks = []
    return {
        "api": {"status": "healthy", "checked_at": _iso_now()},
        "last_reported": checks,
    }


def _correlation(request_id: str) -> dict[str, str]:
    value = str(request_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,80}", value):
        return {}
    return {"request_id": value}


def build_diagnostics(
    *,
    route: str = "",
    session_id: str = "",
    request_id: str = "",
    owner: Optional[str] = None,
    client: Optional[Mapping[str, Any]] = None,
    include_diagnostics: bool = True,
) -> dict[str, Any]:
    """Build the bounded diagnostic bundle shown in review and posted publicly."""
    if not include_diagnostics:
        return {"included": False}
    session_value = str(session_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,80}", session_value):
        session_value = ""
    client_data = client if isinstance(client, Mapping) else {}
    bundle: dict[str, Any] = {
        "included": True,
        "generated_at": _iso_now(),
        "app": _app_info(),
        "route": safe_route(route),
        "client": {
            "user_agent_class": redact_automatic_text(client_data.get("user_agent_class"), limit=80),
            "viewport_class": redact_automatic_text(client_data.get("viewport_class"), limit=40),
            "locale": redact_automatic_text(client_data.get("locale"), limit=40),
        },
        "session": {"id": session_value} if session_value else {},
        "correlation": _correlation(request_id),
        "latest_error": _latest_error(session_value, owner),
        "service_health": _service_health_timestamps(session_value, owner),
    }
    return bundle