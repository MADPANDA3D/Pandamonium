import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from src.agent_loop import _NATIVE_MCP_DIRECT_RULES
from src.authority_protocol import AuthorityStore, action_effect_for
from src.mcp_manager import (
    McpManager,
    _format_mcp_connection_error,
    _mcp_tool_record,
    _static_http_headers,
)


def test_playwright_mcp_connection_error_includes_install_hint():
    msg = _format_mcp_connection_error(
        "Browser (Playwright)",
        "npx",
        ["-y", "@playwright/mcp@0.0.80", "--headless"],
        RuntimeError("package not found"),
    )

    assert "package not found" in msg
    assert "Browser MCP could not start" in msg
    assert "npx -y @playwright/mcp@0.0.80 --version" in msg
    assert "restart Pandamonium" in msg


def test_generic_mcp_connection_error_preserves_original_error():
    msg = _format_mcp_connection_error(
        "Custom MCP",
        "python",
        ["server.py"],
        RuntimeError("boom"),
    )

    assert msg == "boom"


def test_http_transport_routes_to_start_http_connect():
    mgr = McpManager()

    async def fake_start(server_id, name, url):
        return "ROUTED"

    with patch.object(McpManager, "_start_http_connect", side_effect=fake_start) as m:
        result = asyncio.run(mgr.connect_server("id1", "n", "http", url="https://x/mcp"))
    assert result == "ROUTED"
    m.assert_called_once()


def test_http_transport_forwards_static_headers_without_changing_other_transports():
    """HTTP bearer credentials must reach the native transport intact."""
    mgr = McpManager()
    headers = {"Authorization": "Bearer fixture-only-token"}

    async def fake_start(server_id, name, url, *, headers=None):
        assert headers == {"Authorization": "Bearer fixture-only-token"}
        return True

    with patch.object(McpManager, "_start_http_connect", side_effect=fake_start) as mocked:
        result = asyncio.run(
            mgr.connect_server(
                "http-static",
                "Static HTTP",
                "http",
                url="https://example.invalid/mcp",
                headers=headers,
            )
        )

    assert result is True
    mocked.assert_called_once_with(
        "http-static",
        "Static HTTP",
        "https://example.invalid/mcp",
        headers=headers,
    )


def test_static_http_headers_accept_only_bounded_bearer_storage():
    assert _static_http_headers('{"static_bearer_token":"fixture-token"}') == {
        "Authorization": "Bearer fixture-token"
    }
    assert _static_http_headers('{"static_bearer_token":"bad\\nvalue"}') is None
    assert _static_http_headers('{"static_bearer_token":42}') is None
    assert _static_http_headers('{"tokens":{"access_token":"oauth"}}') is None
    assert _static_http_headers('not-json') is None


def test_mcp_call_preserves_bounded_structured_content_for_native_consumers():
    class FakeSession:
        async def call_tool(self, _name, _arguments):
            return SimpleNamespace(
                content=[SimpleNamespace(text="Catalog ready")],
                structuredContent={"data": {"items": [{"id": "calendar"}]}},
                isError=False,
            )

    manager = McpManager()
    result = asyncio.run(
        manager._do_call(
            FakeSession(),
            "portal.list_services",
            {},
            max_output_bytes=4096,
        )
    )

    assert result["stdout"] == "Catalog ready"
    assert result["structured_content"] == {
        "data": {"items": [{"id": "calendar"}]}
    }


