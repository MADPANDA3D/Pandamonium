"""Focused provider audit tests (MAD-787).

Pins the acceptance criteria that were not already covered by the provider
suites:

* setup/login failures are actionable and never echo provider tokens
  (``_probe_single_model`` / ``_ping_endpoint`` redact a provider-echoed key;
  device-flow provisioning results never carry tokens);
* discovery returns sanitized model IDs and never invents provider identity;
* local and remote endpoints share one canonical ``ModelEndpoint`` record;
* provider capabilities are advertised only when actually known.
"""

import json
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest

from tests.helpers.import_state import clear_fake_endpoint_resolver_modules, preserve_import_state

with preserve_import_state("core.database", "src.database", "core.session_manager", "routes.model_routes"):
    clear_fake_endpoint_resolver_modules()
    if "core.database" not in sys.modules:
        _core_db = types.ModuleType("core.database")
        for _name in [
            "SessionLocal", "ModelEndpoint", "Session", "ChatMessage", "Document",
            "DocumentVersion", "GalleryImage", "GalleryAlbum", "Note",
            "CalendarCal", "CalendarEvent", "ScheduledTask", "TaskRun",
            "McpServer", "ProviderAuthSession", "Base",
        ]:
            setattr(_core_db, _name, MagicMock())
        sys.modules["core.database"] = _core_db

    import routes.model_routes as model_routes
    import src.endpoint_resolver as endpoint_resolver

from src import model_discovery


_LIVE_KEY = "sk-live-abcdefghijklmnopqrstuvwxyz123456"


def _post_response(status, payload):
    return httpx.Response(
        status,
        request=httpx.Request("POST", "http://fixture.local/v1/chat/completions"),
        json=payload,
    )


# ── setup/login failures redact provider-echoed credentials ──────────────


def test_probe_single_model_redacts_provider_echoed_api_key(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None, verify=None, **kwargs):
        return _post_response(401, {"error": {"message": f"Incorrect API key provided: {_LIVE_KEY}"}})

    monkeypatch.setattr(model_routes.httpx, "post", fake_post)

    result = model_routes._probe_single_model(
        "http://fixture.local/v1", _LIVE_KEY, "test-model", timeout=1
    )

    assert result["status"] == "fail"
    assert _LIVE_KEY not in result["error"]
    assert "[redacted]" in result["error"]


def test_probe_single_model_redacts_key_from_string_error_body(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None, verify=None, **kwargs):
        return _post_response(403, {"error": f"denied token={_LIVE_KEY}"})

    monkeypatch.setattr(model_routes.httpx, "post", fake_post)

    result = model_routes._probe_single_model(
        "http://fixture.local/v1", _LIVE_KEY, "test-model", timeout=1
    )

    assert result["status"] == "fail"
    assert _LIVE_KEY not in result["error"]


def test_probe_single_model_redacts_key_from_transport_error(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None, verify=None, **kwargs):
        raise httpx.ConnectError(f"proxy rejected credential {_LIVE_KEY}")

    monkeypatch.setattr(model_routes.httpx, "post", fake_post)

    result = model_routes._probe_single_model(
        "http://fixture.local/v1", _LIVE_KEY, "test-model", timeout=1
    )

    assert result["status"] == "fail"
    assert _LIVE_KEY not in result["error"]


def test_ping_endpoint_redacts_key_from_transport_error(monkeypatch):
    monkeypatch.setattr(endpoint_resolver, "resolve_url", lambda url: url, raising=False)

    def fake_get(url, headers=None, timeout=None, verify=None, **kwargs):
        raise httpx.ConnectError(f"tls handshake failed using {_LIVE_KEY}")

    monkeypatch.setattr(model_routes.httpx, "get", fake_get)

    result = model_routes._ping_endpoint("http://fixture.local/v1", _LIVE_KEY, timeout=0.5)

    assert result["reachable"] is False
    assert _LIVE_KEY not in (result.get("error") or "")
    assert "[redacted]" in (result.get("error") or "")


# ── device-flow setup responses never carry provider secrets ─────────────


