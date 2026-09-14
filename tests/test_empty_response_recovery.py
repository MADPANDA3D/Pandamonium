"""MAD-860: safe bounded recovery for zero-content model responses.

One failed request must produce one chronological actionable result. A replay
may only run when no tool has executed (so no external side effect can be
duplicated); otherwise the UI gets an explicit retry instruction and no replay.
"""
import asyncio
import json

import pytest

import src.agent_loop as al


def _collect(gen):
    async def _run():
        return [chunk async for chunk in gen]
    return asyncio.run(_run())


def _events(chunks):
    events = []
    for chunk in chunks:
        if chunk.startswith("data: ") and not chunk.startswith("data: [DONE]"):
            try:
                events.append(json.loads(chunk[6:]))
            except Exception:
                pass
    return events


def _patch_common(monkeypatch):
    monkeypatch.setattr(al, "get_setting", lambda key, default=None: default, raising=False)
    monkeypatch.setattr(al, "get_mcp_manager", lambda: None, raising=False)
    monkeypatch.setattr(al, "estimate_tokens", lambda *args, **kwargs: 10, raising=False)

    async def _fake_exec(block, *args, **kwargs):
        return "bash", {"output": "ok", "exit_code": 0}

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)


def _empty_stream(monkeypatch):
    async def _fake_stream(_candidates, messages, **kwargs):
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)


def _empty_response_fallback(**kwargs):
    return al._empty_response_fallback(**kwargs)


# ── The sync formatter: one actionable result ────────────────────────────

def test_empty_fallback_names_recovery_action_and_request_id():
    response, chunk = _empty_response_fallback(
        full_response="",
        round_reasoning="",
        tool_events=[],
        category="zero_content_completion",
        request_id="req-xyz",
    )
    assert chunk is not None
    assert "Retry" in response
    assert "req-xyz" in response
    body = json.loads(chunk[6:])
    assert body["delta"] == response


def test_empty_fallback_uses_validate_settings_for_authentication():
    response, _ = _empty_response_fallback(
        full_response="",
        round_reasoning="",
        tool_events=[],
        category="authentication",
        request_id="req-auth",
    )
    assert "Validate settings" in response


def test_tool_result_without_answer_offers_explicit_retry_and_never_replays():
    response, chunk = _empty_response_fallback(
        full_response="",
        round_reasoning="hidden reasoning",
        tool_events=[{"tool": "bash"}],
        category="zero_content_completion",
        request_id="req-tool",
    )
    assert "tool call completed" in response
    assert "retry" in response.lower()
    assert "hidden reasoning" not in response
    assert chunk is not None


def test_reasoning_only_round_still_emits_nothing():
    response, chunk = _empty_response_fallback(
        full_response="",
        round_reasoning="I reasoned carefully",
        tool_events=[],
        category="zero_content_completion",
        request_id="req-1",
    )
    assert chunk is None
    assert response == "I reasoned carefully"


def test_guidance_has_no_private_identity_names():
    response, _ = _empty_response_fallback(
        full_response="",
        round_reasoning="",
        tool_events=[],
        category="zero_content_completion",
        request_id="req-1",
    )
    lowered = response.lower()
    assert "jarvis" not in lowered
    assert "leo" not in lowered


# ── stream_agent_loop: bounded recovery with no tools ────────────────────

def test_empty_response_is_recovered_once_without_tools(monkeypatch):
    _patch_common(monkeypatch)
    _empty_stream(monkeypatch)
    calls = []

    async def _fake_replay(url, model, messages, **kwargs):
        calls.append((url, model, messages))
        return "Recovered final answer."

    import src.llm_core as llm_core
    monkeypatch.setattr(llm_core, "llm_call_async", _fake_replay)

    chunks = _collect(al.stream_agent_loop(
        "http://x/v1", "m",
        [{"role": "user", "content": "hello"}],
        max_rounds=2,
        relevant_tools={"bash"},
    ))
    events = _events(chunks)
    deltas = [e.get("delta") for e in events if e.get("delta")]
    assert "Recovered final answer." in deltas
    assert len(calls) == 1
    assert not any(e.get("type") == "model_response_diagnostic" for e in events)


