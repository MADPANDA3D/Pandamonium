"""JOS-P2A observes mounted context without changing provider messages."""

import json

import pytest

from src import agent_loop
from src.llm_core import _sanitize_llm_messages
from src.mcp_manager import McpManager
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
async def test_selected_portal_chain_reaches_actual_model_payload_under_cap(monkeypatch):
    captured = {}
    manager = McpManager()
    manager._connections["portal-fixture"] = {
        "status": "connected",
        "name": "MAD MCP Portal",
        "server_info": {"name": "Fixture Broker"},
        "catalog_terms": ["Discord"],
        "instructions": (
            "Start with portal.welcome, then portal.list_services. "
            "Use portal.find_tools with a natural-language intent and "
            "portal.get_tool_reference for the complete schema and safety rules. "
            "Call portal.preview_tool_call before writes or destructive work. "
            "Execute reads with portal.call_read_tool."
        ),
    }
    chain = [
        "portal.welcome",
        "portal.list_services",
        "portal.check_connection",
        "portal.find_tools",
        "portal.get_tool_reference",
        "portal.preview_tool_call",
        "portal.call_read_tool",
    ]
    noisy_description = (
        "List configured services, inspect the Discord connection, discover the "
        "catalog tool, read its reference, validate its schema and envelope, and "
        "return five messages or a precise terminal service error."
    )
    distractors = [
        "portal.list_service_tools",
        "portal.list_releases",
        "portal.list_skills",
        "portal.list_tickets",
        "portal.get_project_thread",
        "portal.view_play",
        "portal.export_play",
        "portal.call_service_tool",
    ] + [f"portal.read_catalog_artifact_{index}" for index in range(50)]
    manager._tools["portal-fixture"] = [
        {
            "name": name,
            "description": ("Agent-ready broker entrypoint " + name) * 5,
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True},
        }
        for name in chain
    ] + [
        {
            "name": name,
            "description": noisy_description,
            "input_schema": {"type": "object", "properties": {}},
            "annotations": {"readOnlyHint": name != "portal.call_service_tool"},
        }
        for name in distractors
    ]
    assert len(manager._tools["portal-fixture"]) == 65

    full_smoke_request = (
        "Use only the configured MAD MCP Portal native mounted tools to read the last five "
        "messages from the configured Discord #general channel. Do not use a direct Discord "
        "connector, do not guess or hardcode any provider, profile, channel ID, service URL, "
        "or topology, and perform exactly one downstream message read. Start with portal.welcome, "
        "then portal.list_services and portal.check_connection. Use portal.find_tools with the "
        "natural-language intent, portal.get_tool_reference for the complete executable schema, "
        "portal.preview_tool_call for safety, and portal.call_read_tool for the single read. "
        "Do not use portal.list_service_tools or portal.call_service_tool."
    )
    assert manager.native_tool_names_for_request(full_smoke_request, limit=8) == {
        f"mcp__portal-fixture__{name}" for name in chain
    }

    async def fake_stream(*args, **kwargs):
        captured["messages"] = args[1]
        captured["tools"] = kwargs.get("tools") or []
        yield 'data: {"delta":"Ready to use the mounted Portal chain."}\n\n'
        yield "data: [DONE]\n\n"

    def fake_setting(key, default=None):
        if key == "agent_input_token_budget":
            return 8208
        return default

    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: manager)
    monkeypatch.setattr(agent_loop, "blocked_tools_for_owner", lambda _owner: set())
    monkeypatch.setattr(agent_loop, "get_setting", fake_setting)
    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream)

    exact_request = (
        "Run this read-only installed acceptance through the native configured MAD MCP Portal. "
        "Use only the mounted Portal tools and never bypass Portal. Follow one bounded flow: "
        "bootstrap once, list configured services once, check Discord connection once, discover "
        "the catalog-declared read tool for the configured Discord #general channel once, read its "
        "lossless reference once, and call the Portal read executor exactly once with a count of five. "
        "Do not preview a read. Do not call another executor or connector. Do not retry any tool. "
        "On any schema, permission, unsupported capability, service, authentication, or transport "
        "failure, stop immediately with one precise terminal error and never continue to a step limit. "
        "Never include message content, author names, identifiers, timestamps, attachments, or credentials "
        "in the final response. If and only if the returned Portal envelope is valid and proves exactly "
        "five messages, return exactly: MAD842_PORTAL_READ_OK count=5"
    )
    async for _chunk in agent_loop.stream_agent_loop(
        "https://api.openai.com/v1/chat/completions",
        "gpt-4o",
        [{"role": "user", "content": exact_request}],
        context_length=8208,
        max_tokens=2048,
    ):
        pass

    sent = {
        schema["function"]["name"]
        for schema in captured["tools"]
        if schema.get("function")
    }
    required = {f"mcp__portal-fixture__{name}" for name in chain}
    assert {name for name in sent if name.startswith("mcp__portal-fixture__")} == required
    assert "mcp__portal-fixture__portal.list_service_tools" not in sent
    assert "mcp__portal-fixture__portal.call_service_tool" not in sent

    visible_messages = json.dumps(captured["messages"])
    assert "mcp__portal-fixture__portal.call_service_tool" not in visible_messages
    contract = next(
        message["content"]
        for message in captured["messages"]
        if str(message.get("content") or "").startswith(
            agent_loop._NATIVE_MCP_CONTRACT_HEADING
        )
    )
    assert required == {
        name for name in required if f"`{name}`" in contract
    }


