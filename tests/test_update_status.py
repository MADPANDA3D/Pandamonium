from __future__ import annotations

import pytest

from src import update_status


@pytest.mark.asyncio
async def test_release_status_reports_actionable_newer_patch(monkeypatch):
    monkeypatch.setattr(update_status, "APP_VERSION", "1.0.9")
    monkeypatch.setattr(
        update_status,
        "installation_status",
        lambda: {
            "supported": True,
            "reason": None,
            "kind": "managed-native",
            "root": "/opt/pandamonium",
            "trigger": "systemd-path",
        },
    )
    monkeypatch.setattr(
        update_status,
        "discover_release",
        lambda: {
            "version": "1.0.10",
            "tag": "v1.0.10",
            "commit": "a" * 40,
            "channel": "stable",
            "release_url": "https://github.com/MADPANDA3D/Pandamonium/releases/tag/v1.0.10",
            "current": False,
            "compatibility": {"minimum_version": "1.0.9", "minimum_python": "3.10"},
        },
    )
    update_status._CACHE.update(expires_at=0.0, payload=None)

    status = await update_status.release_status(force=True)

    assert status["version"] == "1.0.9"
    assert status["latest_version"] == "1.0.10"
    assert status["latest_commit"] == "a" * 40
    assert status["update_available"] is True
    assert status["can_update"] is True
    assert status["compatible"] is True
    assert status["update_status"] == "available"


@pytest.mark.asyncio
async def test_release_status_reports_current_without_update_action(monkeypatch):
    monkeypatch.setattr(update_status, "APP_VERSION", "1.0.10")
    monkeypatch.setattr(
        update_status,
        "discover_release",
        lambda: {
            "version": "1.0.10",
            "tag": "v1.0.10",
            "channel": "stable",
            "release_url": "https://github.com/MADPANDA3D/Pandamonium/releases/tag/v1.0.10",
            "current": True,
        },
    )
    update_status._CACHE.update(expires_at=0.0, payload=None)

    status = await update_status.release_status(force=True)

    assert status["update_status"] == "current"
    assert status["update_available"] is False
    assert status["update_url"] is None
