"""MAD-860: redacted model-response diagnosis and validation state.

The classifier is the single seam that turns an OpenAI-compatible completion
attempt into one of the documented categories. It must be deterministic,
redact endpoint/credential details, and choose a recovery action from the four
operator-visible options: validate settings, change model, change endpoint
mode, retry, or inspect a redacted diagnostic.
"""
import httpx
import pytest

from src import model_response_diagnosis as mrd


ALL_CATEGORIES = {
    "authentication",
    "url_http",
    "tls",
    "timeout",
    "unsupported_model",
    "stream_framing",
    "parser_failure",
    "zero_choices",
    "empty_deltas",
    "zero_content_completion",
}

VALID_ACTIONS = {
    "validate_settings",
    "change_model",
    "change_endpoint_mode",
    "retry",
    "inspect_diagnostic",
}


# ── HTTP status classification ────────────────────────────────────────────

@pytest.mark.parametrize("status,body,category", [
    (401, '{"error":{"message":"invalid api key"}}', "authentication"),
    (403, '{"error":"forbidden"}', "authentication"),
    (404, '{"error":{"message":"model gpt-ghost does not exist"}}', "unsupported_model"),
    (400, '{"error":{"message":"The model `ghost` does not exist"}}', "unsupported_model"),
    (404, '{"error":"not found"}', "url_http"),
    (429, '{"error":"rate limited"}', "url_http"),
    (500, 'boom', "url_http"),
    (302, '', "url_http"),
])
def test_http_status_categories(status, body, category):
    result = mrd.classify_http_status(status, body)
    assert result["category"] == category
    assert result["category"] in ALL_CATEGORIES
    assert result["action"] in VALID_ACTIONS
    assert result["guidance"]
    assert result["redacted"] is True


def test_http_classification_never_echoes_credentials_or_urls():
    result = mrd.classify_http_status(
        401,
        '{"error":"bad key sk-secret-abcdef1234567890 for https://api.example.com/v1"}',
    )
    blob = str(result)
    assert "sk-secret" not in blob
    assert "api.example.com" not in blob


# ── Exception classification ──────────────────────────────────────────────

def test_timeout_exception_is_timeout():
    result = mrd.classify_exception(httpx.ReadTimeout("read timed out"))
    assert result["category"] == "timeout"
    assert result["action"] == "retry"


def test_tls_exception_is_tls():
    result = mrd.classify_exception(httpx.ConnectError("SSL: CERTIFICATE_VERIFY_FAILED"))
    assert result["category"] == "tls"
    assert result["action"] == "validate_settings"


def test_plain_connect_error_is_url_http():
    result = mrd.classify_exception(httpx.ConnectError("Connection refused"))
    assert result["category"] == "url_http"


def test_stream_protocol_error_is_stream_framing_when_streaming():
    result = mrd.classify_exception(
        httpx.RemoteProtocolError("peer closed connection without sending complete message body"),
        streaming=True,
    )
    assert result["category"] == "stream_framing"
    assert result["action"] == "change_endpoint_mode"


def test_json_error_is_parser_failure():
    import json

    try:
        json.loads("{not json")
    except json.JSONDecodeError as exc:
        result = mrd.classify_exception(exc)
    assert result["category"] == "parser_failure"


# ── Completion payload classification ─────────────────────────────────────

def test_openai_compatible_gpt4o_mini_fixture_is_ok():
    payload = {
        "choices": [{
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": "OK"},
        }],
    }
    result = mrd.classify_completion_body(payload, model="gpt-4o-mini")
    assert result["category"] == "ok"
    assert result["action"] == "none"
    assert result["redacted"] is True


def test_zero_choices_is_reported():
    result = mrd.classify_completion_body({"choices": [], "model": "gpt-4o-mini"})
    assert result["category"] == "zero_choices"
    assert result["action"] == "retry"


def test_empty_content_completion_is_reported():
    payload = {"choices": [{"finish_reason": "stop", "message": {"content": ""}}]}
    result = mrd.classify_completion_body(payload)
    assert result["category"] == "zero_content_completion"
    assert result["action"] == "retry"


def test_reasoning_only_completion_counts_as_content():
    payload = {"choices": [{"message": {"content": "", "reasoning_content": "thinking"}}]}
    assert mrd.classify_completion_body(payload)["category"] == "ok"


def test_tool_call_only_completion_counts_as_content():
    payload = {"choices": [{"message": {"content": None, "tool_calls": [{"id": "1"}]}}]}
    assert mrd.classify_completion_body(payload)["category"] == "ok"


def test_anthropic_text_block_is_ok():
    payload = {"content": [{"type": "text", "text": "OK"}], "stop_reason": "end_turn"}
    assert mrd.classify_completion_body(payload)["category"] == "ok"


def test_anthropic_empty_content_is_zero_content():
    payload = {"content": [], "stop_reason": "end_turn"}
    assert mrd.classify_completion_body(payload)["category"] == "zero_content_completion"


def test_non_object_body_is_parser_failure():
    assert mrd.classify_completion_body(["nope"])["category"] == "parser_failure"


