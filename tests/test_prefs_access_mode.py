"""Access-mode pref endpoint validation (MAD-885)."""
import json
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from tests.helpers.import_state import preserve_import_state

with preserve_import_state("core.database", "src.database", "routes.prefs_routes"):
    if "core.database" not in sys.modules:
        _core_db = types.ModuleType("core.database")
        for _name in [
            "SessionLocal", "ModelEndpoint", "Session", "ChatMessage", "Document",
            "DocumentVersion", "GalleryImage", "GalleryAlbum", "Note",
            "CalendarCal", "CalendarEvent", "ScheduledTask", "TaskRun",
            "McpServer", "ProviderAuthSession", "Base",
        ]:
            setattr(_core_db, _name, MagicMock())
        _core_db.utcnow_naive = MagicMock()
        sys.modules["core.database"] = _core_db

    import routes.prefs_routes as prefs_routes


def _request(user="leo"):
    return SimpleNamespace(
        state=SimpleNamespace(current_user=user, api_token=False),
        app=SimpleNamespace(state=SimpleNamespace(auth_manager=None)),
    )


def _set_pref(monkeypatch, tmp_path):
    prefs_file = tmp_path / "user_prefs.json"
    monkeypatch.setattr(prefs_routes, "PREFS_FILE", str(prefs_file))
    router = prefs_routes.setup_prefs_routes()
    for route in router.routes:
        if getattr(route, "path", "") == "/api/prefs/{key}" and "PUT" in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError("PUT /api/prefs/{key} route not found")


@pytest.mark.asyncio
async def test_access_mode_valid_values_persist(monkeypatch, tmp_path):
    set_pref = _set_pref(monkeypatch, tmp_path)
    for valid in ("ask_for_approval", "approve_for_me", "full_access"):
        result = await set_pref(_request(), "access_mode", {"value": valid})
        assert result == {"key": "access_mode", "value": valid}
    stored = json.loads((tmp_path / "user_prefs.json").read_text())
    assert stored["_users"]["leo"]["access_mode"] == "full_access"


@pytest.mark.asyncio
async def test_access_mode_invalid_value_rejected(monkeypatch, tmp_path):
    prefs_file = tmp_path / "user_prefs.json"
    prefs_file.write_text(json.dumps({"_users": {"leo": {"density": "compact"}}}))
    set_pref = _set_pref(monkeypatch, tmp_path)
    with pytest.raises(HTTPException) as exc:
        await set_pref(_request(), "access_mode", {"value": "always_run"})
    assert exc.value.status_code == 400
    stored = json.loads(prefs_file.read_text())
    assert "access_mode" not in stored["_users"]["leo"]
    assert stored["_users"]["leo"]["density"] == "compact"


@pytest.mark.asyncio
async def test_other_pref_keys_unaffected(monkeypatch, tmp_path):
    set_pref = _set_pref(monkeypatch, tmp_path)
    result = await set_pref(_request(), "density", {"value": "compact"})
    assert result == {"key": "density", "value": "compact"}