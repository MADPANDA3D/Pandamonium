"""Server-held GitHub App client for in-app bug reports (MAD-856).

The browser never sees a repository credential. This module exchanges a
server-configured GitHub App private key for a short-lived installation access
token and uses it for the two operations the report flow needs: a bounded
duplicate search and issue creation (plus evidence comments for an existing
issue). Requested permissions are intentionally minimal — the operator
installs the App with **Issues: write only**.

Configuration is installation-owned and optional. With nothing configured the
client is ``available() is False`` and callers must fail closed with honest
copy; tests inject a fake transport instead of touching the network.

Secret handling:

* the private key and installation token are never logged, never returned in
  any payload, and never included in an exception message;
* only method/path/status are logged;
* the token is cached in memory and refreshed shortly before expiry.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

API_BASE = "https://api.github.com"
DEFAULT_REPO = "MADPANDA3D/Pandamonium"
TOKEN_REFRESH_SKEW_SECONDS = 300
HTTP_TIMEOUT_SECONDS = 20
MAX_SEARCH_RESULTS = 5

# owner/name with GitHub's allowed characters.
_REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}$")
_PRIVATE_KEY_ENV = "PANDAMONIUM_GITHUB_APP_PRIVATE_KEY"
_PRIVATE_KEY_FILE_ENV = "PANDAMONIUM_GITHUB_APP_PRIVATE_KEY_FILE"


class GitHubIssuesError(RuntimeError):
    """A bounded GitHub failure with honest copy and a retryable flag."""

    def __init__(self, code: str, message: str, *, retryable: bool = False, status: int = 0):
        super().__init__(message)
        self.code = str(code or "github_error")
        self.message = str(message or "")
        self.retryable = bool(retryable)
        self.status = int(status or 0)


@dataclass(frozen=True)
class GitHubConfig:
    app_id: str
    installation_id: str
    private_key: str
    repo: str = DEFAULT_REPO
    api_base: str = API_BASE
    attachment_url_base: str = ""
    security_url: str = ""

    def public_status(self) -> dict[str, Any]:
        """Redacted status safe for a route payload (no key material)."""
        return {
            "repo": self.repo,
            "app_id": self.app_id,
            "api_base": self.api_base,
            "attachment_base_configured": bool(self.attachment_url_base),
            "security_url": self.security_url,
        }


def _env(name: str) -> str:
    return str(os.getenv(name) or "").strip()


def _load_private_key() -> str:
    inline = _env(_PRIVATE_KEY_ENV)
    if inline:
        # Operators commonly paste PEMs with escaped newlines into env config.
        return inline.replace("\\n", "\n")
    path = _env(_PRIVATE_KEY_FILE_ENV)
    if not path:
        return ""
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return ""


def resolve_config() -> Optional[GitHubConfig]:
    """Build the installation config, or None when not fully configured."""
    app_id = _env("PANDAMONIUM_GITHUB_APP_ID")
    installation_id = _env("PANDAMONIUM_GITHUB_APP_INSTALLATION_ID")
    private_key = _load_private_key()
    if not (app_id and installation_id and private_key):
        return None
    repo = _env("PANDAMONIUM_GITHUB_REPO") or DEFAULT_REPO
    if not _REPO_RE.fullmatch(repo):
        return None
    api_base = (_env("PANDAMONIUM_GITHUB_API_BASE") or API_BASE).rstrip("/")
    if not api_base.startswith("https://"):
        return None
    attachment_url_base = _env("PANDAMONIUM_FEEDBACK_ATTACHMENT_URL_BASE")
    security_url = _env("PANDAMONIUM_FEEDBACK_SECURITY_URL") or (
        f"https://github.com/{repo}/security/advisories/new"
    )
    return GitHubConfig(
        app_id=app_id,
        installation_id=installation_id,
        private_key=private_key,
        repo=repo,
        api_base=api_base,
        attachment_url_base=attachment_url_base,
        security_url=security_url,
    )


def config_reason() -> str:
    """Honest reason the submission path is unavailable (never echoes values)."""
    if not _env("PANDAMONIUM_GITHUB_APP_ID") or not _env("PANDAMONIUM_GITHUB_APP_INSTALLATION_ID"):
        return (
            "GitHub submission is not configured on this installation. Set "
            "PANDAMONIUM_GITHUB_APP_ID, PANDAMONIUM_GITHUB_APP_INSTALLATION_ID, and the "
            "App private key, then restart."
        )
    if not _load_private_key():
        return (
            "The GitHub App private key is missing or unreadable. Set "
            "PANDAMONIUM_GITHUB_APP_PRIVATE_KEY (or ..._PRIVATE_KEY_FILE) to a valid PEM."
        )
    if not _REPO_RE.fullmatch(_env("PANDAMONIUM_GITHUB_REPO") or DEFAULT_REPO):
        return "PANDAMONIUM_GITHUB_REPO must be an owner/name repository slug."
    return "GitHub submission is not configured on this installation."


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def build_app_jwt(app_id: str, private_key_pem: str, *, now: Optional[float] = None) -> str:
    """Build a short-lived RS256 GitHub App JWT using only `cryptography`."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    issued = int(now if now is not None else time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    payload = {"iat": issued - 60, "exp": issued + 540, "iss": str(app_id)}
    signing_input = ".".join(
        _b64url(json.dumps(part, separators=(",", ":")).encode("utf-8"))
        for part in (header, payload)
    )
    try:
        key = serialization.load_pem_private_key(str(private_key_pem).encode("utf-8"), password=None)
    except (ValueError, TypeError) as exc:
        raise GitHubIssuesError(
            "github_key_invalid",
            "The configured GitHub App private key could not be read.",
        ) from exc
    signature = key.sign(signing_input.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_input}.{_b64url(signature)}"


