"""MAD-989: feedback relay submission (no GitHub credential)."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import src.constants as constants
import src.feedback_relay as relay
import src.feedback_store as store
import routes.feedback_routes as fb


class FakeRequest:
    def __init__(self, payload=None, owner="leo"):
        self._payload = payload
        self.state = SimpleNamespace(current_user=owner, api_token=False)

    async def json(self):
        return self._payload


def _draft_payload(**overrides):
    payload = {
        "draft_id": "draft-abc12345",
        "type": "bug",
        "summary": "Send button does nothing",
        "goal": "Send a message",
        "expected": "Message sends",
        "actual": "Nothing happens",
        "steps": [],
        "workaround": "Reload",
        "reviewed_text": "",
        "route": "/static/index.html#chat",
        "client": {"user_agent_class": "Chrome 140", "viewport_class": "desktop", "locale": "en-US"},
        "include_diagnostics": True,
        "attachment_ids": [],
        "confirmation": True,
        "duplicate_decision": "new",
        "idempotency_key": "sub-0000000000000001",
    }
    payload.update(overrides)
    return payload


def _endpoint(router, path, method="GET"):
    for route in router.routes:
        if route.path == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"{method} {path} not registered")


class FakeTransport:
    def __init__(self):
        self.calls: list[dict] = []
        self.status = 200
        self.response: dict = {
            "ok": True,
            "issue_url": "https://github.com/MADPANDA3D/Pandamonium/issues/313",
            "issue_number": 313,
        }

    def __call__(self, url, *, headers, body, timeout):
        self.calls.append({"url": url, "headers": dict(headers), "body": json.loads(body.decode())})
        return self.status, json.dumps(self.response).encode()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(constants, "FEEDBACK_DIR", str(tmp_path / "feedback"))
    monkeypatch.setattr(
        fb, "require_authenticated_request", lambda request: request.state.current_user or ""
    )
    monkeypatch.setattr(fb, "_feedback_enabled", lambda: True)
    store.reset_store_for_tests()
    yield
    store.reset_store_for_tests()


@pytest.fixture
def router():
    return fb.setup_feedback_routes()


@pytest.fixture
def relay_env(monkeypatch):
    transport = FakeTransport()
    monkeypatch.setenv("PANDAMONIUM_FEEDBACK_RELAY_URL", "https://n8n.example.test/webhook/pandamonium-feedback")
    monkeypatch.setenv("PANDAMONIUM_FEEDBACK_RELAY_TOKEN", "pf1_test_token")
    monkeypatch.setattr(relay, "_default_transport", transport)
    return transport


def test_config_prefers_relay(env, router, relay_env):
    result = _endpoint(router, "/api/feedback/config")(FakeRequest())
    assert result["configured"] is True
    assert result["public_submission"] is True
    assert result["mode"] == "relay"


def test_submit_uses_relay_and_returns_issue(env, router, relay_env):
    result = asyncio.run(
        _endpoint(router, "/api/feedback/submit", "POST")(FakeRequest(payload=_draft_payload()))
    )
    assert result["ok"] is True
    assert result["issue_url"].endswith("/issues/313")
    assert result["issue_number"] == 313

    assert len(relay_env.calls) == 1
    call = relay_env.calls[0]
    assert call["headers"]["X-Pandamonium-Relay-Token"] == "pf1_test_token"
    assert call["headers"]["X-Pandamonium-Schema"] == "pandamonium-feedback/v1"
    assert call["body"]["schema"] == "pandamonium-feedback/v1"
    assert call["body"]["idempotency_key"]
    assert call["body"]["title"].startswith("[Bug]")
    assert "### Summary" in call["body"]["body_markdown"]
    assert call["body"]["install"]["version"]
    diagnostics = call["body"]["diagnostics"]
    assert set(diagnostics) <= {
        "version", "revision", "installation_method", "runtime", "platform_class",
        "platform_release", "route", "browser_class", "viewport_class", "locale",
        "session_id", "request_id", "last_error", "health",
    }
    assert diagnostics["version"]
    assert diagnostics["route"]


def test_prepare_and_submit_use_the_same_body_when_relay_and_github_are_configured(
    env, router, relay_env, monkeypatch
):
    github_config = SimpleNamespace(
        attachment_url_base="https://pandamonium.example.test/api/feedback/attachment",
        security_url="https://github.com/MADPANDA3D/Pandamonium/security/advisories/new",
    )
    monkeypatch.setattr(fb.github, "resolve_config", lambda: github_config)
    attachment = store.save_attachment(
        draft_id="draft-abc12345",
        owner="leo",
        data=b"\x89PNG\r\n\x1a\n" + b"x" * 64,
        content_type="image/png",
        suffix=".png",
        name="player.png",
        label="wrong audio",
        route="/static/index.html#chat",
    )
    payload = _draft_payload(
        idempotency_key="sub-0000000000000008",
        attachment_ids=[attachment["id"]],
    )

    prepared = asyncio.run(
        _endpoint(router, "/api/feedback/prepare", "POST")(
            FakeRequest(payload=payload)
        )
    )
    submitted = asyncio.run(
        _endpoint(router, "/api/feedback/submit", "POST")(
            FakeRequest(payload=payload)
        )
    )

    assert relay_env.calls[0]["body"]["body_markdown"] == prepared["body"]
    assert submitted["redacted_report"]["body"] == prepared["body"]
    assert "pandamonium.example.test" not in prepared["body"]


def test_submit_offloads_relay_transport(env, router, relay_env, monkeypatch):
    calls = []

    async def inline_to_thread(func, *args, **kwargs):
        calls.append(func.__name__)
        return func(*args, **kwargs)

    monkeypatch.setattr(fb.asyncio, "to_thread", inline_to_thread)
    result = asyncio.run(
        _endpoint(router, "/api/feedback/submit", "POST")(
            FakeRequest(payload=_draft_payload(idempotency_key="sub-0000000000000007"))
        )
    )

    assert result["ok"] is True
    assert calls == ["submit"]


def test_relay_retryable_failure_keeps_draft(env, router, relay_env):
    relay_env.status = 500
    relay_env.response = {}
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            _endpoint(router, "/api/feedback/submit", "POST")(
                FakeRequest(payload=_draft_payload(idempotency_key="sub-0000000000000002"))
            )
        )
    assert excinfo.value.status_code == 502


def test_relay_success_requires_confirmed_issue_identity(env, router, relay_env):
    relay_env.response = {"ok": True}
    payload = _draft_payload(idempotency_key="sub-0000000000000005")
    endpoint = _endpoint(router, "/api/feedback/submit", "POST")

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(endpoint(FakeRequest(payload=payload)))
    assert excinfo.value.status_code == 502
    assert store.get_submission(payload["idempotency_key"])["status"] == "failed"

    relay_env.response = {
        "ok": True,
        "issue_url": "https://github.com/MADPANDA3D/Pandamonium/issues/313",
        "issue_number": 313,
    }
    assert asyncio.run(endpoint(FakeRequest(payload=payload)))["issue_number"] == 313


def test_existing_duplicate_uses_github_when_relay_is_configured(
    env, router, relay_env, monkeypatch
):
    class GitHubClient:
        config = SimpleNamespace(attachment_url_base="")

        def __init__(self):
            self.comments = []

        def search_open_issues(self, _terms):
            return [{"number": 7, "title": "Existing", "url": "https://example.test/7"}]

        def add_issue_comment(self, *, issue_number, body):
            self.comments.append((issue_number, body))
            return {
                "issue_number": issue_number,
                "issue_url": f"https://github.com/MADPANDA3D/Pandamonium/issues/{issue_number}",
            }

    github_client = GitHubClient()
    github_config = SimpleNamespace(
        attachment_url_base="", security_url="https://example.test/security"
    )
    monkeypatch.setattr(fb.github, "resolve_config", lambda: github_config)
    monkeypatch.setattr(fb.github, "client_for_submission", lambda: github_client)
    payload = _draft_payload(
        duplicate_decision="existing",
        existing_issue_number=7,
        idempotency_key="sub-0000000000000006",
    )

    result = asyncio.run(
        _endpoint(router, "/api/feedback/submit", "POST")(FakeRequest(payload=payload))
    )
    assert result["attached_to_existing"] is True
    assert github_client.comments[0][0] == 7
    assert relay_env.calls == []


def test_relay_rejection_is_not_retryable(env, router, relay_env):
    relay_env.status = 400
    relay_env.response = {"ok": False, "message": "Rejected as spam."}
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            _endpoint(router, "/api/feedback/submit", "POST")(
                FakeRequest(payload=_draft_payload(idempotency_key="sub-0000000000000004"))
            )
        )
    assert excinfo.value.status_code == 400


def test_security_reports_never_use_relay(env, router, relay_env):
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            _endpoint(router, "/api/feedback/submit", "POST")(
                FakeRequest(payload=_draft_payload(type="security", idempotency_key="sub-0000000000000003"))
            )
        )
    assert excinfo.value.status_code == 409
    assert relay_env.calls == []