def test_failed_replay_emits_one_diagnostic_and_one_result(monkeypatch):
    _patch_common(monkeypatch)
    _empty_stream(monkeypatch)

    async def _fake_replay(url, model, messages, **kwargs):
        return ""

    import src.llm_core as llm_core
    monkeypatch.setattr(llm_core, "llm_call_async", _fake_replay)
    recorded = []
    monkeypatch.setattr(
        al, "record_operational_event",
        lambda **kwargs: recorded.append(kwargs), raising=False,
    )

    chunks = _collect(al.stream_agent_loop(
        "http://x/v1", "m",
        [{"role": "user", "content": "hello"}],
        max_rounds=2,
        relevant_tools={"bash"},
    ))
    events = _events(chunks)
    diagnostics = [e for e in events if e.get("type") == "model_response_diagnostic"]
    deltas = [e.get("delta") for e in events if e.get("delta")]
    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic["category"] in {
        "zero_choices", "zero_content_completion", "empty_deltas",
        "stream_framing", "parser_failure",
    }
    assert diagnostic["request_id"]
    assert diagnostic["guidance"]
    # One chronological result: exactly one user-facing empty-response line.
    empty_lines = [d for d in deltas if "empty" in (d or "").lower()]
    assert len(deltas) == 1
    assert len(empty_lines) == 1
    # Runtime telemetry keeps the redacted category, never the endpoint/prompt.
    response_events = [row for row in recorded if row.get("event_type") == "response"]
    assert response_events, recorded
    metadata = response_events[-1].get("metadata") or {}
    assert metadata.get("finish_category") == diagnostic["category"]
    assert metadata.get("diagnostic_id") == diagnostic["request_id"]
    blob = json.dumps(recorded)
    assert "http://x/v1" not in blob
    assert "hello" not in blob


def test_replay_is_skipped_when_a_tool_has_run(monkeypatch):
    _patch_common(monkeypatch)
    calls = []

    async def _fake_replay(url, model, messages, **kwargs):
        calls.append(url)
        return "should not run"

    import src.llm_core as llm_core
    monkeypatch.setattr(llm_core, "llm_call_async", _fake_replay)

    rounds = iter([True, False])

    async def _fake_stream(_candidates, messages, **kwargs):
        if next(rounds, False):
            yield (
                "data: "
                + json.dumps({
                    "type": "tool_calls",
                    "calls": [{
                        "id": "c1",
                        "name": "bash",
                        "arguments": json.dumps({"command": "echo hi"}),
                    }],
                })
                + "\n\n"
            )
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    chunks = _collect(al.stream_agent_loop(
        "http://x/v1", "m",
        [{"role": "user", "content": "run it"}],
        max_rounds=2,
        relevant_tools={"bash"},
        extra_tool_schemas=[{
            "type": "function",
            "function": {
                "name": "bash",
                "description": "b",
                "parameters": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                },
            },
        }],
    ))
    events = _events(chunks)
    assert calls == [], "a replay after a tool ran could duplicate its side effect"
    deltas = [e.get("delta") for e in events if e.get("delta")]
    assert any("retry" in (d or "").lower() for d in deltas)


def test_intermittent_empty_response_recovers_on_replay(monkeypatch):
    _patch_common(monkeypatch)
    _empty_stream(monkeypatch)

    async def _fake_replay(url, model, messages, **kwargs):
        return "OK"

    import src.llm_core as llm_core
    monkeypatch.setattr(llm_core, "llm_call_async", _fake_replay)

    chunks = _collect(al.stream_agent_loop(
        "http://x/v1", "m",
        [{"role": "user", "content": "Say OK"}],
        max_rounds=1,
        relevant_tools=set(),
    ))
    events = _events(chunks)
    deltas = [e.get("delta") for e in events if e.get("delta")]
    assert deltas == ["OK"]
    assert not any(e.get("type") == "model_response_diagnostic" for e in events)