class _PortalContractSession:
    def __init__(self):
        self.calls = []
        self.discord_version = "discord-v2"

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        trace_id = f"trace-{len(self.calls)}"
        if name == "portal.list_services":
            structured = {
                "traceId": trace_id,
                "data": {"items": [
                    {
                        "id": "discord",
                        "name": "DISCORD-MCP",
                        "configured": True,
                        "state": "configured",
                        "catalogVersion": self.discord_version,
                    },
                    {
                        "id": "qdrant",
                        "name": "QDRANT-MCP",
                        "configured": True,
                        "state": "configured",
                        "catalogVersion": "qdrant-v2",
                    },
                ]},
            }
        elif name == "portal.find_tools":
            assert arguments["service"] == "discord"
            if arguments["query"] == "find channel":
                row = {
                    "serviceId": "discord",
                    "toolName": "find_channel",
                    "description": "Find a readable channel by name.",
                    "risk": "read",
                    "descriptorHash": "find-channel-hash",
                }
            else:
                assert arguments["query"] == "read messages"
                row = {
                    "serviceId": "discord",
                    "toolName": "read_messages",
                    "description": "Read recent messages from one channel.",
                    "risk": "read",
                    "descriptorHash": "read-messages-hash",
                }
            structured = {"traceId": trace_id, "data": {"items": [row]}}
        elif name == "portal.get_tool_reference":
            tool_name = arguments["toolName"]
            if tool_name == "find_channel":
                input_schema = {
                    "type": "object",
                    "required": ["channel_name"],
                    "properties": {"channel_name": {"type": "string"}},
                }
                descriptor_hash = "find-channel-hash"
            else:
                input_schema = {
                    "type": "object",
                    "properties": {
                        "channel_id": {"type": "string", "default": ""},
                        "count": {"type": "string", "default": ""},
                    },
                }
                descriptor_hash = "read-messages-hash"
            structured = {
                "traceId": trace_id,
                "data": {"descriptor": {
                    "serviceId": "discord",
                    "nativeToolName": tool_name,
                    "description": f"Fixture {tool_name}",
                    "descriptorHash": descriptor_hash,
                    "catalogVersion": "discord-v2",
                    "inputSchema": input_schema,
                }},
            }
        elif name == "portal.call_read_tool" and arguments["toolName"] == "find_channel":
            assert arguments["arguments"] == {"channel_name": "general"}
            structured = {
                "traceId": trace_id,
                "data": {"result": {
                    "ok": True,
                    "data": {"items": [{
                        "id": "1542679644640247860",
                        "name": "💬│general",
                    }]},
                }},
            }
        elif name == "portal.call_read_tool":
            assert arguments == {
                "serviceId": "discord",
                "toolName": "read_messages",
                "arguments": {
                    "count": "5",
                    "channel_id": "1542679644640247860",
                },
            }
            structured = {
                "traceId": trace_id,
                "data": {"result": {
                    "ok": True,
                    "data": {"items": [{"id": str(i), "content": f"message-{i}"} for i in range(5)]},
                }},
            }
        else:
            raise AssertionError((name, arguments))
        return SimpleNamespace(
            content=[SimpleNamespace(text="Portal result ready")],
            structuredContent=structured,
            isError=False,
        )


def _portal_contract_manager():
    manager = McpManager()
    manager._connections["portal-fixture"] = {
        "status": "connected",
        "name": "MAD MCP Portal",
        "server_info": {"name": "mad-mcp-aggregator"},
        "instructions": "Use portal.find_tools, portal.get_tool_reference, and portal.call_read_tool.",
    }
    manager._tools["portal-fixture"] = [
        {
            "name": name,
            "description": name,
            "input_schema": {"type": "object", "properties": {}},
            "annotations": {"readOnlyHint": True},
        }
        for name in (
            "portal.list_services",
            "portal.find_tools",
            "portal.get_tool_reference",
            "portal.call_read_tool",
        )
    ]
    session = _PortalContractSession()
    manager._sessions["portal-fixture"] = session
    asyncio.run(manager._sync_portal_services("portal-fixture"))
    return manager, session


def test_portal_service_sync_populates_one_versioned_search_identity():
    manager, _session = _portal_contract_manager()

    connection = manager._connections["portal-fixture"]
    assert connection["catalog_terms"] == [
        "discord", "DISCORD-MCP", "qdrant", "QDRANT-MCP"
    ]
    assert connection["portal_services"][0] == {
        "id": "discord",
        "name": "DISCORD-MCP",
        "description": "",
        "configured": True,
        "state": "configured",
        "catalog_version": "discord-v2",
    }
    records = manager.get_portal_index_records()
    assert {record["tool_name"] for record in records} == {
        "mcp__portal-fixture__portal.find_tools"
    }
    assert any("Catalog version: qdrant-v2" in record["document"] for record in records)