@pytest.mark.asyncio
async def test_collection_followup_keeps_portal_qdrant_chain_in_model_payload(monkeypatch):
    captured = {}
    manager = McpManager()
    manager._connections["portal-fixture"] = {
        "status": "connected",
        "name": "MAD MCP Portal",
        "server_info": {"name": "Fixture Broker"},
        "catalog_terms": ["Qdrant"],
        "instructions": (
            "Start with portal.welcome, then portal.list_services. "
            "Use portal.find_tools with a natural-language intent and "
            "portal.get_tool_reference for the complete schema and safety rules. "
            "Call portal.preview_tool_call before writes or destructive work. "
            "Execute reads with portal.call_read_tool."
        ),
    }
    chain = [
        "portal.welcome",
        "portal.list_services",
        "portal.find_tools",
        "portal.get_tool_reference",
        "portal.preview_tool_call",
        "portal.call_read_tool",
    ]
    manager._tools["portal-fixture"] = [
        {
            "name": name,
            "description": f"Agent-ready broker entrypoint {name}",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True},
        }
        for name in chain
    ]

    async def fake_stream(*args, **kwargs):
        captured["messages"] = args[1]
        captured["tools"] = kwargs.get("tools") or []
        yield 'data: {"delta":"I will continue the Portal read."}\n\n'
        yield "data: [DONE]\n\n"

    def fake_setting(key, default=None):
        if key == "agent_input_token_budget":
            return 8208
        return default

    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: manager)
    monkeypatch.setattr(agent_loop, "blocked_tools_for_owner", lambda _owner: set())
    monkeypatch.setattr(agent_loop, "get_setting", fake_setting)
    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream)

    messages = [
        {
            "role": "user",
            "content": (
                "Ok use the mad mcp portal to find the jarvis-knowledgebase "
                "collection in Qdrant and tell me whats in that collection"
            ),
        },
        {"role": "assistant", "content": "I found the collection."},
        {
            "role": "user",
            "content": "I want you to tell me what information is in that collection",
        },
        {"role": "assistant", "content": "I need to inspect the collection."},
        {
            "role": "user",
            "content": (
                "the mad mcp portal has all the tools you need it is an mcp broker "
                "which means there is an entire qdrant mcp in there you need to use "
                "the portal.welcome tool to learn your way around the mad mcp portal "
                "so you can see how to make the correct tool calls"
            ),
        },
        {"role": "assistant", "content": "I listed the available collections."},
        {
            "role": "user",
            "content": (
                "Ok but youre not answering my fucking question I already told you I "
                "want ot know whats in that collection you need to query it and look it "
                "over and come back to me with bullet points on what is in tere - there "
                "is a lot of operational stuff in there so I want yo uto tell me what "
                "the fuck is in that collection"
            ),
        },
    ]
    async for _chunk in agent_loop.stream_agent_loop(
        "http://127.0.0.1:1919/v1/chat/completions",
        "jarvis",
        messages,
        context_length=8208,
        max_tokens=2048,
    ):
        pass

    sent = {
        schema["function"]["name"]
        for schema in captured["tools"]
        if schema.get("function")
    }
    required = {f"mcp__portal-fixture__{name}" for name in chain}
    assert {name for name in sent if name.startswith("mcp__portal-fixture__")} == required
    assert {"manage_mcp", "api_call", "app_api", "pipeline"}.isdisjoint(sent)
    visible_messages = json.dumps(captured["messages"])
    assert "Listing collections does not answer what is inside one" in visible_messages
    assert "jarvis-knowledgebase" in visible_messages
    assert "Qdrant" in visible_messages


