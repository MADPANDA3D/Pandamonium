"""MAD-907: local/text engines can discover and mount any enabled built-in.

API engines mount the full catalog (MAD-905). Text engines still receive only
the RAG-selected fenced-block prompt, so `manage_settings action=load_tools`
mounts requested tools into the request's tool selection; the tool result also
carries the exact fenced-block section so a local model can format the call
immediately. Mounts last for the remainder of the request only.
"""
from __future__ import annotations

import asyncio
import json

import src.agent_loop as al
import src.tool_security as ts
from src.agent_tools.admin_tools import do_manage_settings
from src.tool_catalog import resolve_tool_mounts


def test_resolve_tool_mounts_classifies_known_unknown_disabled():
    result = resolve_tool_mounts(["grep", "nope_tool", "bash"], disabled={"bash"})
    assert result == {
        "mounted_tools": ["grep"],
        "unknown_tools": ["nope_tool"],
        "disabled_tools": ["bash"],
    }


def test_resolve_tool_mounts_dedupes_and_ignores_blanks():
    result = resolve_tool_mounts(["grep", " grep ", "", None, "grep"])
    assert result["mounted_tools"] == ["grep"]
    assert result["unknown_tools"] == []
    assert result["disabled_tools"] == []


def test_load_tools_mounts_and_returns_sections():
    result = asyncio.run(
        do_manage_settings(
            json.dumps({"action": "load_tools", "tools": ["grep", "manage_research"]}),
            owner=None,
        )
    )
    assert result.get("exit_code") == 0
    assert set(result.get("mounted_tools") or []) == {"grep", "manage_research"}
    assert not result.get("unknown_tools")
    # Text engines need the exact fenced-block syntax in the model-visible text.
    assert "```manage_research" in result.get("response", "")
    assert result.get("tool_sections")


def test_load_tools_reports_unknown_without_failing():
    result = asyncio.run(
        do_manage_settings(
            json.dumps({"action": "load_tools", "tools": ["definitely_not_a_tool"]}),
            owner=None,
        )
    )
    assert result.get("exit_code") == 0
    assert result.get("mounted_tools") == []
    assert result.get("unknown_tools") == ["definitely_not_a_tool"]
    assert "definitely_not_a_tool" in result.get("response", "")


def test_load_tools_cannot_mount_disabled_tools(monkeypatch):
    import src.settings as settings

    real_get_setting = settings.get_setting

    def _fake_get_setting(key, default=None):
        if key == "disabled_tools":
            return ["bash"]
        return real_get_setting(key, default)

    monkeypatch.setattr(settings, "get_setting", _fake_get_setting)
    result = asyncio.run(
        do_manage_settings(
            json.dumps({"action": "load_tools", "tools": ["bash", "grep"]}),
            owner=None,
        )
    )
    assert result.get("mounted_tools") == ["grep"]
    assert result.get("disabled_tools") == ["bash"]


def test_mount_hook_recovers_budget_capped_tool(monkeypatch):
    """A mounted tool joins the next round's schemas even when the measured
    budget capped it out of the catalog."""
    monkeypatch.setattr(ts, "owner_is_admin_or_single_user", lambda owner: True, raising=False)
    monkeypatch.setattr(al, "get_setting", lambda key, default=None: default, raising=False)
    monkeypatch.setattr(al, "get_mcp_manager", lambda: None, raising=False)
    monkeypatch.setattr(al, "estimate_tokens", lambda *args, **kwargs: 10, raising=False)

    import src.context_budget as context_budget

    monkeypatch.setattr(
        context_budget, "model_input_token_budget", lambda model: 6000, raising=False
    )

    frames: list[set[str]] = []

    async def _fake_stream(_candidates, messages, **kwargs):
        frames.append(
            {
                schema.get("function", {}).get("name")
                for schema in (kwargs.get("tools") or [])
                if schema.get("function", {}).get("name")
            }
        )
        if len(frames) == 1:
            calls = [{
                "id": "call_mount_1",
                "name": "manage_settings",
                "arguments": json.dumps(
                    {"action": "load_tools", "tools": ["manage_research"]}
                ),
            }]
            yield f"data: {json.dumps({'type': 'tool_calls', 'calls': calls})}\n\n"
        else:
            yield 'data: {"delta": "Done."}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(al, "stream_llm_with_fallback", _fake_stream, raising=False)

    async def _fake_exec(block, *args, **kwargs):
        if block.tool_type == "manage_settings":
            return "manage_settings", {"mounted_tools": ["manage_research"], "exit_code": 0}
        return block.tool_type, {"output": "ok", "exit_code": 0}

    monkeypatch.setattr(al, "execute_tool_block", _fake_exec, raising=False)

    async def _collect():
        return [
            chunk
            async for chunk in al.stream_agent_loop(
                "https://openrouter.ai/api/v1/chat/completions",
                "deepseek-v4-flash",
                [{"role": "user", "content": "look at the local project"}],
                max_rounds=3,
                relevant_tools={"web_search"},
                owner="leo",
                workspace="/tmp",
                context_length=4000,
            )
        ]

    asyncio.run(_collect())
    assert len(frames) >= 2, frames
    assert "manage_research" not in frames[0], sorted(frames[0])
    assert "manage_research" in frames[1], sorted(frames[1])
