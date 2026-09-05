from __future__ import annotations

import pytest

from src import update_status


class _Response:
    def __init__(self, tag: str):
        self._tag = tag

    def raise_for_status(self):
        return None

    def json(self):
        return {"tag_name": self._tag}


class _Client:
    def __init__(self, tag: str):
        self._tag = tag

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, *_args, **_kwargs):
        return _Response(self._tag)


@pytest.mark.asyncio
async def test_release_status_reports_actionable_newer_patch(monkeypatch):
    monkeypatch.setattr(update_status, "APP_VERSION", "1.0.9")
    monkeypatch.setattr(update_status.httpx, "AsyncClient", lambda **_kwargs: _Client("v1.0.10"))
    update_status._CACHE.update(expires_at=0.0, payload=None)

    status = await update_status.release_status(force=True)

    assert status == {
        "version": "1.0.9",
        "latest_version": "1.0.10",
        "update_available": True,
        "update_url": "https://github.com/MADPANDA3D/Pandamonium/releases/tag/v1.0.10",
        "update_status": "available",
    }


@pytest.mark.asyncio
async def test_release_status_reports_current_without_update_action(monkeypatch):
    monkeypatch.setattr(update_status, "APP_VERSION", "1.0.10")
    monkeypatch.setattr(update_status.httpx, "AsyncClient", lambda **_kwargs: _Client("v1.0.10"))
    update_status._CACHE.update(expires_at=0.0, payload=None)

    status = await update_status.release_status(force=True)

    assert status["update_status"] == "current"
    assert status["update_available"] is False
    assert status["update_url"] is None
