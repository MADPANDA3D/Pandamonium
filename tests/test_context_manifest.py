"""JOS-P2A observes mounted context without changing provider messages."""

import json

import pytest

from src import agent_loop
from src.llm_core import _sanitize_llm_messages
from src.model_context import (
    annotate_context_messages,
    build_context_manifest,
    cap_tool_schemas,
    estimate_tool_schema_tokens,
)
from src.prompt_security import untrusted_context_message


def _messages():
    return [
        {
            "role": "system",
            "content": "Jarvis identity",
            "metadata": {
                "jos_context": {
                    "class": "identity_policy",
                    "source": "odysseus.identity",
                    "trust": "system_authority",
                }
            },
        },
        {"role": "user", "content": "earlier question"},
        {"role": "assistant", "content": "earlier answer"},
        untrusted_context_message("saved memory: retrieved context", "private memory text"),
        untrusted_context_message("retrieved documents", "secret document text"),
        {
            "role": "user",
            "content": "current time",
            "metadata": {
                "jos_context": {
                    "class": "time",
                    "source": "odysseus.current_time",
                    "trust": "system_authority",
                }
            },
        },
        {"role": "user", "content": "current request"},
    ]


def test_manifest_reports_classes_trust_sources_and_omissions_without_content():
    manifest = build_context_manifest(
        _messages(),
        32768,
        omissions=["skills_disabled"],
        extensions={"oracle": {"engaged": True, "state_mounted": True, "tool_count": 1}},
        tool_catalog={
            "extension": [{
                "type": "function",
                "function": {"name": "fly_to_location", "parameters": {"type": "object"}},
            }],
        },
    )

    assert manifest["version"] == "jos-p2a.1"
    assert set(manifest["mounted"]["classes"]) >= {
        "identity_policy", "conversation", "operator_intent",
        "recalled_memory", "retrieved_knowledge", "time",
    }
    assert manifest["mounted"]["classes"]["operator_intent"]["messages"] == 1
    assert manifest["mounted"]["trust"]["untrusted_data"]["messages"] == 2
    assert {row["source"] for row in manifest["mounted"]["sources"]} >= {
        "memory.recalled", "documents.rag", "operator.current_turn",
    }
    assert manifest["extensions"]["oracle"] == {
        "engaged": True, "state_mounted": True, "tool_count": 1,
    }
    assert manifest["tools"]["extension"]["names"] == ["fly_to_location"]
    assert manifest["omissions"] == ["skills_disabled"]
    serialized = str(manifest)
    assert "private memory text" not in serialized
    assert "secret document text" not in serialized
    assert "current request" not in serialized


def test_manifest_reports_class_level_trimming():
    before = _messages()
    after = [before[0], before[-1]]
    manifest = build_context_manifest(after, 2048, before_messages=before)

    assert manifest["trimming"]["ran"] is True
    assert manifest["trimming"]["dropped_by_class"]["recalled_memory"]["messages"] == 1
    assert manifest["trimming"]["dropped_by_class"]["retrieved_knowledge"]["messages"] == 1
    assert manifest["mounted"]["classes"]["operator_intent"]["messages"] == 1


def test_extension_state_reports_ids_for_two_extensions_and_none():
    messages = _messages() + [
        {
            "role": "user",
            "content": "extension state one",
            "metadata": {"jos_context": {
                "class": "extension_state", "source": "extension.atlas",
                "trust": "untrusted_data", "extension_id": "atlas",
            }},
        },
        {
            "role": "user",
            "content": "extension state two",
            "metadata": {"jos_context": {
                "class": "extension_state", "source": "extension.cad-lab",
                "trust": "untrusted_data", "extension_id": "cad-lab",
            }},
        },
    ]
    manifest = build_context_manifest(messages, 4096, extensions={
        "atlas": {"engaged": True, "state_mounted": True, "tool_count": 1},
        "cad-lab": {"engaged": False, "state_mounted": False, "tool_count": 0},
    })
    extension_sources = {
        row["extension_id"] for row in manifest["mounted"]["sources"]
        if row.get("extension_id")
    }

    assert manifest["mounted"]["classes"]["extension_state"]["messages"] == 2
    assert extension_sources == {"atlas", "cad-lab"}
    assert set(manifest["extensions"]) == {"atlas", "cad-lab"}
    assert build_context_manifest(_messages(), 4096)["extensions"] == {}


