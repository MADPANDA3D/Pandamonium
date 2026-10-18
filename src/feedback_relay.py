"""Submit reviewed feedback to the operator's relay (MAD-989).

The universal submission path: no GitHub credential lives in the product. The
reviewed, already-redacted report is POSTed to the operator's relay
(an n8n webhook per MAD-984), which files it in the canonical tracker and
returns the issue URL. Configuration is installation-owned env:

    PANDAMONIUM_FEEDBACK_RELAY_URL   https://.../webhook/pandamonium-feedback
    PANDAMONIUM_FEEDBACK_RELAY_TOKEN shared relay token (not a secret; abuse
                                     control stays server-side on the relay)
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Callable, Mapping, Optional

SCHEMA = "pandamonium-feedback/v1"
SCHEMA_HEADER = "X-Pandamonium-Schema"
TOKEN_HEADER = "X-Pandamonium-Relay-Token"
DEFAULT_TIMEOUT_SECONDS = 45
MAX_RESPONSE_BYTES = 64 * 1024


class FeedbackRelayError(RuntimeError):
    """Raised for a rejected or failed relay submission."""

    def __init__(self, code: str, message: str, *, retryable: bool = False, status: int = 0):
        super().__init__(message)
        self.code = str(code or "relay_error")
        self.message = str(message or "")
        self.retryable = bool(retryable)
        self.status = int(status or 0)


def _env(name: str) -> str:
    return str(os.environ.get(name) or "").strip()


class FeedbackRelayConfig:
    def __init__(self, url: str, token: str = "", *, timeout: int = DEFAULT_TIMEOUT_SECONDS):
        self.url = url
        self.token = token
        self.timeout = int(timeout)

    def public_status(self) -> dict[str, Any]:
        return {"mode": "relay", "relay_url_configured": True, "token_configured": bool(self.token)}


def resolve_config() -> Optional[FeedbackRelayConfig]:
    url = _env("PANDAMONIUM_FEEDBACK_RELAY_URL")
    if not url or not url.lower().startswith("https://"):
        return None
    return FeedbackRelayConfig(url, _env("PANDAMONIUM_FEEDBACK_RELAY_TOKEN"))


def config_reason() -> str:
    url = _env("PANDAMONIUM_FEEDBACK_RELAY_URL")
    if not url:
        return "No feedback relay is configured on this installation."
    if not url.lower().startswith("https://"):
        return "The feedback relay URL must use HTTPS."
    return ""


def _default_transport(url: str, *, headers: Mapping[str, str], body: bytes, timeout: int) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status), response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read(MAX_RESPONSE_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise FeedbackRelayError(
            "relay_unreachable",
            "Could not reach the feedback relay. Your draft is saved; retry when connectivity returns.",
            retryable=True,
        ) from exc


class FeedbackRelayClient:
    def __init__(
        self,
        config: FeedbackRelayConfig,
        *,
        transport: Callable[..., tuple[int, bytes]] | None = None,
    ):
        self.config = config
        self._transport = transport or _default_transport

    def submit(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """POST one reviewed report; return {issue_url, issue_number}."""
        body = json.dumps(dict(payload), ensure_ascii=True).encode("utf-8")
        headers = {"Content-Type": "application/json", SCHEMA_HEADER: SCHEMA}
        if self.config.token:
            headers[TOKEN_HEADER] = self.config.token
        status, raw = self._transport(
            self.config.url, headers=headers, body=body, timeout=self.config.timeout
        )
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        if status == 429 or status >= 500:
            raise FeedbackRelayError(
                "relay_busy",
                "The feedback relay is busy. Your draft is saved; retry in a moment.",
                retryable=True,
                status=status,
            )
        if status >= 400 or data.get("ok") is False:
            message = str(data.get("message") or "The feedback relay rejected this report.")
            raise FeedbackRelayError("relay_rejected", message, retryable=False, status=status)
        issue_url = str(data.get("issue_url") or data.get("url") or "")
        try:
            issue_number = int(data.get("issue_number") or data.get("number") or 0)
        except (TypeError, ValueError):
            issue_number = 0
        return {"issue_url": issue_url, "issue_number": issue_number}


def client_for_submission() -> FeedbackRelayClient:
    config = resolve_config()
    if config is None:
        raise FeedbackRelayError("relay_not_configured", config_reason() or "No feedback relay is configured.")
    return FeedbackRelayClient(config)
