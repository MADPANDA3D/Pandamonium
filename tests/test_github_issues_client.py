"""MAD-856: server-held GitHub App client contract tests (no network)."""
from __future__ import annotations

import base64
import json
import time
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from src import github_issues as gh


@pytest.fixture(scope="module")
def keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    return private_pem, key.public_key()


def _config(keypair, **overrides):
    private_pem, _ = keypair
    values = {
        "app_id": "123456",
        "installation_id": "78910",
        "private_key": private_pem,
        "repo": "MADPANDA3D/Pandamonium",
        "api_base": "https://api.github.com",
        "attachment_url_base": "",
        "security_url": "https://github.com/MADPANDA3D/Pandamonium/security/advisories/new",
    }
    values.update(overrides)
    return gh.GitHubConfig(**values)


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


class FakeTransport:
    def __init__(self, handler):
        self.calls = []
        self.handler = handler

    def __call__(self, method, url, *, headers, json_body, params, timeout):
        self.calls.append({"method": method, "url": url, "headers": dict(headers), "json": json_body, "params": params})
        return self.handler(method, url, json_body)


def _token_handler(expires_in=3600):
    def handler(method, url, json_body):
        if url.endswith("/access_tokens"):
            return FakeResponse(201, {"token": "ghs_installationtokenvalue", "expires_at": _iso_expiry(expires_in)})
        return FakeResponse(200, {})
    return handler


def _iso_expiry(seconds):
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


# ── JWT ──────────────────────────────────────────────────────────────────


