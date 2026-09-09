"""Regression: stream_agent_loop emits `rounds_exhausted` only when the round
cap is hit while still working, and NOT on a normal finish.

The decision is a `for/else` in the loop: the `else` runs only if no `break`
fired (break = done / budget / error). A refactor that adds a stray break or
return, or moves the done-break, could silently flip this. See PR #1999 / #1997.
"""

import asyncio
import json

import src.agent_loop as al
from src.mcp_manager import McpManager


_PORTAL_READ = "mcp__portal-fixture__portal.call_read_tool"


def _collect(gen):
    async def _run():
        return [c async for c in gen]
    return asyncio.run(_run())


def _types(chunks):
    out = []
    for c in chunks:
        if c.startswith("data: ") and not c.startswith("data: [DONE]"):
            try:
                out.append(json.loads(c[6:]))
            except Exception:
                pass
    return out


def _patch_common(monkeypatch):
    # Skip RAG/tool-index, MCP, and settings lookups; keep the real loop body,
    # _resolve_tool_blocks, and parse_tool_blocks.
    monkeypatch.setattr(al, "get_setting", lambda key, default=None: default, raising=False)
    monkeypatch.setattr(al, "get_mcp_manager", lambda: None, raising=False)
    monkeypatch.setattr(al, "estimate_tokens", lambda *a, **k: 10, raising=False)

    async def _fake_exec(block, *a, **k):
        return ("bash", {"output": "ok", "exit_code": 0})
    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)


def _portal_manager():
    manager = McpManager()
    manager._connections["portal-fixture"] = {
        "status": "connected",
        "name": "MAD MCP Portal",
        "server_info": {"name": "Fixture Broker"},
        "catalog_terms": ["Qdrant"],
        "instructions": (
            "Use portal.find_tools, portal.get_tool_reference, and "
            "portal.call_read_tool for downstream reads."
        ),
    }
    manager._tools["portal-fixture"] = [{
        "name": "portal.call_read_tool",
        "description": "Execute one typed downstream provider read.",
        "input_schema": {
            "type": "object",
            "properties": {
                "serviceId": {"type": "string"},
                "toolName": {"type": "string"},
                "arguments": {"type": "object"},
            },
            "required": ["serviceId", "toolName", "arguments"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    }]
    return manager


def _patch_portal_common(monkeypatch, manager, execute):
    monkeypatch.setattr(al, "get_setting", lambda key, default=None: default, raising=False)
    monkeypatch.setattr(al, "get_mcp_manager", lambda: manager, raising=False)
    monkeypatch.setattr(al, "blocked_tools_for_owner", lambda _owner: set(), raising=False)
    monkeypatch.setattr(al, "estimate_tokens", lambda *a, **k: 10, raising=False)
    monkeypatch.setattr(al, "execute_tool_block", execute, raising=False)


def _run_loop(monkeypatch, round_text, max_rounds=2, max_tool_calls=20):
    async def _fake_stream(_candidates, messages, **kwargs):
        yield f'data: {json.dumps({"delta": round_text})}\n\n'
        yield "data: [DONE]\n\n"
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    gen = al.stream_agent_loop(
        "http://x/v1", "m",
        [{"role": "user", "content": "do a long multi-step task"}],
        max_rounds=max_rounds,
        max_tool_calls=max_tool_calls,
        relevant_tools={"bash"},
    )
    return _types(_collect(gen))


def test_emits_rounds_exhausted_when_cap_hit_mid_task(monkeypatch):
    _patch_common(monkeypatch)
    # Every round returns a tool block -> never "done" -> loop exhausts the cap.
    events = _run_loop(monkeypatch, "```bash\necho hi\n```", max_rounds=2)
    assert any(e.get("type") == "rounds_exhausted" for e in events), events
    metrics = next(e["data"] for e in events if e.get("type") == "metrics")
    assert metrics["rounds_exhausted"] == 2


def test_persists_tool_budget_exhaustion_in_metrics(monkeypatch):
    _patch_common(monkeypatch)
    events = _run_loop(
        monkeypatch,
        "```bash\necho hi\n```",
        max_rounds=3,
        max_tool_calls=1,
    )

    assert any(e.get("type") == "budget_exceeded" for e in events), events
    metrics = next(e["data"] for e in events if e.get("type") == "metrics")
    assert metrics["tool_budget_exceeded"] == {"limit": 1, "used": 1}


def test_no_rounds_exhausted_on_normal_finish(monkeypatch):
    _patch_common(monkeypatch)
    # A plain answer (no tool block) -> done-break on round 1 -> no event.
    events = _run_loop(monkeypatch, "All done, here is your answer.", max_rounds=2)
    assert not any(e.get("type") == "rounds_exhausted" for e in events), events


def test_emits_intent_nudge_exhausted_when_cap_is_exhausted(monkeypatch):
    _patch_common(monkeypatch)

    events = _run_loop(monkeypatch, "Let me check the logs", max_rounds=5)

    guard = next((e for e in events if e.get("type") == "intent_nudge_exhausted"), None)
    assert guard is not None, events
    assert guard["reason"] == "intent_without_action_nudge_cap"
    assert guard["nudges"] == 2


def test_emits_loop_breaker_triggered_when_loop_breaker_trips(monkeypatch):
    _patch_common(monkeypatch)

    events = _run_loop(monkeypatch, "```bash\necho hi\n```", max_rounds=6)

    guard = next((e for e in events if e.get("type") == "loop_breaker_triggered"), None)
    assert guard is not None, events
    assert guard["reason"] == "loop_breaker_stall"


def test_empty_post_tool_completion_forces_a_final_answer(monkeypatch):
    _patch_common(monkeypatch)
    rounds = iter([
        "```bash\necho hi\n```",
        "",
        "Final answer from the tool result.",
    ])

    async def _fake_stream(_candidates, messages, **kwargs):
        text = next(rounds)
        if text:
            yield f'data: {json.dumps({"delta": text})}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    events = _types(_collect(al.stream_agent_loop(
        "http://x/v1", "m",
        [{"role": "user", "content": "do a long multi-step task"}],
        max_rounds=3,
        relevant_tools={"bash"},
    )))

    assert any(e.get("delta") == "Final answer from the tool result." for e in events), events
    assert not any(e.get("type") == "rounds_exhausted" for e in events), events