def test_completion_classification_redacts_private_output():
    payload = {"choices": [{"message": {"content": "my private diary sk-live-12345678901234567890"}}]}
    result = mrd.classify_completion_body(payload)
    blob = str(result)
    assert "diary" not in blob
    assert "sk-live" not in blob


# ── Stream framing classification ─────────────────────────────────────────

def test_stream_with_no_frames_is_framing_failure():
    result = mrd.classify_stream_frames([])
    assert result["category"] == "stream_framing"


def test_stream_of_non_sse_noise_is_framing_failure():
    result = mrd.classify_stream_frames(["<html>not a stream</html>", ""])
    assert result["category"] == "stream_framing"


def test_stream_of_broken_json_is_parser_failure():
    result = mrd.classify_stream_frames(["data: {not json", "data: also not json"])
    assert result["category"] == "parser_failure"


def test_stream_with_zero_choices_is_zero_choices():
    frames = [
        'data: {"id":"1","choices":[]}',
        'data: {"id":"2","choices":[]}',
        "data: [DONE]",
    ]
    assert mrd.classify_stream_frames(frames)["category"] == "zero_choices"


def test_stream_with_empty_deltas_is_empty_deltas():
    frames = [
        'data: {"choices":[{"delta":{"role":"assistant"}}]}',
        'data: {"choices":[{"delta":{}}]}',
        "data: [DONE]",
    ]
    assert mrd.classify_stream_frames(frames)["category"] == "empty_deltas"


def test_stream_with_content_delta_is_ok():
    frames = [
        'data: {"choices":[{"delta":{"content":"O"}}]}',
        'data: {"choices":[{"delta":{"content":"K"}}]}',
        "data: [DONE]",
    ]
    assert mrd.classify_stream_frames(frames)["category"] == "ok"


def test_stream_accepts_data_without_space():
    frames = ['data:{"choices":[{"delta":{"content":"OK"}}]}', "data:[DONE]"]
    assert mrd.classify_stream_frames(frames)["category"] == "ok"


# ── Guidance and actions ──────────────────────────────────────────────────

@pytest.mark.parametrize("category", sorted(ALL_CATEGORIES))
def test_every_category_has_actionable_guidance(category):
    text = mrd.recovery_guidance(category, request_id="req-123")
    assert text
    assert text.split(" ", 1)[0].rstrip("—-") in {
        "Validate", "Change", "Retry", "Inspect",
    }, text
    blob = text.lower()
    assert "jarvis" not in blob
    assert "leo" not in blob


def test_guidance_names_the_redacted_diagnostic_request_id():
    text = mrd.recovery_guidance("zero_content_completion", request_id="req-abc")
    assert "req-abc" in text


def test_guidance_actions_are_exact():
    assert mrd.action_for("authentication") == "validate_settings"
    assert mrd.action_for("unsupported_model") == "change_model"
    assert mrd.action_for("stream_framing") == "change_endpoint_mode"
    assert mrd.action_for("timeout") == "retry"
    assert mrd.action_for("parser_failure") == "inspect_diagnostic"


def test_diagnostic_payload_is_redacted_and_has_correlation():
    payload = mrd.diagnostic_payload(
        "zero_content_completion",
        request_id="req-1",
        provider="openai",
        model="gpt-4o-mini",
    )
    assert payload["category"] == "zero_content_completion"
    assert payload["request_id"] == "req-1"
    assert payload["provider"] == "openai"
    assert payload["model"] == "gpt-4o-mini"
    assert "url" not in payload
    assert "prompt" not in payload
    assert payload["redacted"] is True


# ── Validation persistence (settings-backed, reuses existing settings) ────

def _isolate_settings(monkeypatch, tmp_path):
    import src.settings as settings_module

    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(settings_module, "SETTINGS_FILE", settings_file)
    settings_module._invalidate_caches()
    return settings_file


def test_validation_records_configured_discovered_validated_and_failed(monkeypatch, tmp_path):
    _isolate_settings(monkeypatch, tmp_path)

    assert mrd.validation_state("ep-1", "gpt-4o-mini") == "configured"

    mrd.record_model_validation("ep-1", "gpt-4o-mini", "ok")
    assert mrd.validation_state("ep-1", "gpt-4o-mini") == "validated"

    mrd.record_model_validation("ep-2", "gpt-4o-mini", "authentication")
    state = mrd.validation_state("ep-2", "gpt-4o-mini")
    assert state == "failed"
    entry = mrd.validation_entry("ep-2", "gpt-4o-mini")
    assert entry["category"] == "authentication"
    assert entry["state"] == "failed"

    snapshot = mrd.validation_snapshot()
    assert "ep-1::gpt-4o-mini" in snapshot
    assert "ep-2::gpt-4o-mini" in snapshot
    assert snapshot["ep-1::gpt-4o-mini"]["validated_at"]


def test_validation_snapshot_caps_entries(monkeypatch, tmp_path):
    _isolate_settings(monkeypatch, tmp_path)
    monkeypatch.setattr(mrd, "_MAX_VALIDATION_ENTRIES", 3, raising=False)
    for index in range(6):
        mrd.record_model_validation("ep", f"model-{index}", "ok")
    assert len(mrd.validation_snapshot()) == 3