def test_internal_tags_do_not_change_provider_payload():
    raw = _messages()
    annotated = annotate_context_messages(raw)

    assert _sanitize_llm_messages(annotated) == _sanitize_llm_messages(raw)
    assert all("metadata" not in message for message in _sanitize_llm_messages(annotated))


def test_native_tool_catalog_is_whole_schema_capped_with_extension_priority():
    schemas = [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": marker * 450,
                "parameters": {"type": "object"},
            },
        }
        for name, marker in (
            ("built_in_one", "a"),
            ("built_in_two", "b"),
            ("oracle_native", "o"),
        )
    ]

    kept, dropped = cap_tool_schemas(
        schemas,
        2048,
        priority_names={"oracle_native"},
    )
    kept_names = [schema["function"]["name"] for schema in kept]

    assert "oracle_native" in kept_names
    assert dropped
    assert estimate_tool_schema_tokens(kept) <= int(2048 * 0.20)


@pytest.mark.asyncio
async def test_agent_metrics_carry_manifest_on_direct_path(monkeypatch):
    async def fake_stream(*args, **kwargs):
        yield 'data: {"delta":"Hello."}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(agent_loop, "blocked_tools_for_owner", lambda owner: set())
    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream)

    events = []
    async for chunk in agent_loop.stream_agent_loop(
        "http://model.test/v1/chat/completions",
        "test-model",
        [{"role": "user", "content": "hello"}],
        context_length=4096,
    ):
        if chunk.startswith("data: ") and chunk.strip() != "data: [DONE]":
            events.append(json.loads(chunk[6:]))

    metrics = next(event["data"] for event in events if event.get("type") == "metrics")
    manifest = metrics["context_manifest"]
    assert manifest["mounted"]["classes"]["operator_intent"]["messages"] == 1
    assert manifest["omissions"] == ["agent_context_reduced_low_signal"]


@pytest.mark.asyncio
async def test_agent_caps_native_schemas_and_reports_the_omission(monkeypatch):
    captured = {}

    async def fake_stream(*args, **kwargs):
        captured["messages"] = args[1]
        captured["tools"] = kwargs.get("tools") or []
        yield 'data: {"delta":"Handled."}\n\n'
        yield "data: [DONE]\n\n"

    def fake_setting(key, default=None):
        if key == "agent_input_token_budget":
            return 4096
        return default

    schemas = [
        {
            "type": "function",
            "function": {
                "name": f"oracle_native_{index}",
                "description": str(index) * 900,
                "parameters": {"type": "object"},
            },
        }
        for index in range(4)
    ]
    relevant = {schema["function"]["name"] for schema in schemas}

    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(agent_loop, "blocked_tools_for_owner", lambda owner: set())
    monkeypatch.setattr(agent_loop, "get_setting", fake_setting)
    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream)

    events = []
    async for chunk in agent_loop.stream_agent_loop(
        "https://api.openai.com/v1/chat/completions",
        "gpt-4o",
        [{"role": "user", "content": "Use the ORACLE native capability."}],
        context_length=4096,
        max_tokens=1024,
        relevant_tools=relevant,
        extra_tool_schemas=schemas,
    ):
        if chunk.startswith("data: ") and chunk.strip() != "data: [DONE]":
            events.append(json.loads(chunk[6:]))

    metrics = next(event["data"] for event in events if event.get("type") == "metrics")
    manifest = metrics["context_manifest"]
    mounted_schema_tokens = estimate_tool_schema_tokens(captured["tools"])

    assert 0 < len(captured["tools"]) < len(schemas)
    assert mounted_schema_tokens <= manifest["budget"]["class_token_limits"]["tool_catalog"]
    assert metrics["input_tokens"] + mounted_schema_tokens <= manifest["budget"]["input_tokens"]
    assert "tool_catalog_budget_limited" in manifest["omissions"]