def test_build_app_jwt_has_valid_rs256_signature(keypair):
    private_pem, public_key = keypair
    token = gh.build_app_jwt("123456", private_pem, now=1_700_000_000)
    header_b64, payload_b64, signature_b64 = token.split(".")
    header = json.loads(_b64decode(header_b64))
    payload = json.loads(_b64decode(payload_b64))
    assert header == {"alg": "RS256", "typ": "JWT"}
    assert payload["iss"] == "123456"
    assert payload["iat"] < 1_700_000_000
    assert payload["exp"] > 1_700_000_000
    public_key.verify(
        _b64decode(signature_b64),
        f"{header_b64}.{payload_b64}".encode("ascii"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def test_build_app_jwt_rejects_invalid_key():
    with pytest.raises(gh.GitHubIssuesError) as exc:
        gh.build_app_jwt("1", "not a pem")
    assert exc.value.code == "github_key_invalid"


# ── configuration ────────────────────────────────────────────────────────


def test_resolve_config_is_none_without_env(monkeypatch):
    for name in (
        "PANDAMONIUM_GITHUB_APP_ID",
        "PANDAMONIUM_GITHUB_APP_INSTALLATION_ID",
        gh._PRIVATE_KEY_ENV,
        gh._PRIVATE_KEY_FILE_ENV,
        "PANDAMONIUM_GITHUB_REPO",
    ):
        monkeypatch.delenv(name, raising=False)
    assert gh.resolve_config() is None
    assert "PANDAMONIUM_GITHUB_APP_ID" in gh.config_reason()


def test_resolve_config_from_env_never_exposes_the_key(monkeypatch, keypair):
    private_pem, _ = keypair
    monkeypatch.setenv("PANDAMONIUM_GITHUB_APP_ID", "123456")
    monkeypatch.setenv("PANDAMONIUM_GITHUB_APP_INSTALLATION_ID", "78910")
    monkeypatch.setenv(gh._PRIVATE_KEY_ENV, private_pem.replace("\n", "\\n"))
    monkeypatch.setenv("PANDAMONIUM_GITHUB_REPO", "MADPANDA3D/Pandamonium")
    config = gh.resolve_config()
    assert config is not None
    status = config.public_status()
    assert status["repo"] == "MADPANDA3D/Pandamonium"
    assert "PRIVATE KEY" not in json.dumps(status)


def test_resolve_config_rejects_bad_repo_and_http_api_base(monkeypatch, keypair):
    private_pem, _ = keypair
    monkeypatch.setenv("PANDAMONIUM_GITHUB_APP_ID", "1")
    monkeypatch.setenv("PANDAMONIUM_GITHUB_APP_INSTALLATION_ID", "2")
    monkeypatch.setenv(gh._PRIVATE_KEY_ENV, private_pem)
    monkeypatch.setenv("PANDAMONIUM_GITHUB_REPO", "../../etc/passwd")
    assert gh.resolve_config() is None
    monkeypatch.setenv("PANDAMONIUM_GITHUB_REPO", "MADPANDA3D/Pandamonium")
    monkeypatch.setenv("PANDAMONIUM_GITHUB_API_BASE", "http://evil.example.com")
    assert gh.resolve_config() is None


def test_client_for_submission_fails_closed(monkeypatch):
    monkeypatch.delenv("PANDAMONIUM_GITHUB_APP_ID", raising=False)
    with pytest.raises(gh.GitHubIssuesError) as exc:
        gh.client_for_submission()
    assert exc.value.code == "github_unconfigured"


# ── token + operations ───────────────────────────────────────────────────


def test_installation_token_is_cached_and_refreshed(keypair):
    private_pem, _ = keypair
    transport = FakeTransport(_token_handler(expires_in=3600))
    client = gh.GitHubIssuesClient(_config(keypair), transport=transport)
    first = client._installation_token()
    second = client._installation_token()
    assert first == second == "ghs_installationtokenvalue"
    exchanges = [call for call in transport.calls if call["url"].endswith("/access_tokens")]
    assert len(exchanges) == 1
    # Force near-expiry and confirm exactly one more exchange.
    client._token_expires_at = time.time() + 10
    client._installation_token()
    exchanges = [call for call in transport.calls if call["url"].endswith("/access_tokens")]
    assert len(exchanges) == 2


def test_search_open_issues_builds_bounded_query(keypair):
    transport = FakeTransport(
        lambda method, url, body: (
            FakeResponse(201, {"token": "t", "expires_at": _iso_expiry(3600)})
            if url.endswith("/access_tokens")
            else FakeResponse(
                200,
                {
                    "items": [
                        {"number": 12, "title": "Send button broken", "html_url": "https://x/12", "state": "open"},
                        {"number": 13, "title": "Other", "html_url": "https://x/13", "state": "open"},
                    ]
                },
            )
        )
    )
    client = gh.GitHubIssuesClient(_config(keypair), transport=transport)
    results = client.search_open_issues("Send button broken", limit=99)
    assert [row["number"] for row in results] == [12, 13]
    search = next(call for call in transport.calls if "/search/issues" in call["url"])
    assert search["params"]["q"].startswith("repo:MADPANDA3D/Pandamonium is:issue is:open")
    assert search["params"]["per_page"] <= gh.MAX_SEARCH_RESULTS


def test_create_issue_returns_number_and_url(keypair):
    def handler(method, url, body):
        if url.endswith("/access_tokens"):
            return FakeResponse(201, {"token": "t", "expires_at": _iso_expiry(3600)})
        return FakeResponse(201, {"number": 42, "html_url": "https://github.com/x/y/issues/42"})

    transport = FakeTransport(handler)
    client = gh.GitHubIssuesClient(_config(keypair), transport=transport)
    result = client.create_issue(title="[Bug] x", body="body")
    assert result == {"number": 42, "url": "https://github.com/x/y/issues/42"}
    created = next(call for call in transport.calls if call["url"].endswith("/issues"))
    assert created["json"]["title"] == "[Bug] x"
    assert created["json"]["body"] == "body"


def test_add_issue_comment_returns_issue_url(keypair):
    def handler(method, url, body):
        if url.endswith("/access_tokens"):
            return FakeResponse(201, {"token": "t", "expires_at": _iso_expiry(3600)})
        return FakeResponse(201, {"html_url": "https://github.com/x/y/issues/7#issuecomment-1"})

    transport = FakeTransport(handler)
    client = gh.GitHubIssuesClient(_config(keypair), transport=transport)
    result = client.add_issue_comment(issue_number=7, body="evidence")
    assert result["issue_number"] == 7
    assert result["issue_url"].endswith("/issues/7")


@pytest.mark.parametrize(
    "status,code,retryable",
    [
        (401, "github_auth_failed", False),
        (403, "github_forbidden", False),
        (404, "github_not_found", False),
        (429, "github_rate_limited", True),
        (500, "github_unavailable", True),
    ],
)
def test_status_errors_map_to_honest_codes(keypair, status, code, retryable):
    def handler(method, url, body):
        if url.endswith("/access_tokens"):
            return FakeResponse(201, {"token": "t", "expires_at": _iso_expiry(3600)})
        return FakeResponse(status, {"message": "nope"})

    client = gh.GitHubIssuesClient(_config(keypair), transport=FakeTransport(handler))
    with pytest.raises(gh.GitHubIssuesError) as exc:
        client.create_issue(title="x", body="y")
    assert exc.value.code == code
    assert exc.value.retryable is retryable
    assert "nope" not in exc.value.message


def test_token_never_appears_in_errors_or_logs(keypair, caplog):
    def handler(method, url, body):
        if url.endswith("/access_tokens"):
            return FakeResponse(201, {"token": "ghs_supersecretvalue", "expires_at": _iso_expiry(3600)})
        return FakeResponse(500, {"message": "boom"})

    client = gh.GitHubIssuesClient(_config(keypair), transport=FakeTransport(handler))
    with caplog.at_level("INFO"):
        with pytest.raises(gh.GitHubIssuesError) as exc:
            client.create_issue(title="x", body="y")
    assert "ghs_supersecretvalue" not in str(exc.value)
    assert "ghs_supersecretvalue" not in caplog.text