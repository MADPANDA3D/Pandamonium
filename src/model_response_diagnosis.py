"""Redacted model-response diagnosis for OpenAI-compatible endpoints (MAD-860).

One classifier owns the mapping from a failed completions attempt to a stable,
user-safe category and a single recovery action. It deliberately reuses the
existing model probe and settings store instead of adding a second
provider-health subsystem: ``routes/model_routes.py`` feeds live results here,
``routes/setup_routes.py`` reads the persisted validation state, and
``src/agent_loop.py`` reuses the guidance for its runtime fallback.

Nothing in this module stores or returns credentials, prompts, private model
output, or endpoint URLs. Categories are fixed strings; failures are redacted
at the boundary.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

FAILURE_CATEGORIES = (
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
)

VALIDATION_KEY = "model_endpoint_validation"
_MAX_VALIDATION_ENTRIES = 100

_ACTIONS = {
    "authentication": "validate_settings",
    "url_http": "validate_settings",
    "tls": "validate_settings",
    "timeout": "retry",
    "unsupported_model": "change_model",
    "stream_framing": "change_endpoint_mode",
    "parser_failure": "inspect_diagnostic",
    "zero_choices": "retry",
    "empty_deltas": "retry",
    "zero_content_completion": "retry",
}

# Categories that are worth one bounded replay without operator input. Config
# errors (auth, unsupported model, TLS) are not retried automatically because
# a second identical request cannot succeed.
_REPLAY_SAFE = {
    "timeout",
    "url_http",
    "stream_framing",
    "parser_failure",
    "zero_choices",
    "empty_deltas",
    "zero_content_completion",
}

_MODEL_ERROR_RE = (
    "model not found", "model_not_found", "no such model", "does not exist",
    "unknown model", "unsupported model", "invalid model", "model is not",
    "not a valid model", "unrecognized model",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def action_for(category: str) -> str:
    """The single recovery action for a category (never raises)."""
    if category == "ok":
        return "none"
    return _ACTIONS.get(str(category), "retry")


def recovery_guidance(category: str, request_id: str = "") -> str:
    """One operator instruction: validate settings, change model/endpoint mode,
    retry, or inspect the redacted diagnostic."""
    rid = f" {request_id}" if request_id else ""
    return {
        "authentication": (
            "Validate settings — the endpoint rejected the credential. Check the API key "
            "for this endpoint, save it, and run the model test again."
        ),
        "url_http": (
            "Validate settings — the endpoint returned an HTTP error. Check the endpoint "
            "URL and mode, save, and run the model test again."
        ),
        "tls": (
            "Validate settings — the endpoint's TLS certificate could not be verified. "
            "Fix the endpoint URL or certificate, save, and run the model test again."
        ),
        "timeout": (
            "Retry — the endpoint did not answer in time. Send the request again; if it "
            "keeps timing out, check the server's load."
        ),
        "unsupported_model": (
            "Change model — this endpoint does not offer the selected model. Choose a "
            "different model or endpoint, save, and test again."
        ),
        "stream_framing": (
            "Change endpoint mode — the response stream framing was not understood. "
            "Switch this endpoint to non-streaming mode, save, and try again."
        ),
        "parser_failure": (
            f"Inspect the redacted diagnostic — the response could not be parsed. Check "
            f"the model diagnostic for request{rid or ' (no id)'}."
        ),
        "zero_choices": (
            "Retry — the endpoint returned no choices. Send the request again; if it "
            "repeats, change the endpoint mode and test again."
        ),
        "empty_deltas": (
            f"Retry — the stream carried no content deltas. Send the request again; if it "
            f"repeats, change endpoint mode and inspect the diagnostic for request{rid}."
        ),
        "zero_content_completion": (
            f"Retry — the model finished without any content. Send the request again; if "
            f"it repeats, choose a different model. Redacted diagnostic: request{rid}."
        ),
    }.get(str(category), f"Retry — the model did not return usable content. Redacted diagnostic: request{rid}.")


def _result(category: str, *, request_id: str = "", streaming: bool = False) -> dict[str, Any]:
    return {
        "category": category,
        "action": action_for(category),
        "guidance": recovery_guidance(category, request_id),
        "replay_safe": category in _REPLAY_SAFE and category != "ok",
        "retryable": category in _REPLAY_SAFE,
        "streaming": bool(streaming),
        "request_id": request_id,
        "redacted": True,
    }


def classify_http_status(status: int, body: Any = "") -> dict[str, Any]:
    """Classify a non-2xx HTTP response. The body is only pattern-matched; it
    is never returned, so credentials and URLs cannot leak through."""
    try:
        code = int(status)
    except (TypeError, ValueError):
        code = 0
    text = ""
    if isinstance(body, Mapping):
        text = str(body.get("error") or body.get("detail") or body.get("message") or "")
    else:
        text = str(body or "")
    lowered = text.lower()
    if code in (401, 403):
        category = "authentication"
    elif any(token in lowered for token in _MODEL_ERROR_RE):
        category = "unsupported_model"
    else:
        category = "url_http"
    result = _result(category, streaming=False)
    result["http_status"] = code
    return result


def classify_exception(exc: BaseException, *, streaming: bool = False) -> dict[str, Any]:
    """Classify a transport/parse exception without echoing its message."""
    name = type(exc).__name__
    message = str(exc).lower()
    try:
        import httpx
    except Exception:  # pragma: no cover - httpx is a hard dependency
        httpx = None  # type: ignore[assignment]

    category = "url_http"
    if httpx is not None:
        if isinstance(exc, httpx.TimeoutException):
            category = "timeout"
        elif isinstance(exc, getattr(httpx, "RemoteProtocolError", ())) or (
            streaming and isinstance(exc, getattr(httpx, "ProtocolError", ()))
        ):
            category = "stream_framing"
        elif isinstance(exc, httpx.ConnectError):
            if "ssl" in message or "certificate" in message or "tls" in message:
                category = "tls"
            else:
                category = "url_http"
        elif isinstance(exc, httpx.TooManyRedirects):
            category = "url_http"
    if category == "url_http" and name == "JSONDecodeError":
        category = "parser_failure"
    if category == "url_http" and ("ssl" in message or "certificate" in message):
        category = "tls"
    result = _result(category, streaming=streaming)
    result["exc_type"] = name[:80]
    return result


def _content_list_text(value: Any) -> str:
    if not isinstance(value, list):
        return value if isinstance(value, str) else ""
    parts = []
    for block in value:
        if isinstance(block, Mapping):
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


def classify_completion_body(body: Any, *, model: str = "") -> dict[str, Any]:
    """Classify a non-streaming completion body.

    Handles OpenAI-compatible ``choices`` payloads and Anthropic-style
    ``content`` blocks. Only the shape is inspected; content is never copied
    into the result.
    """
    if not isinstance(body, Mapping):
        result = _result("parser_failure")
        result["model"] = str(model or "")[:120]
        return result

    if "choices" in body:
        choices = body.get("choices")
        if not isinstance(choices, list):
            return _result("parser_failure")
        if not choices:
            return _result("zero_choices")
        first = choices[0] if choices and isinstance(choices[0], Mapping) else None
        if first is None:
            return _result("parser_failure")
        message = first.get("message")
        if not isinstance(message, Mapping):
            message = first.get("delta") if isinstance(first.get("delta"), Mapping) else {}
        content = message.get("content")
        if isinstance(content, list):
            content = _content_list_text(content)
        reasoning = (
            message.get("reasoning_content") or message.get("reasoning")
            or message.get("thinking") or ""
        )
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            return _result("ok")
        if isinstance(content, str) and content.strip():
            return _result("ok")
        if isinstance(reasoning, str) and reasoning.strip():
            # Reasoning-only responses still carried model output; the runtime
            # already treats them as the answer (llm_core reasoning fallback).
            return _result("ok")
        return _result("zero_content_completion")

    if "content" in body:
        content = _content_list_text(body.get("content"))
        if not content and body.get("content") is None:
            content = ""
        if isinstance(content, str) and content.strip():
            return _result("ok")
        return _result("zero_content_completion")

    return _result("parser_failure")


def classify_stream_frames(frames: Iterable[str]) -> dict[str, Any]:
    """Classify the raw SSE frames of a streaming completion attempt.

    ``frames`` are the lines/strings read from the stream (with or without the
    ``data:`` prefix). Framing and parser failures are distinguished so the UI
    can tell the operator whether to change endpoint mode or inspect a
    diagnostic.
    """
    streamed = [str(frame) for frame in (frames or [])]
    data_lines: list[str] = []
    for frame in streamed:
        for line in frame.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                payload = line[5:].strip()
                if payload and payload != "[DONE]":
                    data_lines.append(payload)
    if not data_lines:
        return _result("stream_framing", streaming=True)

    parsed = 0
    any_choices_key = False
    any_choice = False
    saw_content = False
    for payload in data_lines:
        if not payload.startswith(("{", "[")):
            continue
        try:
            import json

            chunk = json.loads(payload)
        except Exception:
            continue
        parsed += 1
        if not isinstance(chunk, Mapping):
            continue
        choices = chunk.get("choices")
        if choices is None:
            text = chunk.get("text")
            if isinstance(text, str) and text:
                saw_content = True
            continue
        any_choices_key = True
        if not isinstance(choices, list):
            continue
        for choice in choices:
            if not isinstance(choice, Mapping):
                continue
            any_choice = True
            delta = choice.get("delta") if isinstance(choice.get("delta"), Mapping) else {}
            if not delta:
                delta = choice.get("message") if isinstance(choice.get("message"), Mapping) else {}
            content = delta.get("content")
            if isinstance(content, list):
                content = _content_list_text(content)
            if isinstance(content, str) and content:
                saw_content = True
            if delta.get("reasoning_content") or delta.get("reasoning") or delta.get("thinking"):
                saw_content = True
            if delta.get("tool_calls"):
                saw_content = True
    if parsed == 0:
        return _result("parser_failure", streaming=True)
    if saw_content:
        return _result("ok", streaming=True)
    if not any_choices_key:
        return _result("parser_failure", streaming=True)
    if not any_choice:
        return _result("zero_choices", streaming=True)
    return _result("empty_deltas", streaming=True)


def diagnostic_payload(
    category: str,
    *,
    request_id: str = "",
    provider: str = "",
    model: str = "",
) -> dict[str, Any]:
    """Redacted correlation payload for telemetry and the UI card."""
    return {
        "type": "model_response_diagnostic",
        "category": str(category)[:64],
        "action": action_for(category),
        "guidance": recovery_guidance(category, request_id),
        "request_id": str(request_id or ""),
        "provider": str(provider or ""),
        "model": str(model or ""),
        "redacted": True,
    }


# ── Validation persistence (existing settings store, no new subsystem) ────

def _validation_key(endpoint_id: Any, model_id: Any) -> str:
    return f"{str(endpoint_id)}::{str(model_id)}"


def _load_store() -> dict[str, Any]:
    try:
        from src.settings import load_settings

        raw = load_settings().get(VALIDATION_KEY)
    except Exception:
        raw = None
    if not isinstance(raw, dict):
        return {}
    return {
        str(key): dict(value)
        for key, value in raw.items()
        if isinstance(value, dict)
    }


def validation_snapshot() -> dict[str, dict[str, Any]]:
    return _load_store()


def validation_entry(endpoint_id: Any, model_id: Any) -> dict[str, Any] | None:
    return _load_store().get(_validation_key(endpoint_id, model_id))


def validation_state(endpoint_id: Any, model_id: Any) -> str:
    """configured -> validated | failed for one endpoint/model pair."""
    entry = validation_entry(endpoint_id, model_id)
    if not entry:
        return "configured"
    if entry.get("state") == "validated" or entry.get("category") == "ok":
        return "validated"
    return "failed"


def record_model_validation(
    endpoint_id: Any,
    model_id: Any,
    category: str,
    *,
    at: str | None = None,
) -> dict[str, Any]:
    """Persist the latest validation result next to the existing settings."""
    from src.settings import load_settings, save_settings

    entry = {
        "state": "validated" if category == "ok" else "failed",
        "category": str(category)[:64],
        "action": action_for(category),
        "validated_at": at or _now_iso(),
    }
    try:
        settings = load_settings()
        store = settings.get(VALIDATION_KEY)
        if not isinstance(store, dict):
            store = {}
        store[_validation_key(endpoint_id, model_id)] = entry
        if len(store) > _MAX_VALIDATION_ENTRIES:
            ordered = sorted(
                store.items(),
                key=lambda item: str((item[1] or {}).get("validated_at") or ""),
                reverse=True,
            )
            store = dict(ordered[:_MAX_VALIDATION_ENTRIES])
        settings[VALIDATION_KEY] = store
        save_settings(settings)
    except Exception:
        # Validation persistence is observability-adjacent: never break a probe.
        return entry
    return entry
