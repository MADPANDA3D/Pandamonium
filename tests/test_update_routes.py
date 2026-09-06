from __future__ import annotations

import importlib
from pathlib import Path

from fastapi.testclient import TestClient

CANDIDATE = {
    "version": "1.0.11",
    "commit": "2" * 40,
    "channel": "stable",
    "current": False,
    "artifact": {"url": "https://github.com/proof"},
}


def _headers() -> dict[str, str]:
    middleware = importlib.import_module("core.middleware")
    return {middleware.INTERNAL_TOOL_HEADER: middleware.INTERNAL_TOOL_TOKEN}


def _client() -> TestClient:
    application = importlib.import_module("app").app
    return TestClient(application, client=("127.0.0.1", 50000))


def test_update_apply_requires_exact_checked_release(monkeypatch):
    release_updater = importlib.import_module("src.release_updater")
    queued = []
    monkeypatch.setattr(release_updater, "discover_release", lambda: CANDIDATE)
    monkeypatch.setattr(
        release_updater,
        "queue_update",
        lambda candidate: queued.append(candidate) or {"status": "queued"},
    )
    client = _client()

    missing = client.post("/api/update/apply", headers=_headers())
    changed = client.post(
        "/api/update/apply",
        headers=_headers(),
        json={"version": "1.0.11", "commit": "3" * 40},
    )
    accepted = client.post(
        "/api/update/apply",
        headers=_headers(),
        json={"version": "1.0.11", "commit": "2" * 40},
    )

    assert missing.status_code == 409
    assert changed.status_code == 409
    assert accepted.json() == {"status": "queued"}
    assert queued == [CANDIDATE]


def test_update_status_is_admin_gated_and_units_preserve_privilege_boundary(
    monkeypatch,
):
    release_updater = importlib.import_module("src.release_updater")
    monkeypatch.setattr(
        release_updater, "public_update_state", lambda: {"status": "idle"}
    )
    client = _client()

    assert client.get("/api/update/status").status_code in {401, 403}
    assert client.get("/api/update/status", headers=_headers()).json() == {
        "status": "idle"
    }

    root = Path(__file__).resolve().parents[1]
    service = (root / "pandamonium-updater.service").read_text(encoding="utf-8")
    path_unit = (root / "pandamonium-updater.path").read_text(encoding="utf-8")
    recovery = (root / "pandamonium-update-recover.service").read_text(encoding="utf-8")
    assert "User=root" in service
    assert "NoNewPrivileges=true" in service
    assert "ProtectSystem=full" in service
    assert "pandamonium-update apply --request" in service
    assert "PathExists=/var/lib/pandamonium/data/updates/request.json" in path_unit
    assert "Before=pandamonium.service" in recovery
    assert "pandamonium-update recover" in recovery