def test_portal_catalog_revision_invalidates_descriptor_cache_only_on_change():
    manager, session = _portal_contract_manager()
    manager._portal_tool_cache["portal-fixture"] = {"discord:read_messages": {"cached": True}}
    manager._portal_reference_cache["portal-fixture"] = {"discord:read_messages": {"cached": True}}

    asyncio.run(manager._sync_portal_services("portal-fixture"))
    assert manager._portal_tool_cache["portal-fixture"]
    assert manager._portal_reference_cache["portal-fixture"]

    session.discord_version = "discord-v3"
    asyncio.run(manager._sync_portal_services("portal-fixture"))
    assert "portal-fixture" not in manager._portal_tool_cache
    assert "portal-fixture" not in manager._portal_reference_cache


def test_portal_read_preparation_scopes_search_resolves_target_and_relays_exact_call():
    manager, session = _portal_contract_manager()
    preparation = asyncio.run(manager.prepare_portal_read(
        "Use MAD MCP Portal to read the last five messages from Discord channel #general.",
        "Use MAD MCP Portal to read the last five messages from Discord channel #general.",
    ))

    assert preparation is not None
    assert preparation["service_id"] == "discord"
    assert preparation["tool_name"] == "read_messages"
    assert preparation["schema"]["function"]["name"] == preparation["qualified_name"]
    assert preparation["schema"]["function"]["parameters"]["properties"] == {
        "count": {"type": "string", "default": ""}
    }
    assert [event["tool"].rsplit("__", 1)[-1] for event in preparation["trace_events"]] == [
        "portal.find_tools",
        "portal.get_tool_reference",
        "portal.find_tools",
        "portal.get_tool_reference",
        "portal.call_read_tool",
    ]
    assert all(event["trace_id"].startswith("trace-") for event in preparation["trace_events"])

    result = asyncio.run(manager.call_tool(
        preparation["qualified_name"], {"count": "5"}
    ))

    assert result["exit_code"] == 0
    assert result["portal_relay"] == {
        "service_id": "discord",
        "tool_name": "read_messages",
        "descriptor_hash": "read-messages-hash",
        "catalog_version": "discord-v2",
        "trace_id": "trace-7",
        "arguments": {
            "count": "5",
            "channel_id": "1542679644640247860",
        },
        "item_count": 5,
    }
    assert len(preparation["qualified_name"]) <= 64
    assert '"message-4"' in result["model_content"]
    assert session.calls[-1][0] == "portal.call_read_tool"
    assert manager.get_action_policies()[preparation["qualified_name"]] == {
        "action_effect": "read"
    }


def test_portal_discovery_query_preserves_outcome_and_drops_negative_constraints():
    assert McpManager._portal_discovery_query(
        "Use Portal to sample ten payloads from a Qdrant collection. Do not include vectors."
    ) == "collection contents"
    assert McpManager._portal_discovery_query(
        "What information is inside that collection? Show me ten examples."
    ) == "collection contents"
    assert McpManager._portal_discovery_query(
        "Use Portal to list Qdrant collections."
    ) == "collections"


def test_portal_result_count_recovers_nested_provider_shapes_without_content():
    assert McpManager._portal_result_item_count({
        "data": {"result": {"content": [{"text": '{"points":[1,2,3]}' }]}}
    }) == 3


def test_portal_result_context_keeps_only_compact_collection_identifiers():
    payload = {
        "data": [{
            "type": "text",
            "text": '{"data":{"collections":["the-barn","school",'
                    '"jarvis-knowledgebase"],"count":3},'
                    '"meta":{"request_id":"not-model-context"}}',
        }],
        "traceId": "trace-context",
    }

    assert McpManager._portal_result_context(payload) == {
        "collection_names": ["the-barn", "school", "jarvis-knowledgebase"],
    }


