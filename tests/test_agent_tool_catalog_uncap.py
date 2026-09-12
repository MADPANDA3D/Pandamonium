"""MAD-905: API engines mount the full built-in catalog, and the toggle
catalog matches the callable catalog.

Live evidence (session 68b3b9db): the agent reported 23 mounted built-ins while
the Built-in Tools admin panel listed 80. `_relevant_tools` RAG-filtered
`FUNCTION_TOOL_SCHEMAS` for API-host models, so two-thirds of the catalog was
never in the payload. These tests pin the uncapped behavior: an API-host round
must offer every enabled built-in schema (only the measured tool-catalog budget
in `cap_tool_schemas` may trim), disabled tools stay out, and
`manage_settings action=list_tools` reports the real live catalog.
"""
from __future__ import annotations

import asyncio
import json

import src.agent_loop as al
import src.tool_security as ts
from src.agent_tools import TOOL_TAGS
from src.tool_security import BUILTIN_EMAIL_TOOLS
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS


def _schema_names() -> set[str]:
    return {s["function"]["name"] for s in FUNCTION_TOOL_SCHEMAS}


def _patch_loop(monkeypatch, captured: dict):
    # The catalog tests exercise an admin/operator turn (the live evidence
    # session was owner leo); public-user blocking has its own suites.
    monkeypatch.setattr(ts, "owner_is_admin_or_single_user", lambda owner: True, raising=False)

    async def _fake_stream(_candidates, messages, **kwargs):
        captured["tools"] = list(kwargs.get("tools") or [])
        yield 'data: {"delta": "Done."}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)
    monkeypatch.setattr(al, "get_setting", lambda key, default=None: default, raising=False)
    monkeypatch.setattr(al, "get_mcp_manager", lambda: None, raising=False)
    monkeypatch.setattr(al, "estimate_tokens", lambda *args, **kwargs: 10, raising=False)

    async def _fake_exec(block, *args, **kwargs):
        return block.tool_type, {"output": "ok", "exit_code": 0}

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)


def _run_loop(monkeypatch, captured: dict, *, disabled: set[str] | None = None):
    _patch_loop(monkeypatch, captured)

    async def _collect():
        return [
            chunk
            async for chunk in al.stream_agent_loop(
                "https://openrouter.ai/api/v1/chat/completions",
                "deepseek-v4-flash",
                [{"role": "user", "content": "How many built-in tools do you have?"}],
                max_rounds=1,
                relevant_tools={"bash"},
                disabled_tools=set(disabled or set()),
                owner="leo",
                context_length=200_000,
            )
        ]

    asyncio.run(_collect())


def _sent_names(captured: dict) -> set[str]:
    return {
        schema.get("function", {}).get("name")
        for schema in captured.get("tools", [])
        if schema.get("function", {}).get("name")
    }


def test_toggle_catalog_matches_callable_catalog():
    """Every toggle in /api/tools has a callable path and vice versa."""
    assert TOOL_TAGS == (_schema_names() | BUILTIN_EMAIL_TOOLS)


def test_api_host_receives_full_builtin_catalog(monkeypatch):
    captured: dict = {}
    _run_loop(monkeypatch, captured)
    sent = _sent_names(captured)
    expected = _schema_names()
    missing = expected - sent
    assert not missing, f"API round omitted built-in schemas: {sorted(missing)}"
    assert len(expected) >= 74, f"expected the full built-in catalog, found {len(expected)}"


def test_api_host_respects_disabled_tools(monkeypatch):
    captured: dict = {}
    _run_loop(monkeypatch, captured, disabled={"bash", "generate_image"})
    sent = _sent_names(captured)
    assert "bash" not in sent
    assert "generate_image" not in sent
    assert "web_search" in sent


def test_api_host_still_honors_intent_prunes(monkeypatch):
    """Uncapping the catalog must not override per-turn intent clamps."""
    captured: dict = {}
    _patch_loop(monkeypatch, captured)
    seen: dict = {}
    real_classifier = al.is_release_self_knowledge

    def _spy_classifier(text):
        result = real_classifier(text)
        seen["text"] = text
        seen["result"] = result
        return result

    monkeypatch.setattr(al, "is_release_self_knowledge", _spy_classifier, raising=False)
    clamp_seen: dict = {}
    real_clamp = al._clamp_release_self_knowledge_tools

    def _spy_clamp(flag, tools):
        out = real_clamp(flag, tools)
        clamp_seen.update(
            flag=flag,
            removed=sorted(set(tools) - set(out)),
            had_web=sorted(set(tools) & {"web_search", "web_fetch"}),
        )
        return out

    monkeypatch.setattr(al, "_clamp_release_self_knowledge_tools", _spy_clamp, raising=False)

    async def _collect():
        return [
            chunk
            async for chunk in al.stream_agent_loop(
                "https://openrouter.ai/api/v1/chat/completions",
                "deepseek-v4-flash",
                [{"role": "user", "content": (
                    "Search the web for the latest Pandamonium release notes "
                    "and summarize them."
                )}],
                max_rounds=1,
                relevant_tools={"web_search", "read_file"},
                owner="leo",
                context_length=200_000,
            )
        ]

    asyncio.run(_collect())
    assert seen.get("result") is True, seen
    sent = _sent_names(captured)
    assert "web_search" not in sent and "web_fetch" not in sent, (seen, clamp_seen, sorted(sent & {"web_search", "web_fetch"}))
    assert "get_runtime_status" in sent
    assert "read_file" in sent  # the rest of the catalog stays mounted


def test_list_tools_returns_real_catalog():
    from src.agent_tools.admin_tools import do_manage_settings

    result = asyncio.run(
        do_manage_settings(json.dumps({"action": "list_tools"}), owner=None)
    )
    assert result.get("exit_code") == 0
    entries = result.get("tools") or []
    ids = {entry.get("id") for entry in entries}
    assert ids == TOOL_TAGS, (
        "list_tools catalog must match the live toggle catalog; "
        f"missing={sorted(TOOL_TAGS - ids)}, extra={sorted(ids - TOOL_TAGS)}"
    )
    assert result.get("count") == len(TOOL_TAGS)
    for entry in entries:
        assert {"id", "category", "description", "enabled"} <= set(entry), entry
    native = [entry for entry in entries if entry["id"] in _schema_names()]
    assert all(entry["description"] for entry in native), "native tools need descriptions"
