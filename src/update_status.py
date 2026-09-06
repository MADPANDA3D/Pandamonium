"""Release and persisted updater status for the fixed footer."""

from __future__ import annotations

import asyncio
import platform
import time
from typing import Any

from core.constants import APP_VERSION
from src.release_updater import (
    ROOT,
    UpdateError,
    current_revision,
    discover_release,
    installation_status,
    version_tuple,
)

_CACHE_SECONDS = 300
_ERROR_CACHE_SECONDS = 30
_CACHE: dict[str, Any] = {"expires_at": 0.0, "payload": None}
_LOCK = asyncio.Lock()


def _base_payload() -> dict[str, Any]:
    return {
        "version": APP_VERSION,
        "commit": current_revision(ROOT),
        "release": ROOT.name if ROOT.parent.name == "releases" else None,
        "channel": "stable",
        "latest_version": None,
        "latest_commit": None,
        "update_available": False,
        "update_url": None,
        "update_status": "unknown",
        "compatible": None,
        "compatibility_reason": None,
        "can_update": False,
        "installation": installation_status(),
        "release_check": {"status": "not_checked", "message": None},
    }


def _compatibility(candidate: dict[str, Any]) -> tuple[bool, str | None]:
    contract = candidate.get("compatibility") or {}
    minimum_version = str(contract.get("minimum_version") or "")
    minimum_python = str(contract.get("minimum_python") or "")
    if minimum_version and version_tuple(APP_VERSION) < version_tuple(minimum_version):
        return (
            False,
            f"Manual upgrade required from versions older than v{minimum_version}.",
        )
    if minimum_python:
        required = tuple(int(part) for part in minimum_python.split(".")[:2])
        if tuple(map(int, platform.python_version_tuple()[:2])) < required:
            return False, f"Python {minimum_python}+ is required."
    return True, None


async def release_status(*, force: bool = False) -> dict[str, Any]:
    """Compare this exact build with the configured signed release channel."""
    now = time.monotonic()
    cached = _CACHE.get("payload")
    if not force and cached and now < float(_CACHE.get("expires_at") or 0):
        return dict(cached)
    async with _LOCK:
        now = time.monotonic()
        cached = _CACHE.get("payload")
        if not force and cached and now < float(_CACHE.get("expires_at") or 0):
            return dict(cached)
        payload = _base_payload()
        cache_seconds = _CACHE_SECONDS
        try:
            candidate = await asyncio.to_thread(discover_release)
            payload["channel"] = candidate["channel"]
            payload["latest_version"] = candidate["version"]
            payload["update_url"] = candidate.get("release_url") or None
            if candidate.get("current"):
                payload.update(
                    {
                        "update_status": "current",
                        "compatible": True,
                        "release_check": {"status": "current", "message": None},
                    }
                )
            else:
                compatible, reason = _compatibility(candidate)
                payload.update(
                    {
                        "latest_commit": candidate["commit"],
                        "update_available": True,
                        "update_status": "available" if compatible else "incompatible",
                        "compatible": compatible,
                        "compatibility_reason": reason,
                        "can_update": compatible
                        and payload["installation"]["supported"],
                        "release_check": {
                            "status": "available" if compatible else "incompatible",
                            "message": reason,
                        },
                    }
                )
        except (UpdateError, OSError, ValueError) as exc:
            message = str(exc)
            payload.update(
                {
                    "update_status": "unavailable",
                    "compatibility_reason": message,
                    "release_check": {"status": "unavailable", "message": message},
                }
            )
            cache_seconds = _ERROR_CACHE_SECONDS
        _CACHE.update(expires_at=now + cache_seconds, payload=dict(payload))
        return payload