@pytest.mark.asyncio
async def test_requested_browser_actions_reach_the_actual_provider_payload(monkeypatch):
    captured = {}
    manager = McpManager()
    manager._connections["browser-fixture"] = {
        "status": "connected",
        "name": "Built-in Browser",
        "server_info": {"name": "Playwright MCP"},
        "catalog_terms": ["browser"],
    }
    manager._tools["browser-fixture"] = [
        {
            "name": "browser_navigate",
            "description": "Navigate to a URL",
            "input_schema": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
            "annotations": {"readOnlyHint": False, "destructiveHint": True},
        },
        {
            "name": "browser_snapshot",
            "description": "Capture the accessibility snapshot",
            "input_schema": {"type": "object", "properties": {}},
            "annotations": {"readOnlyHint": True, "destructiveHint": False},
        },
    ]

    async def fake_stream(*args, **kwargs):
        captured["tools"] = kwargs.get("tools") or []
        yield 'data: {"delta":"Ready."}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: manager)
    monkeypatch.setattr(agent_loop, "blocked_tools_for_owner", lambda _owner: set())
    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream)

    async for _chunk in agent_loop.stream_agent_loop(
        "https://api.openai.com/v1/chat/completions",
        "gpt-4o",
        [{
            "role": "user",
            "content": (
                "Use Built-in Browser: browser_navigate to https://example.com/, "
                "then browser_snapshot and report the title and H1."
            ),
        }],
        context_length=8208,
        max_tokens=2048,
    ):
        pass

    sent = {
        schema["function"]["name"]
        for schema in captured["tools"]
        if schema.get("function")
    }
    assert {name for name in sent if name.startswith("mcp__browser-fixture__")} == {
        "mcp__browser-fixture__browser_navigate",
        "mcp__browser-fixture__browser_snapshot",
    }


