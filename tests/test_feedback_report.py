"""MAD-856: redaction, validation, and exact issue construction for bug reports."""
from __future__ import annotations

import json

import pytest

from src import feedback_diagnostics as diagnostics
from src import feedback_report as report


def _draft(**overrides):
    base = {
        "draft_id": "draft-abc12345",
        "type": "bug",
        "summary": "Chat send button does nothing",
        "goal": "Send a message",
        "expected": "The message sends",
        "actual": "Nothing happens",
        "steps": ["Open a chat", "Type hello", "Click send"],
        "workaround": "Reload the page",
        "reviewed_text": "",
        "route": "/static/index.html#chat",
        "client": {"user_agent_class": "Chrome 140", "viewport_class": "desktop", "locale": "en-US"},
        "include_diagnostics": True,
        "attachment_ids": [],
    }
    base.update(overrides)
    return base


# ── validation ───────────────────────────────────────────────────────────


def test_validate_draft_normalizes_and_bounds():
    draft = report.validate_draft(_draft())
    assert draft["type"] == "bug"
    assert draft["steps"] == ["Open a chat", "Type hello", "Click send"]
    assert draft["route"] == "/static/index.html#chat"
    assert draft["include_diagnostics"] is True


def test_validate_draft_rejects_short_summary_and_bad_types():
    with pytest.raises(report.FeedbackValidationError) as exc:
        report.validate_draft(_draft(summary="a"))
    assert exc.value.code == "missing_summary"
    with pytest.raises(report.FeedbackValidationError) as exc:
        report.validate_draft(_draft(type="exploit"))
    assert exc.value.code == "invalid_type"


def test_validate_draft_rejects_hostile_draft_ids():
    for bad in ("../etc/passwd", "a", "x" * 65, "draft id"):
        with pytest.raises(report.FeedbackValidationError):
            report.validate_draft(_draft(draft_id=bad))


def test_steps_are_capped_and_rejected_when_too_many():
    with pytest.raises(report.FeedbackValidationError) as exc:
        report.validate_draft(_draft(steps=[f"step {i}" for i in range(report.MAX_STEPS + 1)]))
    assert exc.value.code == "too_many_steps"
    with pytest.raises(report.FeedbackValidationError):
        report.validate_draft(_draft(steps=["x" * (report.MAX_STEP_CHARS + 1)]))


def test_attachment_ids_are_validated_and_unique():
    draft = report.validate_draft(_draft(attachment_ids=["att-12345678", "att-12345678", "att-87654321"]))
    assert draft["attachments"] == ["att-12345678", "att-87654321"]
    with pytest.raises(report.FeedbackValidationError):
        report.validate_draft(_draft(attachment_ids=["../evil"]))
    with pytest.raises(report.FeedbackValidationError):
        report.validate_draft(_draft(attachment_ids=[f"att-{i:08d}" for i in range(report.MAX_ATTACHMENTS + 1)]))


# ── redaction ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "secret_text,canary",
    [
        ("Authorization: Bearer " + "c" * 24, "c" * 24),
        ("api_key=" + "a" * 20, "a" * 20),
        ("password: " + "b" * 16, "b" * 16),
        ("ghp_" + "d" * 36, "d" * 36),
        ("token=" + "e" * 24, "e" * 24),
    ],
)
def test_secret_canaries_are_redacted_from_report_text(secret_text, canary):
    redacted = report.redact_report_text(secret_text)
    assert canary not in redacted
    assert "[redacted]" in redacted


def test_url_userinfo_and_secret_query_are_redacted():
    text = "Fetching https://user:pass@example.com/path?token=abc12345&x=1 failed"
    redacted = report.redact_report_text(text)
    assert "user:pass" not in redacted
    assert "abc12345" not in redacted


def test_validate_draft_inherits_redaction_into_review_text():
    draft = report.validate_draft(
        _draft(actual="Login failed with password: " + "h" * 16, summary="Login fails")
    )
    assert "h" * 16 not in draft["actual"]


def test_automatic_redaction_strips_paths_hosts_urls_and_emails():
    text = (
        "at /home/leo/secret/app.py via https://internal.example.com/hook "
        "with 192.168.1.20 and leo@example.com"
    )
    redacted = report.redact_automatic_text(text)
    assert "/home/leo/secret" not in redacted
    assert "internal.example.com" not in redacted
    assert "192.168.1.20" not in redacted
    assert "leo@example.com" not in redacted
    assert "[path redacted]" in redacted and "[host redacted]" in redacted
    assert "[url redacted]" in redacted and "[email redacted]" in redacted


def test_safe_route_drops_query_and_fragment():
    assert report.safe_route("/static/index.html#chat?token=abc123") == "/static/index.html"
    assert report.safe_route("javascript:alert(1)") == ""


# ── title / body ─────────────────────────────────────────────────────────


def test_issue_title_uses_the_type_prefix():
    assert report.build_issue_title(_draft()) == "[Bug] Chat send button does nothing"
    assert report.build_issue_title(_draft(type="requested_fix")).startswith("[Fix]")
    assert report.build_issue_title(_draft(type="security")).startswith("[Security]")


def test_issue_body_contains_every_reviewed_field_in_order():
    draft = report.validate_draft(_draft())
    body = report.build_issue_body(
        draft,
        diagnostics={"included": True, "app": {"version": "1.0.62"}},
        attachments=[
            {
                "id": "att-1",
                "name": "shot.png",
                "label": "Step 2 - error dialog",
                "route": "/static/index.html",
                "bytes": 1234,
                "sha256": "abc123",
            }
        ],
    )
    for heading in (
        "### Summary",
        "### What I was trying to do",
        "### Expected behavior",
        "### Actual behavior / error",
        "### Steps to reproduce",
        "### Workaround",
        "### Diagnostics (automatic, redacted)",
        "### Screenshots",
    ):
        assert heading in body
    assert "1. Open a chat" in body
    assert "Step 2 - error dialog" in body
    assert "1.0.62" in body
    assert "_Reported from the in-app **Report a bug** form._" in body


