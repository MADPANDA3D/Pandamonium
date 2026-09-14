"""Guided in-app bug reports: validation, redaction, and issue construction (MAD-856).

This module is the policy core behind ``routes/feedback_routes.py``. It owns:

* strict, bounded validation of the user-supplied report draft;
* the allowlisted diagnostic bundle (never arbitrary logs or message content);
* redaction of credentials and secret material before anything is shown or
  sent to GitHub;
* the exact public issue title/body that the review screen displays and the
  server submits — built once here so the preview can never differ from what
  is posted;
* attachment type/size/count constants and magic-byte validation.

Nothing here performs network I/O, so every rule is unit-testable and the
GitHub client stays a thin transport.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

from src.authority_protocol import redact_secret_text

# ── limits ───────────────────────────────────────────────────────────────

MAX_SUMMARY_CHARS = 160
MIN_SUMMARY_CHARS = 4
MAX_FIELD_CHARS = 2000
MAX_ACTUAL_CHARS = 4000
MAX_REVIEWED_TEXT_CHARS = 4000
MAX_STEPS = 30
MAX_STEP_CHARS = 600
MAX_BODY_CHARS = 60000
MAX_ATTACHMENTS = 8
MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024
MAX_TOTAL_ATTACHMENT_BYTES = 32 * 1024 * 1024
MAX_DIAGNOSTICS_CHARS = 6000
MAX_LABEL_CHARS = 80
MAX_ROUTE_CHARS = 160

# Image types accepted for screenshots. Sniffed from the bytes, never trusted
# from the filename or the browser-supplied content type.
ALLOWED_IMAGE_TYPES: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}
_MAX_DIMENSION_NOTE = "1600"

REPORT_TYPES: dict[str, dict[str, str]] = {
    "bug": {"label": "Bug", "prefix": "[Bug]"},
    "requested_fix": {"label": "Requested fix", "prefix": "[Fix]"},
    "product_change": {"label": "Product change", "prefix": "[Change]"},
    "security": {"label": "Security vulnerability", "prefix": "[Security]"},
}


class FeedbackValidationError(ValueError):
    """Raised for a rejected draft; callers map this to honest copy."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = str(code or "invalid_report")
        self.message = str(message or "")


# ── redaction ────────────────────────────────────────────────────────────

# Automatically collected values must never carry userinfo or credential-like
# query parameters. `redact_secret_text` already masks bearer tokens and
# key=value secrets; these strip the remaining URL carry-overs.
_URL_RE = re.compile(r"(?i)\bhttps?://[^\s<>\"']{4,}")
_URL_USERINFO_RE = re.compile(r"(?i)\b(https?://)[^/@\s]{1,120}@")
_URL_QUERY_RE = re.compile(r"([?&](?:token|key|secret|password|sig|signature|auth|code|access_token)=)[^&\s]{1,300}")
_FILE_PATH_RE = re.compile(
    r"(?<![\w/])(?:/(?:Users|home|root|var|etc|opt|srv|mnt|media|private)/[^\s\"'<>]{1,200}"
    r"|[A-Za-z]:\\[^\s\"'<>]{1,200})"
)
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PRIVATE_HOST_RE = re.compile(
    r"(?i)\b(?:localhost|127\.0\.0\.1|10\.\d{1,3}(?:\.\d{1,3}){2}|192\.168\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|[a-z0-9-]+\.(?:local|internal|lan|home))\b"
)


def redact_report_text(value: Any, *, limit: int = MAX_FIELD_CHARS) -> str:
    """Redact secret-shaped material from user-supplied report text.

    The result is what the review screen shows and what the issue body carries,
    so the user can see (and correct) anything redaction removes before submit.
    """
    text = str(value if value is not None else "")
    text = redact_secret_text(text)
    text = _URL_USERINFO_RE.sub(r"\1[redacted]@", text)
    text = _URL_QUERY_RE.sub(r"\1[redacted]", text)
    text = text.replace("\x00", "")
    if len(text) > limit:
        text = text[: max(0, limit - 1)].rstrip() + "…"
    return text


