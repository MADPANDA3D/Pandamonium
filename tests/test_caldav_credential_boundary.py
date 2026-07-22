"""CalDAV secrets stay server-only and cannot be repurposed through prefs."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.database import Integration


class _Request:
    def __init__(self, body=None):
        self._body = body or {}

    async def json(self):
        return self._body


@pytest.fixture
def credential_db(monkeypatch):
    import src.caldav_credentials as credentials
    import src.secret_storage as secret_storage

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Integration.__table__.create(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(credentials, "SessionLocal", factory)
    monkeypatch.setattr(
        secret_storage,
        "encrypt",
        lambda value: value if value.startswith("enc:") else f"enc:{value}",
    )
    monkeypatch.setattr(
        secret_storage,
        "decrypt",
        lambda value: value.removeprefix("enc:"),
    )
    return factory


def _backup_router(monkeypatch):
    import routes.backup_routes as backup_routes

    monkeypatch.setattr(backup_routes, "require_admin", lambda request: None)
    monkeypatch.setattr(backup_routes, "get_current_user", lambda request: "alice")
    monkeypatch.setattr(backup_routes, "load_settings", lambda: {})
    monkeypatch.setattr(backup_routes, "load_features", lambda: {})
    managers = (
        SimpleNamespace(load=lambda owner: [], load_all=lambda: [], save=lambda rows: None),
        SimpleNamespace(get_all=lambda: {}, save=lambda rows: None),
        SimpleNamespace(load=lambda owner: [], load_all=lambda: [], add_skill=lambda **kwargs: {}),
    )
    return backup_routes.setup_backup_routes(*managers)


def test_prefs_and_backup_migrate_without_exposing_caldav_ciphertext(
    tmp_path, monkeypatch, credential_db
):
    import routes.prefs_routes as prefs_routes
    import src.caldav_credentials as credentials

    prefs_file = tmp_path / "user_prefs.json"
    prefs_file.write_text(
        json.dumps({
            "_users": {
                "alice": {
                    "theme": "dark",
                    "caldav_accounts": [
                        {
                            "id": "google-1",
                            "provider": "google",
                            "url": "https://apidata.googleusercontent.com/caldav/v2/alice%40example.com/events",
                            "username": "alice@example.com",
                            "oauth_access_token": "enc:access-cipher",
                            "oauth_refresh_token": "enc:refresh-cipher",
                        },
                        {
                            "id": "dav-1",
                            "url": "https://calendar.example.com/dav",
                            "username": "alice",
                            "password": "enc:password-cipher",
                        },
                    ],
                }
            }
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(prefs_routes, "PREFS_FILE", str(prefs_file))
    monkeypatch.setattr(prefs_routes, "get_current_user", lambda request: "alice")

    prefs_router = prefs_routes.setup_prefs_routes()
    get_all = next(route.endpoint for route in prefs_router.routes if route.path == "/api/prefs")
    public = asyncio.run(get_all(_Request()))
    public_json = json.dumps(public)
    assert "access-cipher" not in public_json
    assert "refresh-cipher" not in public_json
    assert "password-cipher" not in public_json

    raw = json.loads(prefs_file.read_text(encoding="utf-8"))["_users"]["alice"]
    for account in raw["caldav_accounts"]:
        assert "password" not in account
        assert "oauth_access_token" not in account
        assert "oauth_refresh_token" not in account

    db = credential_db()
    try:
        rows = {
            row.name: row.config
            for row in db.query(Integration).filter(
                Integration.owner == "alice",
                Integration.type == credentials.INTEGRATION_TYPE,
            )
        }
    finally:
        db.close()
    assert rows["google-1"] == {
        credentials.GOOGLE_ACCESS_TOKEN: "enc:access-cipher",
        credentials.GOOGLE_REFRESH_TOKEN: "enc:refresh-cipher",
    }
    assert rows["dav-1"] == {
        credentials.BASIC_PASSWORD: "enc:password-cipher",
    }

    export = next(
        route.endpoint
        for route in _backup_router(monkeypatch).routes
        if route.path == "/api/export"
    )
    exported = asyncio.run(export(_Request())).body.decode("utf-8")
    assert "access-cipher" not in exported
    assert "refresh-cipher" not in exported
    assert "password-cipher" not in exported


def test_auth_disabled_prefs_migration_uses_calendar_fallback_owner(
    tmp_path, monkeypatch, credential_db
):
    import routes.prefs_routes as prefs_routes
    import src.caldav_credentials as credentials
    import src.caldav_sync as caldav_sync

    prefs_file = tmp_path / "user_prefs.json"
    prefs_file.write_text(
        json.dumps(
            {
                "_users": {
                    "previous-user": {
                        "caldav_accounts": [
                            {
                                "id": "dav-auth-disabled",
                                "provider": "caldav",
                                "url": "https://calendar.example.test/dav",
                                "username": "leo",
                                "password": "enc:legacy-password",
                            }
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(prefs_routes, "PREFS_FILE", str(prefs_file))
    monkeypatch.setenv("AUTH_ENABLED", "false")

    public = prefs_routes._public_for_user(None)

    assert "password" not in public["caldav_accounts"][0]
    assert credentials.get_secret(
        prefs_routes.FALLBACK_OWNER,
        "dav-auth-disabled",
        credentials.BASIC_PASSWORD,
    ) == "legacy-password"
    assert credentials.get_secret(
        None,
        "dav-auth-disabled",
        credentials.BASIC_PASSWORD,
    ) == ""
    stored = json.loads(prefs_file.read_text(encoding="utf-8"))
    assert "password" not in stored["_users"]["previous-user"]["caldav_accounts"][0]
    accounts = caldav_sync._load_caldav_accounts(prefs_routes.FALLBACK_OWNER)
    assert [account["id"] for account in accounts] == ["dav-auth-disabled"]


def test_prefs_write_and_backup_import_cannot_transplant_refresh_ciphertext(
    tmp_path, monkeypatch, credential_db
):
    import routes.prefs_routes as prefs_routes
    import src.caldav_credentials as credentials
    import src.caldav_sync as caldav_sync

    original_account = {
        "id": "google-1",
        "provider": "google",
        "url": "https://apidata.googleusercontent.com/caldav/v2/alice%40example.com/events",
        "username": "alice@example.com",
    }
    prefs_file = tmp_path / "user_prefs.json"
    prefs_file.write_text(
        json.dumps({"_users": {"alice": {"caldav_accounts": [original_account]}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(prefs_routes, "PREFS_FILE", str(prefs_file))
    monkeypatch.setattr(prefs_routes, "get_current_user", lambda request: "alice")
    credentials.set_credentials(
        "alice", "google-1", google_refresh_token="refresh-secret"
    )

    transplanted = {
        "id": "google-1",
        "provider": "caldav",
        "url": "https://attacker.example/collect",
        "username": "victim",
        "password": "enc:refresh-secret",
    }
    prefs_router = prefs_routes.setup_prefs_routes()
    set_pref = next(
        route.endpoint
        for route in prefs_router.routes
        if route.path == "/api/prefs/{key}" and "PUT" in route.methods
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(set_pref(_Request(), "caldav_accounts", {"value": [transplanted]}))
    assert exc.value.status_code == 403

    import_endpoint = next(
        route.endpoint
        for route in _backup_router(monkeypatch).routes
        if route.path == "/api/import"
    )
    result = asyncio.run(import_endpoint(_Request({
        "preferences": {
            "theme": "paper",
            "caldav_accounts": [transplanted],
        }
    })))
    assert result["ok"] is True
    stored_prefs = json.loads(prefs_file.read_text(encoding="utf-8"))["_users"]["alice"]
    assert stored_prefs["theme"] == "paper"
    assert stored_prefs["caldav_accounts"] == [original_account]

    # Even a directly forged metadata object cannot make the resolver decrypt
    # the purpose-bound Google refresh token as a generic Basic password.
    assert caldav_sync._resolve_caldav_auth("alice", transplanted) == (
        "victim",
        "",
        None,
    )
    assert credentials.get_secret(
        "alice", "google-1", credentials.GOOGLE_REFRESH_TOKEN
    ) == "refresh-secret"
    assert credentials.get_secret(
        "alice", "google-1", credentials.BASIC_PASSWORD
    ) == ""


def test_saved_basic_password_is_not_reused_for_a_different_destination(
    monkeypatch, credential_db
):
    from routes.calendar_routes import setup_calendar_routes
    import src.caldav_credentials as credentials
    import src.caldav_sync as caldav_sync

    account = {
        "id": "dav-1",
        "url": "https://calendar.example.com/dav",
        "username": "alice",
    }
    credentials.set_credentials("alice", "dav-1", basic_password="app-password")
    monkeypatch.setattr(caldav_sync, "_load_caldav_accounts", lambda owner: [account])
    endpoint = next(
        route.endpoint
        for route in setup_calendar_routes().routes
        if route.path == "/api/calendar/test"
    )
    monkeypatch.setitem(endpoint.__globals__, "_require_user", lambda request: "alice")

    result = asyncio.run(endpoint(_Request({
        "account_id": "dav-1",
        "url": "https://attacker.example/collect",
        "username": "alice",
    })))
    assert result == {
        "ok": False,
        "error": "Password is required to test a different CalDAV server or username",
    }


@pytest.mark.parametrize(
    ("path", "method", "kwargs"),
    [
        ("/api/calendar/config", "POST", {}),
        ("/api/calendar/config/accounts", "POST", {}),
        ("/api/calendar/config/accounts/{account_id}", "PUT", {"account_id": "dav-1"}),
    ],
)
def test_calendar_password_routes_reject_client_ciphertext(
    monkeypatch, credential_db, path, method, kwargs
):
    from routes.calendar_routes import setup_calendar_routes

    endpoint = next(
        route.endpoint
        for route in setup_calendar_routes().routes
        if route.path == path and method in route.methods
    )
    monkeypatch.setitem(endpoint.__globals__, "_require_user", lambda request: "alice")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(endpoint(request=_Request({"password": "enc:stolen-cipher"}), **kwargs))
    assert exc.value.status_code == 400
    assert "Encrypted credential" in exc.value.detail
