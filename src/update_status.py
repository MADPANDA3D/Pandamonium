"""Release and persisted updater status for the fixed footer."""

from __future__ import annotations

import asyncio
import platform
import time
from pathlib import Path
from typing import Any

from core.constants import APP_VERSION
from src.release_updater import (
    REPOSITORY_URL,
    ROOT,
    UpdateError,
    current_revision,
    discover_release,
    installation_status,
    public_update_state,
    version_tuple,
)

_CACHE_SECONDS = 300
_ERROR_CACHE_SECONDS = 30
_NOTES_MAX_CHARS = 20_000
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


def curated_release_notes(version: str, *, root: Path | None = None) -> dict[str, Any]:
    """Read the curated Markdown notes for one exact release version.

    Curated notes ship inside every signed release tree at
    `.github/releases/v<version>.md`. Only that exact filename under the notes
    directory is read; an unknown or malformed version fails precisely instead
    of guessing a path or falling back to public search results.
    """
    requested = str(version or "").strip().strip("'\"")
    normalized = requested.removeprefix("v").removeprefix("V")
    try:
        version_tuple(normalized)
    except UpdateError:
        return {
            "available": False,
            "version": None,
            "reason": (
                f"'{requested[:32]}' is not a valid release version; "
                "expected a version like 1.0.47"
            ),
        }
    notes_dir = (root or ROOT) / ".github" / "releases"
    path = notes_dir / f"v{normalized}.md"
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {
            "available": False,
            "version": normalized,
            "reason": f"curated release notes for v{normalized} are not installed",
        }
    except OSError as exc:
        return {
            "available": False,
            "version": normalized,
            "reason": (
                f"curated release notes for v{normalized} could not be read: {exc}"
            ),
        }
    truncated = len(text) > _NOTES_MAX_CHARS
    if truncated:
        text = text[:_NOTES_MAX_CHARS] + (
            f"\n... (truncated at {_NOTES_MAX_CHARS} characters)"
        )
    return {
        "available": True,
        "version": normalized,
        "source": f".github/releases/v{normalized}.md",
        "markdown": text,
        "truncated": truncated,
    }


def _public_updater_state() -> dict[str, Any]:
    """Project the privileged updater receipt down to agent-safe fields."""
    try:
        state = public_update_state()
    except (OSError, ValueError) as exc:
        return {"status": "unavailable", "reason": str(exc)[:200]}
    previous = str(state.get("previous_release") or "")
    return {
        "status": state.get("status") or "idle",
        "phase": state.get("phase"),
        "progress": state.get("progress", 0),
        "message": str(state.get("message") or "")[:500] or None,
        "target_version": state.get("target_version"),
        "target_commit": state.get("target_commit"),
        "previous_release": Path(previous).name if previous else None,
        "rollback_available": bool(state.get("rollback_available")),
        "auto_rolled_back": bool(state.get("auto_rolled_back")),
        "updated_at": state.get("updated_at"),
    }


async def release_facts(
    *,
    notes_version: str | None = None,
    include_notes: bool = True,
) -> dict[str, Any]:
    """Return the installation's local release truth for agent answers.

    This is the self-knowledge source for version, release, update, and
    release-notes questions: the running version/commit, the configured
    release channel and its update status, the persisted updater state, the
    canonical repository URL, and the curated notes for the installed (or
    requested) version. When the channel is unreachable the check reports
    `unavailable` with the exact reason instead of fabricating a version.
    """
    status = await release_status()
    target = str(notes_version or "").strip() or str(status.get("version") or APP_VERSION)
    return {
        "repository": REPOSITORY_URL,
        "installed_version": status.get("version") or APP_VERSION,
        "installed_commit": status.get("commit"),
        "installed_release": status.get("release"),
        "channel": status.get("channel") or "stable",
        "update_status": status.get("update_status") or "unknown",
        "update_available": bool(status.get("update_available")),
        "latest_version": status.get("latest_version"),
        "latest_commit": status.get("latest_commit"),
        "update_url": status.get("update_url"),
        "compatibility_reason": status.get("compatibility_reason"),
        "release_check": status.get("release_check")
        or {"status": "not_checked", "message": None},
        "installation": status.get("installation") or {},
        "updater": _public_updater_state(),
        "notes_version": target,
        "notes": curated_release_notes(target) if include_notes else None,
    }