def test_portal_followup_context_is_fixed_and_removed_from_model_schema():
    manager = McpManager()
    manager._connections["portal-fixture"] = {
        "status": "connected",
        "name": "MAD MCP Portal",
        "server_info": {"name": "mad-mcp-aggregator"},
        "portal_services": [{
            "id": "qdrant",
            "name": "QDRANT-MCP",
            "configured": True,
            "state": "configured",
            "catalog_version": "qdrant-v2",
        }],
        "catalog_terms": ["qdrant"],
    }
    manager._tools["portal-fixture"] = [
        {"name": "portal.find_tools"},
        {"name": "portal.get_tool_reference"},
        {"name": "portal.call_read_tool"},
        {"name": "portal.list_services"},
    ]

    async def fake_call(name, arguments, **_kwargs):
        if name.endswith("portal.find_tools"):
            return {
                "exit_code": 0,
                "structured_content": {
                    "traceId": "find-trace",
                    "data": {"items": [{
                        "serviceId": "qdrant",
                        "toolName": "qdrant-list-points",
                        "description": "List points in a collection.",
                        "risk": "read",
                        "descriptorHash": "points-hash",
                    }]},
                },
            }
        assert name.endswith("portal.get_tool_reference")
        return {
            "exit_code": 0,
            "structured_content": {
                "traceId": "reference-trace",
                "data": {"descriptor": {
                    "serviceId": "qdrant",
                    "nativeToolName": "qdrant-list-points",
                    "description": "List points in a collection.",
                    "descriptorHash": "points-hash",
                    "catalogVersion": "qdrant-v2",
                    "inputSchema": {
                        "type": "object",
                        "required": ["collection_name"],
                        "properties": {
                            "collection_name": {"type": "string"},
                            "limit": {"type": "integer"},
                        },
                    },
                }},
            },
        }

    manager.call_tool = fake_call
    preparation = asyncio.run(manager.prepare_portal_read(
        "What information is inside that collection? Show me ten examples.",
        "Use MAD MCP Portal to list Qdrant collections.",
        context_arguments={"collection_name": "jarvis-knowledgebase"},
    ))

    assert preparation is not None
    assert preparation["schema"]["function"]["parameters"]["properties"] == {
        "limit": {"type": "integer"},
    }
    assert preparation["schema"]["function"]["parameters"]["required"] == []
    assert manager._portal_proxy_tools[preparation["qualified_name"]][
        "fixed_arguments"
    ] == {"collection_name": "jarvis-knowledgebase"}


def test_portal_request_schema_keeps_only_exact_fields_needed_for_payload_sample():
    projected = McpManager._portal_request_schema({
        "type": "object",
        "$defs": {"HugeFilter": {"type": "object", "description": "x" * 20_000}},
        "required": ["collection_name"],
        "properties": {
            "collection_name": {"type": "string"},
            "limit": {"type": "integer", "default": 50},
            "include_payload": {"type": "boolean", "default": True},
            "include_vectors": {"type": "boolean", "default": False},
            "offset": {"type": ["string", "null"]},
            "memory_filter": {"$ref": "#/$defs/HugeFilter"},
        },
    }, (
        "Sample ten payloads from the jarvis-knowledgebase collection. "
        "Do not include vectors."
    ))

    assert set(projected["properties"]) == {
        "collection_name", "limit", "include_payload", "include_vectors"
    }
    assert projected["required"] == ["collection_name"]
    assert "$defs" not in projected
    assert len(str(projected)) < 1000


def test_mcp_tools_receive_proven_authority_metadata_and_unknowns_fail_closed(tmp_path):
    manager = McpManager()
    manager._tools["portal"] = [
        {"name": "get_health", "annotations": {"readOnlyHint": True}},
        {"name": "status_services", "annotations": None},
        {
            "name": "navigate",
            "annotations": {"readOnlyHint": False, "destructiveHint": True},
        },
        {"name": "post_message", "annotations": None},
    ]
    manager._connections["portal"] = {"name": "MAD MCP Portal"}

    policies = manager.get_action_policies()

    assert policies == {
        "mcp__portal__get_health": {"action_effect": "read"},
        "mcp__portal__status_services": {"action_effect": "read"},
        "mcp__portal__navigate": {
            "action_effect": "destructive_or_difficult_to_recover"
        },
    }
    read_call = {
        "name": "mcp__portal__get_health",
        "target": "mcp",
        "arguments": {},
        "capability_policy": policies["mcp__portal__get_health"],
    }
    unknown_call = {"name": "mcp__portal__post_message", "target": "mcp", "arguments": {}}
    navigate_call = {
        "name": "mcp__portal__navigate",
        "target": "mcp",
        "arguments": {"url": "https://example.com/"},
        "capability_policy": policies["mcp__portal__navigate"],
    }
    assert action_effect_for(read_call) == "read"
    assert action_effect_for(unknown_call) == "unclassified"
    assert AuthorityStore(tmp_path / "authority.json").decide(
        navigate_call, operator_id="owner", session_id="browser-session"
    )["decision"] == "approval_required"