def test_issue_body_is_deterministic_for_review_and_submit():
    draft = report.validate_draft(_draft())
    first = report.build_issue_body(draft, diagnostics={"included": False}, attachments=[])
    second = report.build_issue_body(draft, diagnostics={"included": False}, attachments=[])
    assert first == second


def test_issue_body_caps_length():
    draft = report.validate_draft(_draft(reviewed_text="x" * report.MAX_REVIEWED_TEXT_CHARS))
    body = report.build_issue_body(draft, diagnostics=None, attachments=[])
    assert len(body) <= report.MAX_BODY_CHARS


def test_attachment_url_base_embeds_public_links():
    body = report.build_issue_body(
        report.validate_draft(_draft()),
        diagnostics=None,
        attachments=[{"name": "a.png", "label": "First", "bytes": 10, "sha256": "d"}],
        url_base="https://feedback.example.dev/evidence",
    )
    assert "https://feedback.example.dev/evidence/a.png" in body


def test_fingerprint_and_search_terms_are_stable():
    draft = report.validate_draft(_draft())
    assert report.fingerprint_for(draft) == report.fingerprint_for(report.validate_draft(_draft()))
    assert "Chat" in report.search_terms_for(draft)
    assert "the" not in report.search_terms_for(_draft(summary="the the the button"))


# ── attachment sniffing ──────────────────────────────────────────────────


def test_sniff_image_type_accepts_only_images():
    assert report.sniff_image_type(b"\x89PNG\r\n\x1a\nrest") == "image/png"
    assert report.sniff_image_type(b"\xff\xd8\xff\xe0rest") == "image/jpeg"
    assert report.sniff_image_type(b"RIFF\x00\x00\x00\x00WEBPrest") == "image/webp"
    assert report.sniff_image_type(b"<svg></svg>") == ""
    assert report.sniff_image_type(b"GIF89a") == ""
    assert report.sniff_image_type(b"") == ""


def test_validate_image_enforces_type_size_and_count():
    png = b"\x89PNG\r\n\x1a\n" + b"x" * 32
    assert report.validate_image(png, total_bytes=0, count=0) == "image/png"
    with pytest.raises(report.FeedbackValidationError) as exc:
        report.validate_image(b"MZ\x90\x00", total_bytes=0, count=0)
    assert exc.value.code == "unsupported_attachment_type"
    with pytest.raises(report.FeedbackValidationError) as exc:
        report.validate_image(b"\x89PNG\r\n\x1a\n" + b"x" * (report.MAX_ATTACHMENT_BYTES + 1), total_bytes=0, count=0)
    assert exc.value.code == "attachment_too_large"
    with pytest.raises(report.FeedbackValidationError) as exc:
        report.validate_image(png, total_bytes=0, count=report.MAX_ATTACHMENTS)
    assert exc.value.code == "too_many_attachments"
    with pytest.raises(report.FeedbackValidationError) as exc:
        report.validate_image(png, total_bytes=report.MAX_TOTAL_ATTACHMENT_BYTES, count=0)
    assert exc.value.code == "attachments_too_large"


# ── diagnostics allowlist ────────────────────────────────────────────────


def test_diagnostics_are_allowlisted_and_never_include_user_content():
    bundle = diagnostics.build_diagnostics(
        route="/static/index.html#chat?token=should-not-appear",
        session_id="session-1234",
        request_id="req-5678",
        owner="leo",
        client={
            "user_agent_class": "Chrome 140",
            "viewport_class": "desktop",
            "locale": "en-US",
            "platform_class": "macos",
        },
    )
    encoded = json.dumps(bundle)
    assert "should-not-appear" not in encoded
    assert bundle["route"] == "/static/index.html"
    assert set(bundle) == {
        "included", "generated_at", "app", "route", "client",
        "session", "correlation", "latest_error", "service_health",
    }
    assert set(bundle["app"]) == {
        "version", "revision", "installation_method", "runtime", "platform_class", "platform_release",
    }
    assert bundle["service_health"]["api"]["status"] == "healthy"


def test_diagnostics_can_be_excluded():
    assert diagnostics.build_diagnostics(include_diagnostics=False) == {"included": False}


def test_diagnostics_omits_invalid_session_and_request_ids():
    bundle = diagnostics.build_diagnostics(session_id="../etc", request_id="has space")
    assert bundle["session"] == {}
    assert bundle["correlation"] == {}


def test_latest_error_is_classified_and_redacted(monkeypatch):
    from src.operational_protocol import ProtocolEventStore

    temp = ProtocolEventStore(path=__import__("tempfile").mktemp(suffix=".jsonl"))
    temp.record(
        actor="leo",
        component="agent_loop",
        event_type="result",
        status="failed",
        session_id="session-1234",
        operator_id="leo",
        error={"category": "provider_error", "detail": "api_key=sk-secret-should-not-leak"},
    )
    import src.operational_protocol as op

    monkeypatch.setattr(op, "events", temp)
    bundle = diagnostics.build_diagnostics(session_id="session-1234", owner="leo")
    assert bundle["latest_error"]["category"] == "provider_error"
    assert "sk-secret-should-not-leak" not in json.dumps(bundle)