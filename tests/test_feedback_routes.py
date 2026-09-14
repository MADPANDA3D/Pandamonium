"""MAD-856: feedback route contract tests with a stubbed GitHub client."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import src.constants as constants
import src.feedback_store as store
import src.github_issues as gh
import routes.feedback_routes as fb

PRIVATE_KEY = "-----BEGIN PRIVATE KEY-----\nFAKE-NOT-A-REAL-KEY\n-----END PRIVATE KEY-----\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 64


class FakeRequest:
    def __init__(self, payload=None, owner="leo"):
        self._payload = payload
        self.state = SimpleNamespace(current_user=owner, api_token=False)

    async def json(self):
        return self._payload


class FakeUpload:
    def __init__(self, data: bytes, filename: str = "shot.png"):
        self._data = data
        self.filename = filename

    async def read(self, size: int = -1):
        return self._data[:size] if size >= 0 else self._data


class FakeClient:
    def __init__(self, *, duplicates=None, create_error=None):
        self.duplicates = duplicates or []
        self.create_error = create_error
        self.calls: list[tuple] = []
        self.config = _fake_config()

    def search_open_issues(self, terms, *, limit=5):
        self.calls.append(("search", terms))
        return list(self.duplicates)

    def create_issue(self, *, title, body):
        self.calls.append(("create", title, body))
        if self.create_error is not None:
            error, self.create_error = self.create_error, None
            raise error
        return {"number": 42, "url": "https://github.com/MADPANDA3D/Pandamonium/issues/42"}

    def add_issue_comment(self, *, issue_number, body):
        self.calls.append(("comment", issue_number, body))
        return {
            "issue_number": int(issue_number),
            "issue_url": f"https://github.com/MADPANDA3D/Pandamonium/issues/{int(issue_number)}",
        }


def _fake_config():
    return gh.GitHubConfig(
        app_id="123456",
        installation_id="78910",
        private_key=PRIVATE_KEY,
        repo="MADPANDA3D/Pandamonium",
        security_url="https://github.com/MADPANDA3D/Pandamonium/security/advisories/new",
    )


def _draft_payload(**overrides):
    payload = {
        "draft_id": "draft-abc12345",
        "type": "bug",
        "summary": "Send button does nothing",
        "goal": "Send a message",
        "expected": "Message sends",
        "actual": "Nothing happens",
        "steps": ["Open chat", "Click send"],
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
def configured(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(gh, "resolve_config", _fake_config)
    monkeypatch.setattr(gh, "client_for_submission", lambda: client)
    return client


def _upload(router, **overrides):
    endpoint = _endpoint(router, "/api/feedback/upload", "POST")
    payload = {"draft_id": "draft-abc12345", "label": "", "route": "/static/index.html"}
    payload.update(overrides)
    return asyncio.run(
        endpoint(
            FakeRequest(),
            draft_id=payload["draft_id"],
            label=payload["label"],
            route=payload["route"],
            file=payload["file"],
        )
    )


# ── config ───────────────────────────────────────────────────────────────


def test_config_unconfigured_is_honest(env, router, monkeypatch):
    monkeypatch.setattr(gh, "resolve_config", lambda: None)
    for name in ("PANDAMONIUM_GITHUB_APP_ID", gh._PRIVATE_KEY_ENV):
        monkeypatch.delenv(name, raising=False)
    endpoint = _endpoint(router, "/api/feedback/config")
    result = endpoint(FakeRequest())
    assert result["enabled"] is True
    assert result["configured"] is False
    assert result["public_submission"] is False
    assert "PANDAMONIUM_GITHUB_APP_ID" in result["github_reason"]
    assert result["limits"]["max_attachments"] > 0


def test_config_never_leaks_key_material(env, router, monkeypatch):
    monkeypatch.setattr(gh, "resolve_config", _fake_config)
    endpoint = _endpoint(router, "/api/feedback/config")
    encoded = json.dumps(endpoint(FakeRequest()))
    assert "PRIVATE KEY" not in encoded
    assert "private_key" not in encoded
    assert "FAKE-NOT-A-REAL-KEY" not in encoded


# ── diagnostics ──────────────────────────────────────────────────────────


def test_diagnostics_endpoint_returns_allowlisted_bundle(env, router):
    endpoint = _endpoint(router, "/api/feedback/diagnostics")
    result = endpoint(
        FakeRequest(),
        route="/static/index.html#chat?token=secret-token",
        user_agent_class="Chrome 140",
        viewport_class="desktop",
        locale="en-US",
        session_id="session-1",
        request_id="req-1",
    )
    encoded = json.dumps(result)
    assert "secret-token" not in encoded
    assert result["diagnostics"]["route"] == "/static/index.html"
    assert result["diagnostics"]["app"]["version"]


# ── uploads ──────────────────────────────────────────────────────────────


def test_upload_validates_and_stores_screenshot(env, router):
    result = _upload(router, file=FakeUpload(PNG))
    assert result["ok"] is True
    attachment = result["attachment"]
    assert attachment["content_type"] == "image/png"
    assert attachment["bytes"] == len(PNG)
    assert len(attachment["sha256"]) == 64


@pytest.mark.parametrize(
    "data,filename",
    [
        (b"<svg xmlns='http://www.w3.org/2000/svg'></svg>", "x.svg"),
        (b"GIF89a", "x.gif"),
        (b"MZ\x90\x00", "x.exe"),
    ],
)
def test_upload_rejects_non_images(env, router, data, filename):
    with pytest.raises(HTTPException) as exc:
        _upload(router, file=FakeUpload(data, filename))
    assert exc.value.status_code == 400
    assert "PNG, JPEG, or WebP" in exc.value.detail


def test_upload_rejects_oversize(env, router):
    from src import feedback_report as report

    too_big = b"\x89PNG\r\n\x1a\n" + b"x" * report.MAX_ATTACHMENT_BYTES
    with pytest.raises(HTTPException) as exc:
        _upload(router, file=FakeUpload(too_big))
    assert "MB or smaller" in exc.value.detail


def test_upload_enforces_attachment_count(env, router):
    from src import feedback_report as report

    for index in range(report.MAX_ATTACHMENTS):
        _upload(router, file=FakeUpload(PNG, f"shot-{index}.png"))
    with pytest.raises(HTTPException) as exc:
        _upload(router, file=FakeUpload(PNG, "one-more.png"))
    assert "at most" in exc.value.detail


def test_attachment_route_is_owner_scoped(env, router):
    attachment = _upload(router, file=FakeUpload(PNG))["attachment"]
    endpoint = _endpoint(router, "/api/feedback/attachment/{draft_id}/{attachment_id}")
    response = endpoint(FakeRequest(), draft_id="draft-abc12345", attachment_id=attachment["id"])
    assert response.body == PNG
    assert response.headers["x-content-type-options"] == "nosniff"
    with pytest.raises(HTTPException) as exc:
        endpoint(FakeRequest(owner="someone-else"), draft_id="draft-abc12345", attachment_id=attachment["id"])
    assert exc.value.status_code == 404


# ── prepare ──────────────────────────────────────────────────────────────


def test_prepare_builds_exact_public_report(env, router, configured):
    configured.duplicates = [
        {"number": 7, "title": "Send button broken", "url": "https://x/7", "state": "open", "updated_at": "now"}
    ]
    attachment = _upload(router, file=FakeUpload(PNG, "send-button.png"), label="error dialog")["attachment"]
    endpoint = _endpoint(router, "/api/feedback/prepare", "POST")
    payload = _draft_payload(attachment_ids=[attachment["id"]], session_id="session-1")
    result = asyncio.run(endpoint(FakeRequest(payload)))
    from src import feedback_report as report

    draft = report.validate_draft(payload)
    assert result["title"] == report.build_issue_title(draft)
    assert result["body"] == report.build_issue_body(
        draft,
        diagnostics=result["diagnostics"],
        attachments=result["attachments"],
        url_base="",
    )
    assert result["duplicates"][0]["number"] == 7
    assert result["security_private_only"] is False
    assert result["attachments"][0]["label"] == "error dialog"
    assert "PRIVATE KEY" not in json.dumps(result)


def test_prepare_rejects_missing_attachment(env, router, configured):
    endpoint = _endpoint(router, "/api/feedback/prepare", "POST")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(endpoint(FakeRequest(_draft_payload(attachment_ids=["att-deadbeef"]))))
    assert "could not be found" in exc.value.detail


def test_prepare_security_report_is_private_only(env, router, configured):
    endpoint = _endpoint(router, "/api/feedback/prepare", "POST")
    result = asyncio.run(endpoint(FakeRequest(_draft_payload(type="security"))))
    assert result["security_private_only"] is True
    assert "security/advisories/new" in result["security_url"]


# ── submit ───────────────────────────────────────────────────────────────


def test_submit_requires_confirmation_decision_and_key(env, router, configured):
    endpoint = _endpoint(router, "/api/feedback/submit", "POST")
    for payload in (
        _draft_payload(confirmation=False),
        _draft_payload(duplicate_decision="whatever"),
        _draft_payload(idempotency_key="bad key"),
    ):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(endpoint(FakeRequest(payload)))
        assert exc.value.status_code == 400
    assert configured.calls == []


def test_submit_security_report_never_posts(env, router, configured):
    endpoint = _endpoint(router, "/api/feedback/submit", "POST")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(endpoint(FakeRequest(_draft_payload(type="security"))))
    assert exc.value.status_code == 409
    assert "not posted publicly" in exc.value.detail
    assert configured.calls == []


def test_submit_unconfigured_is_503_and_leaves_draft_retryable(env, router, monkeypatch):
    monkeypatch.setattr(gh, "resolve_config", lambda: None)
    monkeypatch.setattr(
        gh,
        "client_for_submission",
        lambda: (_ for _ in ()).throw(gh.GitHubIssuesError("github_unconfigured", "GitHub submission is not configured.")),
    )
    endpoint = _endpoint(router, "/api/feedback/submit", "POST")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(endpoint(FakeRequest(_draft_payload())))
    assert exc.value.status_code == 503
    assert store.get_submission("sub-0000000000000001") is None


def test_submit_is_exactly_once(env, router, configured):
    endpoint = _endpoint(router, "/api/feedback/submit", "POST")
    payload = _draft_payload()
    first = asyncio.run(endpoint(FakeRequest(payload)))
    assert first["ok"] is True and first["already_submitted"] is False
    assert first["issue_url"].endswith("/issues/42")
    second = asyncio.run(endpoint(FakeRequest(payload)))
    assert second["already_submitted"] is True
    assert second["issue_number"] == 42
    creates = [call for call in configured.calls if call[0] == "create"]
    assert len(creates) == 1
    # A different key would still create a new report; the guard is per key.
    third = asyncio.run(endpoint(FakeRequest(_draft_payload(idempotency_key="sub-0000000000000002"))))
    assert third["already_submitted"] is False
    assert len([call for call in configured.calls if call[0] == "create"]) == 2


def test_submit_failure_is_retryable_with_the_same_key(env, router, monkeypatch):
    error = gh.GitHubIssuesError("github_unavailable", "GitHub is unavailable.", retryable=True)
    client = FakeClient(create_error=error)
    monkeypatch.setattr(gh, "resolve_config", _fake_config)
    monkeypatch.setattr(gh, "client_for_submission", lambda: client)
    endpoint = _endpoint(router, "/api/feedback/submit", "POST")
    payload = _draft_payload()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(endpoint(FakeRequest(payload)))
    assert exc.value.status_code == 502
    assert store.get_submission("sub-0000000000000001")["status"] == "failed"
    retry = asyncio.run(endpoint(FakeRequest(payload)))
    assert retry["ok"] is True
    assert len([call for call in client.calls if call[0] == "create"]) == 2


def test_submit_adds_evidence_to_matching_existing_issue(env, router, configured):
    configured.duplicates = [
        {"number": 7, "title": "Send button broken", "url": "https://x/7", "state": "open", "updated_at": "now"}
    ]
    endpoint = _endpoint(router, "/api/feedback/submit", "POST")
    payload = _draft_payload(duplicate_decision="existing", existing_issue_number=7)
    result = asyncio.run(endpoint(FakeRequest(payload)))
    assert result["attached_to_existing"] is True
    assert result["issue_number"] == 7
    assert [call[0] for call in configured.calls if call[0] in ("create", "comment")] == ["comment"]


def test_submit_rejects_existing_issue_not_in_candidates(env, router, configured):
    configured.duplicates = []
    endpoint = _endpoint(router, "/api/feedback/submit", "POST")
    payload = _draft_payload(duplicate_decision="existing", existing_issue_number=999)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(endpoint(FakeRequest(payload)))
    assert "not found in the duplicate results" in exc.value.detail
    assert [call for call in configured.calls if call[0] in ("create", "comment")] == []


def test_submit_returns_the_redacted_public_report(env, router, configured):
    endpoint = _endpoint(router, "/api/feedback/submit", "POST")
    payload = _draft_payload(actual="Failed with password: " + "h" * 16)
    result = asyncio.run(endpoint(FakeRequest(payload)))
    encoded = json.dumps(result)
    assert "h" * 16 not in encoded
    assert result["redacted_report"]["title"].startswith("[Bug]")
    assert "### Summary" in result["redacted_report"]["body"]


def test_submit_rejects_unknown_attachment(env, router, configured):
    endpoint = _endpoint(router, "/api/feedback/submit", "POST")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(endpoint(FakeRequest(_draft_payload(attachment_ids=["att-deadbeef"]))))
    assert "could not be found" in exc.value.detail


def test_browser_bundle_contains_no_github_credential_plumbing():
    """The browser never receives or forwards a repository credential."""
    source = open("static/js/bugReport.js", encoding="utf-8").read()
    assert "Authorization" not in source
    assert "private_key" not in source
    assert "ghs_" not in source
    assert "PANDAMONIUM_GITHUB" not in source


def test_route_module_never_names_private_key_material():
    source = open("routes/feedback_routes.py", encoding="utf-8").read()
    assert "private_key" not in source
    assert "Authorization" not in source