def test_explicit_portal_read_nudges_plain_prose_then_executes(monkeypatch):
    manager = _portal_manager()
    rounds = {"count": 0}
    executed = []
    first_messages = {}

    async def _fake_exec(block, *args, **kwargs):
        executed.append(block)
        return (
            f"mcp: {block.tool_type}",
            {
                "output": "20 bounded points returned",
                "structured_content": {"ok": True},
                "exit_code": 0,
            },
        )

    async def _fake_stream(_candidates, messages, **kwargs):
        rounds["count"] += 1
        if rounds["count"] == 1:
            first_messages["value"] = list(messages)
            yield 'data: {"delta":"The collection contains operational information."}\n\n'
        elif rounds["count"] == 2:
            call = {
                "id": "portal-read-1",
                "name": _PORTAL_READ,
                "arguments": json.dumps({
                    "serviceId": "qdrant",
                    "toolName": "qdrant-list-points",
                    "arguments": {
                        "collection_name": "jarvis-knowledgebase",
                        "limit": 20,
                        "include_payload": True,
                        "include_vectors": False,
                    },
                }),
            }
            yield f'data: {json.dumps({"type": "tool_calls", "calls": [call]})}\n\n'
        else:
            yield 'data: {"delta":"- Representative operational topic."}\n\n'
        yield "data: [DONE]\n\n"

    _patch_portal_common(monkeypatch, manager, _fake_exec)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    events = _types(_collect(al.stream_agent_loop(
        "https://api.openai.com/v1",
        "gpt-4o",
        [{
            "role": "user",
            "content": (
                "Use the MAD MCP Portal to tell me what information is in the "
                "jarvis-knowledgebase Qdrant collection. Query it with a bounded sample."
            ),
        }],
        owner="leo",
        max_rounds=4,
        context_length=8208,
    )))

    assert rounds["count"] == 3
    assert [block.tool_type for block in executed] == [_PORTAL_READ]
    assert any(event.get("type") == "tool_start" for event in events)
    assert not any(event.get("type") == "intent_nudge_exhausted" for event in events)
    assert "A prose-only response is not completion" in json.dumps(first_messages["value"])
    emitted_deltas = [event.get("delta", "") for event in events if "delta" in event]
    assert "The collection contains operational information." not in emitted_deltas
    assert "- Representative operational topic." in emitted_deltas
    metrics = next(event["data"] for event in events if event.get("type") == "metrics")
    assert metrics["round_texts"][0] == ""


