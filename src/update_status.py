"""Read-only release status for the sidebar version widget."""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

import httpx

from core.constants import APP_VERSION


_LATEST_RELEASE_API = "https://api.github.com/repos/MADPANDA3D/Pandamonium/releases/latest"
_RELEASE_URL = "https://github.com/MADPANDA3D/Pandamonium/releases/tag/v{}"
_CACHE_SECONDS = 300
_CACHE: dict[str, Any] = {"expires_at": 0.0, "payload": None}
_LOCK = asyncio.Lock()
_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def _version_tuple(value: str) -> tuple[int, int, int]:
    match = _VERSION_RE.fullmatch(str(value or "").strip())
    if not match:
        raise ValueError("invalid release version")
    return tuple(int(part) for part in match.groups())


def _base_payload() -> dict[str, Any]:
    return {
        "version": APP_VERSION,
        "latest_version": None,
        "update_available": False,
        "update_url": None,
        "update_status": "unknown",
    }


async def release_status(*, force: bool = False) -> dict[str, Any]:
    """Compare this build with the canonical GitHub release, without mutating it."""
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
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                response = await client.get(
                    _LATEST_RELEASE_API,
                    headers={"Accept": "application/vnd.github+json"},
                )
            response.raise_for_status()
            tag = str((response.json() or {}).get("tag_name") or "").strip()
            latest = ".".join(str(part) for part in _version_tuple(tag))
            available = _version_tuple(latest) > _version_tuple(APP_VERSION)
            payload.update({
                "latest_version": latest,
                "update_available": available,
                "update_url": _RELEASE_URL.format(latest) if available else None,
                "update_status": "available" if available else "current",
            })
        except (httpx.HTTPError, TypeError, ValueError):
            # The running version is still authoritative when GitHub is
            # unreachable; the UI reports an unknown check instead of lying.
            pass

        _CACHE.update(expires_at=now + _CACHE_SECONDS, payload=dict(payload))
        return payload
