"""MAD-929: /api/auth/identities admin gating, CRUD, and constitution redaction."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import routes.auth_routes as auth_routes
import src.agent_identities as identities
from src.settings import DEFAULT_SETTINGS


SESSION_COOKIE = "odysseus_session"


class _AuthManager:
    signup_enabled = False

    def __init__(self, admin=True):
        self._admin = admin

    def get_username_for_token(self, token):
        return "operator" if token == "token" else None

    def is_admin(self, _username):
        return self._admin


def _request(body=None, token="token"):
    async def _json():
        return body if body is not None else {}
    return SimpleNamespace(cookies={SESSION_COOKIE: token}, json=_json)


def _route(router, path, method="GET"):
    return next(
        route.endpoint
        for route in router.routes
        if route.path == path and method in (route.methods or set())
    )


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(identities, "_IDENTITIES_FILE", str(tmp_path / "agent_identities.json"))
    monkeypatch.setattr(identities, "load_settings", lambda: dict(DEFAULT_SETTINGS))
    monkeypatch.setattr(auth_routes, "migrate_from_settings", lambda: None)
    return tmp_path / "agent_identities.json"


def _router(admin=True):
    return auth_routes.setup_auth_routes(_AuthManager(admin=admin))


def _create_payload(**overrides):
    payload = {
        "display_name": "Friday",
        "constitution": "Report uncertainty as uncertainty.",
        "constitution_version": "1",
        "model_profile": {"chat": {"model": "qwen3-14b", "reasoning_level": "high"}},
    }
    payload.update(overrides)
    return payload


def test_get_identities_redacts_constitution_for_non_admin(store):
    identities.create_identity(_create_payload())

    router = _router(admin=True)
    admin_result = asyncio.run(_route(router, "/api/auth/identities")(_request()))
    admin_friday = next(e for e in admin_result["identities"] if e["id"] == "friday")
    assert admin_result["constitution_included"] is True
    assert admin_friday["constitution"] == "Report uncertainty as uncertainty."
    assert admin_result["active_id"] == "assistant"

    router = _router(admin=False)
    user_result = asyncio.run(_route(router, "/api/auth/identities")(_request()))
    user_friday = next(e for e in user_result["identities"] if e["id"] == "friday")
    assert user_result["constitution_included"] is False
    assert "constitution" not in user_friday
    assert user_friday["constitution_present"] is True
    assert "Report uncertainty" not in repr(user_result)


def test_create_update_duplicate_and_delete_require_admin(store):
    router = _router(admin=False)
    create = _route(router, "/api/auth/identities", "POST")
    with pytest.raises(HTTPException) as denied:
        asyncio.run(create(_request(_create_payload())))
    assert denied.value.status_code == 403


def test_admin_crud_flow_round_trips(store):
    router = _router(admin=True)
    create = _route(router, "/api/auth/identities", "POST")
    update = _route(router, "/api/auth/identities/{identity_id}", "PUT")
    duplicate = _route(router, "/api/auth/identities/{identity_id}/duplicate", "POST")
    delete = _route(router, "/api/auth/identities/{identity_id}", "DELETE")
    active = _route(router, "/api/auth/identities/active", "POST")

    created = asyncio.run(create(_request(_create_payload())))
    assert created["id"] == "friday"
    assert created["model_profile"]["chat"]["model"] == "qwen3-14b"

    updated = asyncio.run(update("friday", _request({"display_name": "Friday Ops"})))
    assert updated["display_name"] == "Friday Ops"
    assert updated["constitution"] == "Report uncertainty as uncertainty."

    copied = asyncio.run(duplicate("friday", _request()))
    assert copied["id"] == "friday-copy"

    active_result = asyncio.run(active(_request({"identity_id": "friday"})))
    assert active_result["active_id"] == "friday"

    deleted = asyncio.run(delete("friday-copy", _request()))
    assert deleted["ok"] is True
    assert deleted["id"] == "friday-copy"

    listed = asyncio.run(_route(router, "/api/auth/identities")(_request()))
    assert [entry["id"] for entry in listed["identities"]] == ["assistant", "friday"]
    assert listed["active_id"] == "friday"


def test_invalid_identity_payloads_return_400(store):
    router = _router(admin=True)
    create = _route(router, "/api/auth/identities", "POST")

    with pytest.raises(HTTPException) as bad_name:
        asyncio.run(create(_request(_create_payload(display_name=""))))
    assert bad_name.value.status_code == 400

    with pytest.raises(HTTPException) as bad_reasoning:
        asyncio.run(create(_request(_create_payload(model_profile={"chat": {"reasoning_level": "extreme"}}))))
    assert bad_reasoning.value.status_code == 400


def test_unknown_identity_operations_return_404(store):
    router = _router(admin=True)
    update = _route(router, "/api/auth/identities/{identity_id}", "PUT")
    delete = _route(router, "/api/auth/identities/{identity_id}", "DELETE")
    duplicate = _route(router, "/api/auth/identities/{identity_id}/duplicate", "POST")
    active = _route(router, "/api/auth/identities/active", "POST")

    with pytest.raises(HTTPException) as missing:
        asyncio.run(update("ghost", _request({"display_name": "Ghost"})))
    assert missing.value.status_code == 404
    with pytest.raises(HTTPException) as missing_delete:
        asyncio.run(delete("ghost", _request()))
    assert missing_delete.value.status_code == 404
    with pytest.raises(HTTPException) as missing_copy:
        asyncio.run(duplicate("ghost", _request()))
    assert missing_copy.value.status_code == 404
    with pytest.raises(HTTPException) as missing_active:
        asyncio.run(active(_request({"identity_id": "ghost"})))
    assert missing_active.value.status_code == 404


def test_delete_last_identity_is_rejected_with_400(store):
    router = _router(admin=True)
    delete = _route(router, "/api/auth/identities/{identity_id}", "DELETE")

    with pytest.raises(HTTPException) as last:
        asyncio.run(delete("assistant", _request()))
    assert last.value.status_code == 400