def redact_automatic_text(value: Any, *, limit: int = 300) -> str:
    """Redact automatically collected strings (paths, URLs, hosts, emails)."""
    text = redact_report_text(value, limit=limit)
    text = _FILE_PATH_RE.sub("[path redacted]", text)
    # URLs before bare hosts: replacing a host inside a URL first would leave
    # the URL's remainder looking like text ("http://[host redacted]:8080").
    text = _URL_RE.sub("[url redacted]", text)
    text = _PRIVATE_HOST_RE.sub("[host redacted]", text)
    text = _EMAIL_RE.sub("[email redacted]", text)
    return text


# ── validation ───────────────────────────────────────────────────────────


def _bounded_text(value: Any, *, limit: int, field: str) -> str:
    text = str(value if value is not None else "").strip()
    if len(text) > limit:
        raise FeedbackValidationError(
            "field_too_long",
            f"The {field} field is limited to {limit} characters.",
        )
    return text


def validate_draft_id(value: Any) -> str:
    draft_id = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", draft_id):
        raise FeedbackValidationError("invalid_draft", "The report draft id is invalid.")
    return draft_id


def validate_type(value: Any) -> str:
    report_type = str(value or "bug").strip().lower()
    if report_type not in REPORT_TYPES:
        raise FeedbackValidationError(
            "invalid_type",
            "The report type must be bug, requested_fix, product_change, or security.",
        )
    return report_type