def _default_transport(method: str, url: str, *, headers: dict, json_body: Any, params: Any, timeout: int):
    import httpx

    return httpx.request(
        method,
        url,
        headers=headers,
        json=json_body,
        params=params,
        timeout=timeout,
    )


class GitHubIssuesClient:
    def __init__(
        self,
        config: GitHubConfig,
        *,
        transport: Optional[Callable[..., Any]] = None,
    ):
        self.config = config
        self._transport = transport or _default_transport
        self._token = ""
        self._token_expires_at = 0.0

    # ── token ────────────────────────────────────────────────────────────

    def _installation_token(self) -> str:
        now = time.time()
        if self._token and now < self._token_expires_at - TOKEN_REFRESH_SKEW_SECONDS:
            return self._token
        jwt_token = build_app_jwt(self.config.app_id, self.config.private_key)
        response = self._transport(
            "POST",
            f"{self.config.api_base}/app/installations/{self.config.installation_id}/access_tokens",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {jwt_token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json_body=None,
            params=None,
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        status = int(getattr(response, "status_code", 0) or 0)
        if status == 401:
            raise GitHubIssuesError(
                "github_auth_failed",
                "GitHub rejected the App credentials. Check the App id, installation id, and private key.",
                status=status,
            )
        if status == 404:
            raise GitHubIssuesError(
                "github_not_installed",
                "The GitHub App is not installed on the configured repository. Install it with Issues: write access.",
                status=status,
            )
        if status >= 400:
            raise _error_for_status(status, "install a token")
        try:
            payload = response.json()
        except Exception as exc:
            raise GitHubIssuesError(
                "github_bad_response", "GitHub returned an unreadable token response.", retryable=True
            ) from exc
        token = str((payload or {}).get("token") or "")
        expires_at = str((payload or {}).get("expires_at") or "")
        if not token:
            raise GitHubIssuesError("github_bad_response", "GitHub returned no installation token.")
        self._token = token
        self._token_expires_at = _parse_expiry(expires_at, now)
        return token

    # ── requests ─────────────────────────────────────────────────────────

    def _request(self, method: str, path: str, *, json_body: Any = None, params: Any = None):
        token = self._installation_token()
        response = self._transport(
            method,
            f"{self.config.api_base}{path}",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json_body=json_body,
            params=params,
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        status = int(getattr(response, "status_code", 0) or 0)
        # Never log headers or bodies that can carry user content beyond the
        # caller's reviewed text; method/path/status only.
        logger.info("github app request method=%s path=%s status=%s", method, path, status)
        if status >= 400:
            raise _error_for_status(status, path)
        return response

    # ── operations ───────────────────────────────────────────────────────

    def search_open_issues(self, terms: str, *, limit: int = MAX_SEARCH_RESULTS) -> list[dict[str, Any]]:
        query = f"repo:{self.config.repo} is:issue is:open"
        if terms.strip():
            query += f" {terms.strip()}"
        response = self._request(
            "GET",
            "/search/issues",
            params={"q": query, "per_page": max(1, min(int(limit), MAX_SEARCH_RESULTS))},
        )
        try:
            payload = response.json() or {}
        except Exception as exc:
            raise GitHubIssuesError("github_bad_response", "GitHub returned an unreadable search response.") from exc
        candidates = []
        for item in (payload.get("items") or [])[:MAX_SEARCH_RESULTS]:
            if not isinstance(item, dict):
                continue
            candidates.append(
                {
                    "number": int(item.get("number") or 0),
                    "title": str(item.get("title") or "")[:200],
                    "url": str(item.get("html_url") or ""),
                    "state": str(item.get("state") or "open")[:20],
                    "updated_at": str(item.get("updated_at") or "")[:40],
                }
            )
        return candidates

    def create_issue(self, *, title: str, body: str) -> dict[str, Any]:
        response = self._request(
            "POST",
            f"/repos/{self.config.repo}/issues",
            json_body={"title": title[:250], "body": body},
        )
        try:
            payload = response.json() or {}
        except Exception as exc:
            raise GitHubIssuesError("github_bad_response", "GitHub returned an unreadable issue response.") from exc
        number = int(payload.get("number") or 0)
        url = str(payload.get("html_url") or "")
        if not number or not url:
            raise GitHubIssuesError("github_bad_response", "GitHub did not return the created issue.")
        return {"number": number, "url": url}

    def add_issue_comment(self, *, issue_number: int, body: str) -> dict[str, Any]:
        if not issue_number:
            raise GitHubIssuesError("invalid_issue", "Choose an existing issue to add evidence to.")
        response = self._request(
            "POST",
            f"/repos/{self.config.repo}/issues/{int(issue_number)}/comments",
            json_body={"body": body},
        )
        try:
            payload = response.json() or {}
        except Exception as exc:
            raise GitHubIssuesError("github_bad_response", "GitHub returned an unreadable comment response.") from exc
        return {
            "comment_url": str(payload.get("html_url") or ""),
            "issue_number": int(issue_number),
            "issue_url": f"https://github.com/{self.config.repo}/issues/{int(issue_number)}",
        }

    def security_advisory_url(self) -> str:
        return self.config.security_url


def _parse_expiry(value: str, now: float) -> float:
    try:
        from datetime import datetime, timezone

        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (TypeError, ValueError):
        return now + 1800


def _error_for_status(status: int, context: str) -> GitHubIssuesError:
    if status in (401,):
        return GitHubIssuesError(
            "github_auth_failed",
            "GitHub rejected the installation token. Re-check the App configuration.",
            status=status,
        )
    if status == 403:
        return GitHubIssuesError(
            "github_forbidden",
            "The GitHub App is missing Issues: write access on the configured repository.",
            status=status,
        )
    if status == 404:
        return GitHubIssuesError(
            "github_not_found",
            "GitHub could not find the configured repository or resource.",
            status=status,
        )
    if status == 422:
        return GitHubIssuesError(
            "github_rejected",
            "GitHub rejected the request payload.",
            status=status,
        )
    if status == 429:
        return GitHubIssuesError(
            "github_rate_limited",
            "GitHub rate-limited the request. Try again shortly.",
            retryable=True,
            status=status,
        )
    if status >= 500:
        return GitHubIssuesError(
            "github_unavailable",
            "GitHub is unavailable right now. The report is saved locally; retry when it recovers.",
            retryable=True,
            status=status,
        )
    return GitHubIssuesError(
        "github_error",
        f"GitHub rejected the {context} request (status {status}).",
        status=status,
    )


def client_for_submission() -> GitHubIssuesClient:
    """Resolve the configured client or raise the honest fail-closed error."""
    config = resolve_config()
    if config is None:
        raise GitHubIssuesError("github_unconfigured", config_reason())
    return GitHubIssuesClient(config)