@pytest.mark.asyncio
async def test_native_mcp_contract_lists_every_mcp_schema_in_the_provider_payload(monkeypatch):
    captured = {}
    manager = McpManager()
    manager._connections.update({
        "portal-fixture": {
            "status": "connected",
            "name": "MAD MCP Portal",
            "catalog_terms": ["Discord"],
        },
        "files-fixture": {
            "status": "connected",
            "name": "Files MCP",
            "catalog_terms": ["documents"],
        },
    })
    manager._tools.update({
        "portal-fixture": [{
            "name": "portal.welcome",
            "description": "Start the broker flow.",
            "input_schema": {"type": "object", "properties": {}},
            "annotations": {"readOnlyHint": True},
        }],
        "files-fixture": [{
            "name": "files.read_document",
            "description": "Read one selected document.",
            "input_schema": {
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
            },
            "annotations": {"readOnlyHint": True},
        }],
    })
    separately_relevant = "mcp__files-fixture__files.read_document"

    async def fake_stream(*args, **kwargs):
        captured["messages"] = args[1]
        captured["tools"] = kwargs.get("tools") or []
        yield 'data: {"delta":"The mounted tools are ready."}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: manager)
    monkeypatch.setattr(agent_loop, "blocked_tools_for_owner", lambda _owner: set())
    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream)

    async for _chunk in agent_loop.stream_agent_loop(
        "https://api.openai.com/v1/chat/completions",
        "gpt-4o",
        [{"role": "user", "content": "Use MAD MCP Portal for Discord."}],
        context_length=8208,
        max_tokens=2048,
        relevant_tools={separately_relevant},
    ):
        pass

    sent = {
        schema["function"]["name"]
        for schema in captured["tools"]
        if schema.get("function", {}).get("name", "").startswith("mcp__")
    }
    contract = next(
        message["content"]
        for message in captured["messages"]
        if str(message.get("content") or "").startswith(
            agent_loop._NATIVE_MCP_CONTRACT_HEADING
        )
    )

    assert sent == {
        "mcp__portal-fixture__portal.welcome",
        separately_relevant,
    }
    assert sent == {name for name in sent if f"`{name}`" in contract}


@pytest.mark.asyncio
async def test_schema_cap_removes_dropped_mcp_tool_from_model_facing_prose(monkeypatch):
    captured = {}
    manager = McpManager()
    manager._connections["portal-fixture"] = {
        "status": "connected",
        "name": "MAD MCP Portal",
        "catalog_terms": ["Discord"],
    }
    manager._tools["portal-fixture"] = [
        {
            "name": "portal.welcome",
            "description": "Start the broker flow.",
            "input_schema": {"type": "object", "properties": {}},
            "annotations": {"readOnlyHint": True},
        },
        {
            "name": "portal.find_tools",
            "description": "x" * 4_000,
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "y" * 4_000},
                },
                "required": ["query"],
            },
            "annotations": {"readOnlyHint": True},
        },
    ]

    async def fake_stream(*args, **kwargs):
        captured["messages"] = args[1]
        captured["tools"] = kwargs.get("tools") or []
        yield 'data: {"delta":"The mounted tool is ready."}\n\n'
        yield "data: [DONE]\n\n"

    def fake_setting(key, default=None):
        if key == "agent_input_token_budget":
            return 4096
        return default

    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: manager)
    monkeypatch.setattr(agent_loop, "blocked_tools_for_owner", lambda _owner: set())
    monkeypatch.setattr(agent_loop, "get_setting", fake_setting)
    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream)

    async for _chunk in agent_loop.stream_agent_loop(
        "https://api.openai.com/v1/chat/completions",
        "gpt-4o",
        [{"role": "user", "content": "Use MAD MCP Portal for Discord."}],
        context_length=4096,
        max_tokens=512,
    ):
        pass

    sent = {
        schema["function"]["name"]
        for schema in captured["tools"]
        if schema.get("function", {}).get("name", "").startswith("mcp__")
    }
    visible_messages = json.dumps(captured["messages"])

    assert sent == {"mcp__portal-fixture__portal.welcome"}
    assert "mcp__portal-fixture__portal.find_tools" not in visible_messages


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


@pytest.mark.asyncio
async def test_books_source_work_retains_worker_delegation(monkeypatch):
    captured = {}

    async def fake_stream(*args, **kwargs):
        captured["tools"] = kwargs.get("tools") or []
        yield 'data: {"delta":"I will inspect the service source."}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(agent_loop, "blocked_tools_for_owner", lambda owner: set())
    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream)

    async for _chunk in agent_loop.stream_agent_loop(
        "https://api.openai.com/v1/chat/completions",
        "gpt-4o",
        [{"role": "user", "content": "Review the Books service source code."}],
        context_length=4096,
        max_tokens=1024,
        relevant_tools={"manage_books", "start_agent_task", "read_agent_task", "get_runtime_status"},
    ):
        pass

    names = {schema["function"]["name"] for schema in captured["tools"]}
    assert "start_agent_task" in names
    assert "read_agent_task" in names
