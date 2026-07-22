"""Google Calendar OAuth stays owner-scoped, token-free, and bearer-backed."""

import asyncio
import copy
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest


@pytest.fixture(autouse=True)
def credential_store(monkeypatch):
    """Keep these route-contract tests independent of SQLite thread pooling."""
    import src.caldav_credentials as credentials

    store = {}

    def set_credentials(owner, account_id, **updates):
        for purpose, value in updates.items():
            key = (owner, account_id, purpose)
            if value:
                store[key] = str(value).removeprefix("enc:")
            else:
                store.pop(key, None)

    def get_secret(owner, account_id, purpose):
        return store.get((owner, account_id, purpose), "")

    def retain_accounts(owner, account_ids):
        for key in list(store):
            if key[0] == owner and key[1] not in account_ids:
                store.pop(key)

    monkeypatch.setattr(credentials, "set_credentials", set_credentials)
    monkeypatch.setattr(credentials, "get_secret", get_secret)
    monkeypatch.setattr(
        credentials,
        "has_secret",
        lambda owner, account_id, purpose: bool(get_secret(owner, account_id, purpose)),
    )
    monkeypatch.setattr(credentials, "retain_accounts", retain_accounts)
    return store


def _endpoint(path):
    from routes.calendar_routes import setup_calendar_routes

    router = setup_calendar_routes()
    return next(route.endpoint for route in router.routes if route.path == path)


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_google_calendar_authorize_uses_event_scope_and_signed_domain_state(monkeypatch):
    import routes.email_helpers as email_helpers

    monkeypatch.setattr(email_helpers, "make_oauth_state", lambda account, owner: f"signed:{account}:{owner}")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "client-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv(
        "GOOGLE_CALENDAR_OAUTH_REDIRECT_URI",
        "https://odysseus.example/api/calendar/oauth/google/callback",
    )

    endpoint = _endpoint("/api/calendar/oauth/google/authorize")
    monkeypatch.setitem(endpoint.__globals__, "_require_user", lambda request: "alice")
    response = asyncio.run(
        endpoint(
            request=SimpleNamespace(base_url="https://ignored.example/"), account_id=""
        )
    )
    query = parse_qs(urlparse(response.headers["location"]).query)

    assert query["scope"] == [
        "openid email https://www.googleapis.com/auth/calendar.events"
    ]
    assert query["state"] == ["signed:calendar:new:alice"]
    assert query["access_type"] == ["offline"]


def test_google_calendar_callback_encrypts_tokens_and_list_never_returns_them(
    monkeypatch, credential_store
):
    import httpx
    import routes.email_helpers as email_helpers
    import routes.prefs_routes as prefs_routes
    import src.secret_storage as secret_storage

    saved = {"caldav_accounts": []}
    state_checks = []

    def verify_state(state, *, consume=False):
        state_checks.append(consume)
        return {"a": "calendar:new", "o": "alice", "n": "nonce"}

    monkeypatch.setattr(email_helpers, "verify_oauth_state", verify_state)
    monkeypatch.setattr(prefs_routes, "_load_for_user", lambda owner: dict(saved))
    monkeypatch.setattr(prefs_routes, "_save_for_user", lambda owner, prefs: saved.update(prefs))
    monkeypatch.setattr(secret_storage, "encrypt", lambda value: f"enc:{value}")
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *args, **kwargs: _Response({
            "access_token": "access-raw",
            "refresh_token": "refresh-raw",
            "expires_in": 3600,
        }),
    )
    monkeypatch.setattr(
        httpx,
        "get",
        lambda *args, **kwargs: _Response({"email": "leo@example.com"}),
    )
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "client-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv(
        "GOOGLE_CALENDAR_OAUTH_REDIRECT_URI",
        "https://odysseus.example/api/calendar/oauth/google/callback",
    )
    request = SimpleNamespace(base_url="https://ignored.example/")

    callback = _endpoint("/api/calendar/oauth/google/callback")
    monkeypatch.setitem(callback.__globals__, "_require_user", lambda request: "alice")
    response = asyncio.run(
        callback(
            request=request, code="code", state="state", error=None
        )
    )
    assert response.headers["location"].endswith("calendar_oauth_success=1")
    account = saved["caldav_accounts"][0]
    assert account["url"] == (
        "https://apidata.googleusercontent.com/caldav/v2/leo%40example.com/events"
    )
    assert "oauth_access_token" not in account
    assert "oauth_refresh_token" not in account
    assert credential_store[("alice", account["id"], "google_access_token")] == "access-raw"
    assert credential_store[("alice", account["id"], "google_refresh_token")] == "refresh-raw"
    assert state_checks == [False, True]

    list_accounts = _endpoint("/api/calendar/config/accounts")
    monkeypatch.setitem(list_accounts.__globals__, "_require_user", lambda request: "alice")
    listed = asyncio.run(list_accounts(request=request))["accounts"][0]
    assert listed["provider"] == "google"
    assert not any("token" in key for key in listed)