def test_http_tool_projection_preserves_readonly_annotations():
    tool = SimpleNamespace(
        name="portal.welcome",
        description="Start here",
        inputSchema={"type": "object", "properties": {}},
        annotations={"readOnlyHint": True},
    )

    projected = _mcp_tool_record(tool)

    assert projected["annotations"] == {"readOnlyHint": True}
    assert action_effect_for({
        "name": "mcp__portal__portal.welcome",
        "target": "mcp",
        "arguments": {},
        "capability_policy": {"action_effect": "read"}
        if projected["annotations"]["readOnlyHint"] else {},
    }) == "read"


def test_named_native_connection_selects_declared_discovery_and_read_tools_only():
    manager = McpManager()
    manager._connections["portal-fixture"] = {
        "status": "connected",
        "name": "Acme MCP Portal",
        "server_info": {"name": "Acme Broker"},
        "catalog_terms": ["Discord", "Slack"],
    }
    guidance = (
        "Start with portal.welcome, then portal.list_services. "
        "Use portal.find_tools and portal.call_read_tool for reads. "
        "Use portal.call_write_tool for writes."
    )
    manager._tools["portal-fixture"] = [
        {"name": "portal.welcome", "description": guidance, "annotations": {"readOnlyHint": True}},
        {"name": "portal.list_services", "description": "List admitted providers", "annotations": {"readOnlyHint": True}},
        {"name": "portal.find_tools", "description": "Find provider tools", "annotations": {"readOnlyHint": True}},
        {"name": "portal.call_read_tool", "description": "Execute one typed read", "annotations": {"readOnlyHint": True}},
        {"name": "portal.call_write_tool", "description": "Execute one write", "annotations": {"readOnlyHint": False}},
    ]
    manager._connections["slack-fixture"] = {
        "status": "connected",
        "name": "Slack MCP",
        "server_info": {},
    }
    manager._tools["slack-fixture"] = [
        {"name": "read_messages", "description": "Read Slack messages", "annotations": {"readOnlyHint": True}},
    ]

    selected = manager.native_tool_names_for_request(
        "Using Acme MCP Portal, read the last five Discord messages from general"
    )

    assert selected == {
        "mcp__portal-fixture__portal.welcome",
        "mcp__portal-fixture__portal.list_services",
        "mcp__portal-fixture__portal.find_tools",
        "mcp__portal-fixture__portal.call_read_tool",
    }
    assert not any("write" in name for name in selected)
    assert not any("slack-fixture" in name for name in selected)
    assert manager.native_tool_names_for_request("Read the last five Teams messages") == set()


def test_named_native_connection_selects_requested_actions_without_authorizing_unknowns():
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
            "annotations": {"readOnlyHint": False, "destructiveHint": True},
        },
        {
            "name": "browser_snapshot",
            "description": "Capture the accessibility snapshot",
            "annotations": {"readOnlyHint": True, "destructiveHint": False},
        },
        {"name": "browser_unclassified", "description": "Unknown browser action"},
    ]

    selected = manager.native_tool_names_for_request(
        "Use Built-in Browser: browser_navigate to https://example.com/, then browser_snapshot."
    )

    assert selected == {
        "mcp__browser-fixture__browser_navigate",
        "mcp__browser-fixture__browser_snapshot",
    }
    assert "mcp__browser-fixture__browser_unclassified" not in manager.get_action_policies()


def test_explicit_native_connection_name_wins_over_earlier_shared_catalog_match():
    manager = McpManager()
    manager._connections["discord-direct"] = {
        "status": "connected",
        "name": "Discord MCP",
        "catalog_terms": ["Discord"],
    }
    manager._tools["discord-direct"] = [
        {
            "name": f"discord.read_{index}",
            "description": "Read Discord data.",
            "annotations": {"readOnlyHint": True},
        }
        for index in range(8)
    ]
    manager._connections["portal-fixture"] = {
        "status": "connected",
        "name": "MAD MCP Portal",
        "server_info": {"name": "MAD Broker"},
        "catalog_terms": ["Discord"],
    }
    manager._tools["portal-fixture"] = [{
        "name": "portal.welcome",
        "description": "Start the Portal flow.",
        "annotations": {"readOnlyHint": True},
    }]

    selected = manager.native_tool_names_for_request(
        "Use MAD MCP Portal to read the last five Discord messages."
    )

    assert selected == {"mcp__portal-fixture__portal.welcome"}


