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
        "skill_count": 0,
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


@pytest.mark.asyncio
async def test_production_dispatcher_prioritizes_requested_oracle_tool_under_realistic_cap(monkeypatch):
    captured = {}

    async def fake_stream(*args, **kwargs):
        captured["messages"] = args[1]
        captured["tools"] = kwargs.get("tools") or []
        yield 'data: {"delta":"Observed."}\n\n'
        yield "data: [DONE]\n\n"

    def fake_setting(key, default=None):
        if key == "agent_input_token_budget":
            return 8208
        return default

    names = ["fly_to_location", "get_current_view_state"] + [f"oracle_tool_{index}" for index in range(26)]
    schemas = [{
        "type": "function",
        "function": {
            "name": name,
            "description": ("Read current visible Cesium view state" if name == "get_current_view_state" else "ORACLE capability " + name) * 12,
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    } for name in names]
    capabilities = {
        name: {"extension_id": "oracle", "permission_mode": "read_only"}
        for name in names
    }

    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(agent_loop, "blocked_tools_for_owner", lambda owner: set())
    monkeypatch.setattr(agent_loop, "get_setting", fake_setting)
    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream)

    async for _chunk in agent_loop.stream_agent_loop(
        "https://api.openai.com/v1/chat/completions",
        "gpt-4o",
        [{"role": "user", "content": "Read the current Cesium view state and report exactly what is visible."}],
        context_length=8208,
        max_tokens=2048,
        relevant_tools=set(names) | {"ui_control"},
        forced_tools=set(names) | {"ui_control"},
        extra_tool_schemas=schemas,
        extension_capabilities=capabilities,
        context_extensions={"oracle": {
            "engaged": True,
            "state_mounted": True,
            "tool_count": len(names),
            "tool_names": names,
        }},
    ):
        pass

    mounted = [schema["function"]["name"] for schema in captured["tools"]]
    assert "ui_control" in mounted
    assert "get_current_view_state" in mounted
    assert "oracle_tool_25" in json.dumps(captured["messages"])


@pytest.mark.asyncio
async def test_current_domain_tool_is_prioritized_before_schema_cap(monkeypatch):
    captured = {}
    real_cap = agent_loop.cap_tool_schemas

    def capturing_cap(schemas, input_budget, *, priority_names=None):
        captured["priority_names"] = set(priority_names or ())
        return real_cap(schemas, input_budget, priority_names=priority_names)

    async def fake_stream(*args, **kwargs):
        yield 'data: {"delta":"I checked your Books catalog."}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(agent_loop, "blocked_tools_for_owner", lambda owner: set())
    monkeypatch.setattr(agent_loop, "cap_tool_schemas", capturing_cap)
    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream)

    async for _chunk in agent_loop.stream_agent_loop(
        "https://api.openai.com/v1/chat/completions",
        "gpt-4o",
        [{"role": "user", "content": "Inspect my Books library and report its status."}],
        context_length=4096,
        max_tokens=1024,
        relevant_tools={"manage_books"},
    ):
        pass

    assert "manage_books" in captured["priority_names"]


@pytest.mark.asyncio
async def test_books_turn_prunes_worker_delegation_from_voice_sized_catalog(monkeypatch):
    captured = {}

    async def fake_stream(*args, **kwargs):
        captured["tools"] = kwargs.get("tools") or []
        yield 'data: {"delta":"There are 14 PDFs in your library."}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(agent_loop, "blocked_tools_for_owner", lambda owner: set())
    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream)

    async for _chunk in agent_loop.stream_agent_loop(
        "https://api.openai.com/v1/chat/completions",
        "gpt-4o",
        [{"role": "user", "content": "List all books in my library."}],
        context_length=4096,
        max_tokens=1024,
        relevant_tools={"manage_books", "start_agent_task", "read_agent_task", "get_runtime_status"},
    ):
        pass

    names = {schema["function"]["name"] for schema in captured["tools"]}
    assert "manage_books" in names
    assert "start_agent_task" not in names
    assert "read_agent_task" not in names