def test_google_calendar_callback_preflights_target_before_consuming_or_exchanging(monkeypatch):
    import httpx
    import routes.email_helpers as email_helpers
    import routes.prefs_routes as prefs_routes

    state_checks = []

    def verify_state(state, *, consume=False):
        state_checks.append(consume)
        return {"a": "calendar:not-owned", "o": "alice", "n": "nonce"}

    def unexpected_token_exchange(*args, **kwargs):
        raise AssertionError("token exchange ran before target preflight")

    monkeypatch.setattr(email_helpers, "verify_oauth_state", verify_state)
    monkeypatch.setattr(
        prefs_routes, "_load_for_user", lambda owner: {"caldav_accounts": []}
    )
    monkeypatch.setattr(httpx, "post", unexpected_token_exchange)
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "client-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "client-secret")

    callback = _endpoint("/api/calendar/oauth/google/callback")
    monkeypatch.setitem(callback.__globals__, "_require_user", lambda request: "alice")
    response = asyncio.run(
        callback(
            request=SimpleNamespace(base_url="https://odysseus.example/"),
            code="code",
            state="state",
            error=None,
        )
    )

    assert response.headers["location"].endswith("calendar_oauth_error=account_not_found")
    assert state_checks == [False]


@pytest.mark.parametrize(
    ("returned_email", "expect_success"),
    [("new@example.com", False), ("old@example.com", True)],
)
def test_google_calendar_reconnect_without_refresh_token_stays_identity_bound(
    monkeypatch, returned_email, expect_success, credential_store
):
    import httpx
    import routes.email_helpers as email_helpers
    import routes.prefs_routes as prefs_routes
    import src.secret_storage as secret_storage

    account = {
        "id": "google-1",
        "provider": "google",
        "url": "https://apidata.googleusercontent.com/caldav/v2/old%40example.com/events",
        "username": "old@example.com",
        "oauth_access_token": "enc:old-access",
        "oauth_refresh_token": "enc:old-refresh",
        "oauth_token_expiry": "0",
    }
    saved = {"caldav_accounts": [account]}
    save_calls = []

    def save_prefs(owner, prefs):
        save_calls.append(copy.deepcopy(prefs))
        saved.update(prefs)

    monkeypatch.setattr(
        email_helpers,
        "verify_oauth_state",
        lambda state, *, consume=False: {
            "a": "calendar:google-1", "o": "alice", "n": "nonce"
        },
    )
    monkeypatch.setattr(prefs_routes, "_load_for_user", lambda owner: dict(saved))
    monkeypatch.setattr(prefs_routes, "_save_for_user", save_prefs)
    monkeypatch.setattr(secret_storage, "encrypt", lambda value: f"enc:{value}")
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *args, **kwargs: _Response({
            "access_token": "new-access", "expires_in": 3600
        }),
    )
    monkeypatch.setattr(
        httpx, "get", lambda *args, **kwargs: _Response({"email": returned_email})
    )
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "client-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "client-secret")

    callback = _endpoint("/api/calendar/oauth/google/callback")
    monkeypatch.setitem(callback.__globals__, "_require_user", lambda request: "alice")
    response = asyncio.run(
        callback(
            request=SimpleNamespace(base_url="https://odysseus.example/"),
            code="code",
            state="state",
            error=None,
        )
    )

    if expect_success:
        assert response.headers["location"].endswith("calendar_oauth_success=1")
        assert credential_store[("alice", "google-1", "google_access_token")] == "new-access"
        assert credential_store[("alice", "google-1", "google_refresh_token")] == "old-refresh"
        assert len(save_calls) == 2
    else:
        assert response.headers["location"].endswith(
            "calendar_oauth_error=missing_refresh_token"
        )
        assert credential_store[("alice", "google-1", "google_access_token")] == "old-access"
        assert credential_store[("alice", "google-1", "google_refresh_token")] == "old-refresh"
        assert len(save_calls) == 1
    assert "oauth_access_token" not in saved["caldav_accounts"][0]
    assert "oauth_refresh_token" not in saved["caldav_accounts"][0]


def test_expired_google_token_refreshes_owner_account_and_returns_bearer(
    monkeypatch, credential_store
):
    import httpx
    import routes.prefs_routes as prefs_routes
    import src.caldav_sync as caldav_sync
    import src.secret_storage as secret_storage

    account = {
        "id": "google-1",
        "provider": "google",
        "url": "https://apidata.googleusercontent.com/caldav/v2/leo%40example.com/events",
        "username": "leo@example.com",
        "oauth_access_token": "enc:expired",
        "oauth_refresh_token": "enc:refresh",
        "oauth_token_expiry": "0",
    }
    saved = {"caldav_accounts": [account]}
    monkeypatch.setattr(prefs_routes, "_load_for_user", lambda owner: saved)
    monkeypatch.setattr(prefs_routes, "_save_for_user", lambda owner, prefs: saved.update(prefs))
    monkeypatch.setattr(secret_storage, "decrypt", lambda value: value.removeprefix("enc:"))
    monkeypatch.setattr(secret_storage, "encrypt", lambda value: f"enc:{value}")
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *args, **kwargs: _Response({"access_token": "fresh", "expires_in": 3600}),
    )
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "client-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "client-secret")
    credential_store[("alice", "google-1", "google_access_token")] = "expired"
    credential_store[("alice", "google-1", "google_refresh_token")] = "refresh"

    username, token, auth_type = caldav_sync._resolve_caldav_auth("alice", account)

    assert (username, token, auth_type) == ("leo@example.com", "fresh", "bearer")
    assert credential_store[("alice", "google-1", "google_access_token")] == "fresh"
    assert "oauth_access_token" not in saved["caldav_accounts"][0]