def test_portal_read_route_skips_cached_connection_orientation_entrypoints():
    manager = McpManager()
    manager._connections["portal-fixture"] = {
        "status": "connected",
        "name": "Acme MCP Portal",
        "server_info": {"name": "Acme Broker"},
        "catalog_terms": ["Discord"],
    }
    entrypoints = [
        "portal.welcome",
        "portal.list_services",
        "portal.find_tools",
        "portal.get_tool_reference",
        "portal.call_read_tool",
    ]
    artifacts = [
        ("portal.list_skills", "Call portal.view_skill."),
        ("portal.view_skill", "Call portal.export_skill."),
        ("portal.export_skill", "Export one artifact."),
        ("portal.view_play", "Call portal.export_play."),
        ("portal.export_play", "Export one artifact."),
    ]
    manager._tools["portal-fixture"] = [
        {
            "name": name,
            "description": "Broker entrypoint.",
            "annotations": {"readOnlyHint": True},
        }
        for name in entrypoints
    ] + [
        {
            "name": name,
            "description": description,
            "annotations": {"readOnlyHint": True},
        }
        for name, description in artifacts
    ]

    selected = manager.native_tool_names_for_request(
        "Using Acme MCP Portal, pull the last five Discord messages from general "
        "starting with portal.welcome.",
        limit=len(entrypoints),
    )

    assert selected == {
        f"mcp__portal-fixture__{name}" for name in (
            "portal.find_tools",
            "portal.get_tool_reference",
            "portal.call_read_tool",
        )
    }