def test_explicit_portal_read_guard_reports_exhaustion(monkeypatch):
    manager = _portal_manager()

    async def _fake_exec(*args, **kwargs):
        raise AssertionError("no tool call should be executed")

    async def _fake_stream(_candidates, messages, **kwargs):
        yield 'data: {"delta":"Here is a generic answer without a provider read."}\n\n'
        yield "data: [DONE]\n\n"

    _patch_portal_common(monkeypatch, manager, _fake_exec)
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    events = _types(_collect(al.stream_agent_loop(
        "https://api.openai.com/v1",
        "gpt-4o",
        [{
            "role": "user",
            "content": (
                "Use the MAD MCP Portal to query what is in the "
                "jarvis-knowledgebase Qdrant collection."
            ),
        }],
        owner="leo",
        max_rounds=5,
        context_length=8208,
    )))

    guard = next(
        event for event in events
        if event.get("type") == "intent_nudge_exhausted"
    )
    metrics = next(event["data"] for event in events if event.get("type") == "metrics")
    assert guard["reason"] == "explicit_portal_read_not_executed"
    assert guard["nudges"] == 2
    assert metrics["completion_guard"] == {
        "reason": "explicit_portal_read_not_executed",
        "nudges": 2,
    }
    emitted_deltas = [event.get("delta", "") for event in events if "delta" in event]
    assert "Here is a generic answer without a provider read." not in emitted_deltas
    assert emitted_deltas == [
        "I couldn't complete the requested live read: no Portal provider read "
        "was executed after two corrective attempts. I did not treat discovery "
        "or collection enumeration as the requested data."
    ]


def test_disabled_portal_read_executor_does_not_activate_impossible_guard(monkeypatch):
    manager = _portal_manager()
    rounds = {"count": 0}

    async def _fake_exec(*args, **kwargs):
        raise AssertionError("disabled tool should not execute")

    async def _fake_stream(_candidates, messages, **kwargs):
        rounds["count"] += 1
        yield 'data: {"delta":"The configured read executor is unavailable."}\n\n'
        yield "data: [DONE]\n\n"

    _patch_portal_common(monkeypatch, manager, _fake_exec)
    monkeypatch.setattr(
        al,
        "_load_mcp_disabled_map",
        lambda: {"portal-fixture": {"portal.call_read_tool"}},
        raising=False,
    )
    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    events = _types(_collect(al.stream_agent_loop(
        "https://api.openai.com/v1",
        "gpt-4o",
        [{
            "role": "user",
            "content": "Use MAD MCP Portal to query the Qdrant collection.",
        }],
        owner="leo",
        max_rounds=4,
        context_length=8208,
    )))

    assert rounds["count"] == 1
    assert any(
        event.get("delta") == "The configured read executor is unavailable."
        for event in events
    )
    assert not any(event.get("type") == "intent_nudge_exhausted" for event in events)


def test_tool_result_without_answer_gets_visible_failure_fallback():
    response, chunk = al._empty_response_fallback("", "hidden reasoning", [{"tool": "bash"}])

    assert "tool call completed" in response
    assert chunk is not None
    assert "hidden reasoning" not in response