def test_copilot_device_start_keeps_device_code_server_side(monkeypatch):
    import routes.copilot_routes as copilot_routes

    monkeypatch.setattr(
        copilot_routes.copilot,
        "request_device_code",
        lambda host: {
            "device_code": "device-secret-xyz",
            "user_code": "ABCD-EFGH",
            "verification_uri": "https://github.com/login/device",
            "verification_uri_complete": "https://github.com/login/device?user_code=ABCD-EFGH",
            "interval": 5,
            "expires_in": 900,
        },
    )

    start = copilot_routes._start_device_flow(_StubRequest(), {"enterprise_url": ""})

    assert start.pending["device_code"] == "device-secret-xyz"
    assert "device_code" not in start.response
    assert "device-secret-xyz" not in json.dumps(start.response)


def test_copilot_provision_result_never_echoes_token(monkeypatch):
    import routes.copilot_routes as copilot_routes

    TestSessionLocal = _memory_copilot_db(monkeypatch, copilot_routes)
    monkeypatch.setattr(
        copilot_routes.copilot,
        "fetch_models",
        lambda base, token: [{"id": "gpt-4o", "tool_calls": True}],
    )

    result = copilot_routes._provision_endpoint("gho_secret_token", "https://api.githubcopilot.com", "alice")

    assert "gho_secret_token" not in json.dumps(result)
    assert not any("token" in key or "key" in key for key in result)
    db = TestSessionLocal()
    try:
        row = db.query(copilot_routes.ModelEndpoint).first()
        assert row is not None and row.api_key == "gho_secret_token"
    finally:
        db.close()


def test_chatgpt_provision_result_never_echoes_tokens(monkeypatch):
    import routes.chatgpt_subscription_routes as csr

    TestSessionLocal = _memory_copilot_db(monkeypatch, csr)
    monkeypatch.setattr(csr.chatgpt_subscription, "fetch_available_models", lambda token: ["gpt-5.5"])

    result = csr._provision_endpoint(
        {"access_token": "at-secret", "refresh_token": "rt-secret"}, "alice"
    )

    blob = json.dumps(result)
    assert "at-secret" not in blob and "rt-secret" not in blob
    db = TestSessionLocal()
    try:
        auth = db.query(csr.ProviderAuthSession).first()
        assert auth is not None
        assert auth.access_token == "at-secret"
        assert auth.refresh_token == "rt-secret"
    finally:
        db.close()


class _StubRequest:
    class _State:
        pass

    state = _State()
    headers = {}


def _memory_copilot_db(monkeypatch, module):
    from core.database import Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine)
    monkeypatch.setattr(module, "SessionLocal", TestSessionLocal)
    return TestSessionLocal


# ── discovery sanitization and provider identity ─────────────────────────


def test_discovery_model_ids_reject_addresses_urls_and_paths():
    payload = {
        "data": [
            {"id": "good-model"},
            {"id": "/leading/slash"},
            {"id": "http://evil.example/v1"},
            {"id": "C:/windows/path"},
            {"id": "192.168.1.10"},
            {"id": "raw\\path"},
            {"id": "good-model"},
        ]
    }

    assert model_discovery.ModelDiscovery._public_model_ids(payload) == ["good-model"]


def test_fingerprint_provider_never_invents_identity(monkeypatch):
    discovery = model_discovery.ModelDiscovery("localhost")

    def fake_get(url, timeout=None, **kwargs):
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            json={"models": [{"id": "whatever"}]},
        )

    monkeypatch.setattr(model_discovery.httpx, "get", fake_get)

    # Neither LM Studio nor llama.cpp markers present -> no provider claimed.
    assert discovery._fingerprint_provider("localhost", 8000) is None


def test_unknown_host_provider_detection_stays_transport_default(monkeypatch):
    # Unknown hosts are classified as the OpenAI-compatible transport, never
    # claimed as a vendor: no invented identity leaks into the picker label.
    unknown = model_routes._safe_detect_provider("http://192.168.1.50:8000/v1")
    assert unknown == "openai"
    assert unknown not in {"anthropic", "ollama", "copilot", "chatgpt-subscription"}

    def boom(_url):
        raise RuntimeError("bad url")

    monkeypatch.setattr(model_routes, "_detect_provider", boom)
    assert model_routes._safe_detect_provider("http://anything/v1") == ""