def test_initialize_read_flow_is_authoritative_with_named_supplements():
    manager = McpManager()
    manager._connections["broker-fixture"] = {
        "status": "connected",
        "name": "Acme Broker",
        "server_info": {"name": "Fixture Broker"},
        "instructions": "Use broker.start, broker.discover, then broker.read.",
    }
    manager._tools["broker-fixture"] = [
        {
            "name": name,
            "description": "Read configured services and validate the catalog.",
            "annotations": {"readOnlyHint": True},
        }
        for name in [
            "broker.start",
            "broker.discover",
            "broker.read",
            "broker.check_connection",
            "broker.list_service_tools",
            "broker.list_releases",
            "broker.foo",
            "broker.bar",
            "broker.baz",
        ]
    ]

    assert manager.native_tool_names_for_request(
        "Using Acme Broker, check the connection."
    ) == {
        f"mcp__broker-fixture__broker.{name}"
        for name in ["start", "discover", "read", "check_connection"]
    }
    assert manager.native_tool_names_for_request(
        "Using Acme Broker, inspect broker.list_releases."
    ) == {
        f"mcp__broker-fixture__broker.{name}"
        for name in ["start", "discover", "read", "list_releases"]
    }
    assert manager.native_tool_names_for_request(
        "Using Acme Broker, check the connection. Do not use "
        "broker.list_service_tools or broker.list_releases."
    ) == {
        f"mcp__broker-fixture__broker.{name}"
        for name in ["start", "discover", "read", "check_connection"]
    }
    assert manager.native_tool_names_for_request(
        "Using Acme Broker, do not use broker.list_releases, instead use "
        "broker.check_connection."
    ) == {
        f"mcp__broker-fixture__broker.{name}"
        for name in ["start", "discover", "read", "check_connection"]
    }
    assert manager.native_tool_names_for_request(
        "Using Acme Broker, use broker.start but do not use "
        "broker.list_releases."
    ) == {
        f"mcp__broker-fixture__broker.{name}"
        for name in ["start", "discover", "read"]
    }
    assert manager.native_tool_names_for_request(
        "Using Acme Broker, you must not use broker.list_releases."
    ) == {
        f"mcp__broker-fixture__broker.{name}"
        for name in ["start", "discover", "read"]
    }
    assert manager.native_tool_names_for_request(
        "Do not use broker.list_releases. Actually use broker.list_releases."
    ) == {
        f"mcp__broker-fixture__broker.{name}"
        for name in ["start", "discover", "read", "list_releases"]
    }
    assert manager.native_tool_names_for_request(
        "Using Acme Broker, avoid guessing IDs and use broker.check_connection."
    ) == {
        f"mcp__broker-fixture__broker.{name}"
        for name in ["start", "discover", "read", "check_connection"]
    }
    assert manager.native_tool_names_for_request(
        "Using Acme Broker, do not use broker.foo, broker.bar, broker.baz, "
        "or broker.list_releases."
    ) == {
        f"mcp__broker-fixture__broker.{name}"
        for name in ["start", "discover", "read"]
    }
    assert manager.native_tool_names_for_request(
        "Using Acme Broker, use broker.start, not broker.list_releases."
    ) == {
        f"mcp__broker-fixture__broker.{name}"
        for name in ["start", "discover", "read"]
    }
    assert manager.native_tool_names_for_request(
        "Using Acme Broker, use broker.start rather than broker.list_releases."
    ) == {
        f"mcp__broker-fixture__broker.{name}"
        for name in ["start", "discover", "read"]
    }
    assert manager.native_tool_names_for_request(
        "Using Acme Broker, not only broker.list_releases but also "
        "broker.check_connection."
    ) == {
        f"mcp__broker-fixture__broker.{name}"
        for name in ["start", "discover", "read", "list_releases", "check_connection"]
    }
    assert manager.native_tool_names_for_request(
        "Using Acme Broker, do not list releases."
    ) == {
        f"mcp__broker-fixture__broker.{name}"
        for name in ["start", "discover", "read"]
    }
    assert manager.native_tool_names_for_request(
        "Using Acme Broker, don't forget to use broker.list_releases."
    ) == {
        f"mcp__broker-fixture__broker.{name}"
        for name in ["start", "discover", "read", "list_releases"]
    }
    assert manager.native_tool_names_for_request(
        "Using Acme Broker, do not omit broker.list_releases."
    ) == {
        f"mcp__broker-fixture__broker.{name}"
        for name in ["start", "discover", "read", "list_releases"]
    }
    assert manager.native_tool_names_for_request(
        "Using Acme Broker, do not use broker.list_releases\n"
        "Use broker.check_connection."
    ) == {
        f"mcp__broker-fixture__broker.{name}"
        for name in ["start", "discover", "read", "check_connection"]
    }
    assert manager.native_tool_names_for_request(
        "Using Acme Broker, don't list any releases."
    ) == {
        f"mcp__broker-fixture__broker.{name}"
        for name in ["start", "discover", "read"]
    }
    assert manager.native_tool_names_for_request(
        "Using Acme Broker, do not use any tool except broker.list_releases."
    ) == {
        f"mcp__broker-fixture__broker.{name}"
        for name in ["start", "discover", "read", "list_releases"]
    }
    assert manager.native_tool_names_for_request(
        "Using Acme Broker, do not use any tool other than broker.list_releases."
    ) == {
        f"mcp__broker-fixture__broker.{name}"
        for name in ["start", "discover", "read", "list_releases"]
    }
    assert manager.native_tool_names_for_request(
        "Using Acme Broker, you won't use broker.list_releases."
    ) == {
        f"mcp__broker-fixture__broker.{name}"
        for name in ["start", "discover", "read"]
    }


def test_native_tool_failure_is_bounded_redacted_and_not_retried():
    class FailingSession:
        calls = 0

        async def call_tool(self, _name, _arguments):
            self.calls += 1
            raise RuntimeError(
                "service admission failed: Authorization: Bearer fixture-secret-token-123456789 "
                + "schema mismatch " * 100
            )

    manager = McpManager()
    session = FailingSession()
    manager._sessions["portal-fixture"] = session

    result = asyncio.run(manager.call_tool(
        "mcp__portal-fixture__portal.call_read_tool",
        {"service": "fixture"},
    ))

    assert result["exit_code"] == 1
    assert session.calls == 1
    assert "service admission failed" in result["error"]
    assert "schema mismatch" in result["error"]
    assert "fixture-secret-token" not in result["error"]
    assert len(result["error"]) <= 500


def test_native_mcp_prompt_forbids_provider_substitution_and_generic_fallbacks():
    assert "Preserve the provider, profile, channel, and target" in _NATIVE_MCP_DIRECT_RULES
    assert "Never substitute a different provider" in _NATIVE_MCP_DIRECT_RULES
    assert "Do not use manage_mcp, api_call, app_api, pipeline, shell, or curl" in _NATIVE_MCP_DIRECT_RULES
    assert "permission, service admission, or schema validation fails" in _NATIVE_MCP_DIRECT_RULES