@pytest.mark.parametrize(
    "url",
    [
        "https://attacker.example/caldav/v2/leo%40example.com/events",
        "http://apidata.googleusercontent.com/caldav/v2/leo%40example.com/events",
        "https://apidata.googleusercontent.com/caldav/v2/attacker%40example.com/events",
    ],
)
def test_google_bearer_token_requires_exact_https_account_collection(monkeypatch, url):
    import src.caldav_sync as caldav_sync
    import src.caldav_credentials as credentials

    account = {
        "id": "google-1",
        "provider": "google",
        "url": url,
        "username": "leo@example.com",
        "oauth_access_token": "enc:access",
        "oauth_refresh_token": "enc:refresh",
        "oauth_token_expiry": "4102444800",
    }

    def forbidden(*args, **kwargs):
        raise AssertionError("Google token was decrypted or refreshed for an invalid URL")

    monkeypatch.setattr(credentials, "get_secret", forbidden)
    monkeypatch.setattr(caldav_sync, "_refresh_google_calendar_token", forbidden)

    assert caldav_sync._resolve_caldav_auth("alice", account) == (
        "leo@example.com", "", "bearer"
    )


def test_generic_caldav_auth_remains_url_agnostic(monkeypatch, credential_store):
    import src.caldav_sync as caldav_sync
    import src.secret_storage as secret_storage

    monkeypatch.setattr(secret_storage, "decrypt", lambda value: value.removeprefix("enc:"))
    account = {
        "id": "generic-1",
        "provider": "caldav",
        "url": "https://calendar.example.com/custom/dav",
        "username": "leo",
        "password": "enc:app-password",
    }
    credential_store[("alice", "generic-1", "basic_password")] = "app-password"

    assert caldav_sync._resolve_caldav_auth("alice", account) == (
        "leo", "app-password", None
    )


def test_google_pull_and_writeback_both_pass_bearer_auth(monkeypatch):
    import routes.prefs_routes as prefs_routes
    import src.caldav_sync as caldav_sync
    import src.caldav_writeback as caldav_writeback
    import src.secret_storage as secret_storage

    account = {
        "id": "google-1",
        "provider": "google",
        "label": "Google Calendar",
        "url": "https://apidata.googleusercontent.com/caldav/v2/leo%40example.com/events",
        "username": "leo@example.com",
        "oauth_access_token": "enc:access",
        "oauth_refresh_token": "enc:refresh",
        "oauth_token_expiry": "4102444800",
        "read_only": False,
    }
    monkeypatch.setattr(
        prefs_routes, "_load_for_user", lambda owner: {"caldav_accounts": [account]}
    )
    monkeypatch.setattr(secret_storage, "decrypt", lambda value: value.removeprefix("enc:"))
    monkeypatch.setattr(caldav_sync, "validate_caldav_url", lambda url: url)
    monkeypatch.setattr(caldav_writeback, "_persist_writeback_result", lambda *args, **kwargs: None)
    calls = []

    def fake_sync(owner, url, username, token, account_id="", auth_type=None):
        calls.append(("pull", token, auth_type))
        return {"calendars": 1, "events": 1, "deleted": 0, "errors": []}

    def fake_writeback(calendar_id, event, delete, url, username, token,
                       owner="", account_id="", auth_type=None):
        calls.append(("write", token, auth_type))
        return {"ok": True}

    monkeypatch.setattr(caldav_sync, "_sync_blocking", fake_sync)
    monkeypatch.setattr(caldav_writeback, "_writeback_blocking", fake_writeback)

    pulled = asyncio.run(caldav_sync.sync_caldav("alice"))
    written = asyncio.run(
        caldav_writeback.writeback_event(
            "alice", "caldav", "google-calendar", {"uid": "event-1"}
        )
    )

    assert pulled["events"] == 1
    assert written == {"ok": True}
    assert calls == [("pull", "access", "bearer"), ("write", "access", "bearer")]


def test_settings_exposes_first_party_google_calendar_connection():
    source = Path("static/js/settings.js").read_text(encoding="utf-8")

    assert "['google_calendar', 'Google Calendar']" in source
    assert "/api/calendar/oauth/google/authorize" in source
    assert "open('integrations');" in source
    assert "window.settingsModule" not in source