def validate_steps(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        raw = [line for line in value.splitlines()]
    elif isinstance(value, (list, tuple)):
        raw = list(value)
    else:
        raise FeedbackValidationError("invalid_steps", "Reproduction steps must be a list of lines.")
    steps: list[str] = []
    for item in raw[: MAX_STEPS + 1]:
        text = _bounded_text(item, limit=MAX_STEP_CHARS, field="reproduction step")
        if text:
            steps.append(redact_report_text(text, limit=MAX_STEP_CHARS))
    if len(steps) > MAX_STEPS:
        raise FeedbackValidationError(
            "too_many_steps", f"Keep the reproduction steps to {MAX_STEPS} or fewer."
        )
    return steps


def validate_attachment_ids(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if not isinstance(value, (list, tuple)):
        raise FeedbackValidationError("invalid_attachments", "Attachments must be a list.")
    ids: list[str] = []
    for item in value:
        attachment_id = str(item or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", attachment_id):
            raise FeedbackValidationError("invalid_attachment", "An attachment id is invalid.")
        if attachment_id not in ids:
            ids.append(attachment_id)
    if len(ids) > MAX_ATTACHMENTS:
        raise FeedbackValidationError(
            "too_many_attachments", f"Attach at most {MAX_ATTACHMENTS} screenshots."
        )
    return ids


def validate_draft(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize and bound one report draft. Raises FeedbackValidationError."""
    if not isinstance(payload, Mapping):
        raise FeedbackValidationError("invalid_report", "The report payload must be an object.")
    report_type = validate_type(payload.get("type"))
    summary = redact_report_text(
        _bounded_text(payload.get("summary"), limit=MAX_SUMMARY_CHARS, field="summary"),
        limit=MAX_SUMMARY_CHARS,
    )
    if len(summary) < MIN_SUMMARY_CHARS:
        raise FeedbackValidationError(
            "missing_summary", "Add a short summary (at least a few words)."
        )
    draft = {
        "draft_id": validate_draft_id(payload.get("draft_id")),
        "type": report_type,
        "summary": summary,
        "goal": redact_report_text(_bounded_text(payload.get("goal"), limit=MAX_FIELD_CHARS, field="goal")),
        "expected": redact_report_text(_bounded_text(payload.get("expected"), limit=MAX_FIELD_CHARS, field="expected")),
        "actual": redact_report_text(_bounded_text(payload.get("actual"), limit=MAX_ACTUAL_CHARS, field="actual")),
        "steps": validate_steps(payload.get("steps")),
        "workaround": redact_report_text(
            _bounded_text(payload.get("workaround"), limit=MAX_FIELD_CHARS, field="workaround")
        ),
        "reviewed_text": redact_report_text(
            _bounded_text(payload.get("reviewed_text"), limit=MAX_REVIEWED_TEXT_CHARS, field="reviewed text")
        ),
        "include_diagnostics": payload.get("include_diagnostics", True) is not False,
        "route": redact_automatic_text(
            _bounded_text(payload.get("route"), limit=MAX_ROUTE_CHARS, field="route"),
            limit=MAX_ROUTE_CHARS,
        ),
        "client": _validate_client_context(payload.get("client")),
        "attachments": validate_attachment_ids(payload.get("attachment_ids")),
    }
    return draft


def _validate_client_context(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {
        "user_agent_class": redact_automatic_text(value.get("user_agent_class"), limit=80),
        "viewport_class": redact_automatic_text(value.get("viewport_class"), limit=40),
        "locale": redact_automatic_text(value.get("locale"), limit=40),
        "platform_class": redact_automatic_text(value.get("platform_class"), limit=40),
    }


def validate_confirmation(value: Any) -> bool:
    if value is not True:
        raise FeedbackValidationError(
            "confirmation_required",
            "Confirm that the report and its attachments will be public before submitting.",
        )
    return True


def validate_duplicate_decision(value: Any) -> str:
    decision = str(value or "").strip().lower()
    if decision not in ("new", "existing"):
        raise FeedbackValidationError(
            "duplicate_decision_required",
            "Choose whether to continue as a new report or add evidence to an existing issue.",
        )
    return decision


def validate_idempotency_key(value: Any) -> str:
    key = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", key):
        raise FeedbackValidationError("invalid_idempotency_key", "The submission key is invalid.")
    return key


# ── title / body construction ────────────────────────────────────────────


def build_issue_title(draft: Mapping[str, Any], *, repo: str = "") -> str:
    meta = REPORT_TYPES[str(draft.get("type") or "bug")]
    summary = str(draft.get("summary") or "").strip()
    title = f"{meta['prefix']} {summary}".strip()
    if len(title) > MAX_SUMMARY_CHARS + len(meta["prefix"]) + 1:
        title = title[: MAX_SUMMARY_CHARS + len(meta["prefix"]) + 1].rstrip()
    return title


def _bullets(lines: list[str]) -> str:
    return "\n".join(f"- {line}" for line in lines) if lines else "_Not provided._"


def _numbered(steps: list[str]) -> str:
    if not steps:
        return "_Not provided._"
    return "\n".join(f"{index}. {step}" for index, step in enumerate(steps, 1))


def _diagnostics_block(diagnostics: Mapping[str, Any] | None) -> str:
    if not diagnostics:
        return "_Diagnostics were not included._"
    try:
        encoded = json.dumps(dict(diagnostics), indent=2, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return "_Diagnostics could not be encoded._"
    if len(encoded) > MAX_DIAGNOSTICS_CHARS:
        encoded = encoded[: MAX_DIAGNOSTICS_CHARS - 1] + "…"
    return f"```json\n{encoded}\n```"


def _attachments_block(attachments: list[Mapping[str, Any]], *, url_base: str = "") -> str:
    if not attachments:
        return "_No screenshots attached._"
    lines: list[str] = []
    for index, item in enumerate(attachments, 1):
        label = str(item.get("label") or "").strip() or f"Screenshot {index}"
        route = str(item.get("route") or "").strip()
        digest = str(item.get("sha256") or "")
        size = item.get("bytes")
        detail = f"{label}"
        if route:
            detail += f" (route: `{route}`)"
        if isinstance(size, int):
            detail += f" — {size} bytes"
        if digest:
            detail += f", sha256 `{digest[:16]}…`"
        url = ""
        name = str(item.get("name") or "")
        if url_base and name:
            url = f"{url_base.rstrip('/')}/{name}"
        if url:
            lines.append(f"![{label}]({url}) — {detail}")
        else:
            lines.append(f"- {detail} _(image stored with the installation; not uploaded)_")
    return "\n".join(lines)


def build_issue_body(
    draft: Mapping[str, Any],
    *,
    diagnostics: Mapping[str, Any] | None = None,
    attachments: list[Mapping[str, Any]] | None = None,
    url_base: str = "",
) -> str:
    """The exact public markdown body shown in review and posted to GitHub."""
    meta = REPORT_TYPES[str(draft.get("type") or "bug")]
    steps = list(draft.get("steps") or [])
    body = "\n".join(
        [
            f"### Summary\n{draft.get('summary') or ''}",
            f"### What I was trying to do\n{draft.get('goal') or '_Not provided._'}",
            f"### Expected behavior\n{draft.get('expected') or '_Not provided._'}",
            f"### Actual behavior / error\n{draft.get('actual') or '_Not provided._'}",
            f"### Steps to reproduce\n{_numbered(steps)}",
            f"### Workaround\n{draft.get('workaround') or '_None found._'}",
            f"### Report type\n{meta['label']}",
            "### Diagnostics (automatic, redacted)\n"
            f"{_diagnostics_block(diagnostics)}",
            "### Screenshots\n"
            f"{_attachments_block(list(attachments or []), url_base=url_base)}",
        ]
    )
    reviewed = str(draft.get("reviewed_text") or "").strip()
    if reviewed:
        body += f"\n### Additional context (reviewed by reporter)\n{reviewed}"
    body += "\n\n---\n_Reported from the in-app **Report a bug** form._"
    if len(body) > MAX_BODY_CHARS:
        body = body[: MAX_BODY_CHARS - 1] + "…"
    return body


def fingerprint_for(draft: Mapping[str, Any]) -> str:
    """Stable duplicate-search fingerprint (type + normalized summary)."""
    summary = re.sub(r"[^a-z0-9]+", " ", str(draft.get("summary") or "").lower()).strip()
    material = f"{draft.get('type') or 'bug'}:{summary}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def search_terms_for(draft: Mapping[str, Any]) -> str:
    """A bounded GitHub search query fragment derived from the summary."""
    words = [
        word
        for word in re.findall(r"[A-Za-z0-9_.-]{3,}", str(draft.get("summary") or ""))
        if word.lower() not in {"the", "and", "for", "with", "that", "this", "when", "from", "into", "not", "but"}
    ]
    return " ".join(words[:6])


# ── attachment validation ────────────────────────────────────────────────


def sniff_image_type(data: bytes) -> str:
    """Return the MIME type from magic bytes, or "" when unsupported."""
    if not data:
        return ""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return ""


def validate_image(data: bytes, *, total_bytes: int, count: int) -> str:
    """Validate one screenshot; returns its sniffed MIME type."""
    if count >= MAX_ATTACHMENTS:
        raise FeedbackValidationError(
            "too_many_attachments", f"Attach at most {MAX_ATTACHMENTS} screenshots."
        )
    if not data:
        raise FeedbackValidationError("empty_attachment", "That screenshot file is empty.")
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise FeedbackValidationError(
            "attachment_too_large",
            f"Each screenshot must be {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB or smaller.",
        )
    if total_bytes + len(data) > MAX_TOTAL_ATTACHMENT_BYTES:
        raise FeedbackValidationError(
            "attachments_too_large",
            f"Keep the screenshots under {MAX_TOTAL_ATTACHMENT_BYTES // (1024 * 1024)} MB in total.",
        )
    content_type = sniff_image_type(data)
    if content_type not in ALLOWED_IMAGE_TYPES:
        raise FeedbackValidationError(
            "unsupported_attachment_type",
            "Screenshots must be PNG, JPEG, or WebP images.",
        )
    return content_type


def safe_route(value: Any) -> str:
    """Path-only route for diagnostics: query strings can carry tokens."""
    text = str(value or "").strip()
    if "#" in text:
        text = text.split("#", 1)[0]
    if "?" in text:
        text = text.split("?", 1)[0]
    text = redact_automatic_text(text, limit=MAX_ROUTE_CHARS)
    if not text.startswith("/"):
        return ""
    return text[:MAX_ROUTE_CHARS]