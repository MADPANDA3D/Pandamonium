import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from src.agent_loop import _NATIVE_MCP_DIRECT_RULES
from src.authority_protocol import action_effect_for
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
        ["-y", "@playwright/mcp@latest", "--headless"],
        RuntimeError("package not found"),
    )

    assert "package not found" in msg
    assert "Browser MCP could not start" in msg
    assert "npx -y @playwright/mcp@latest --version" in msg
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


def test_readonly_mcp_health_tools_receive_authority_metadata_and_unknowns_fail_closed():
    manager = McpManager()
    manager._tools["portal"] = [
        {"name": "get_health", "annotations": {"readOnlyHint": True}},
        {"name": "status_services", "annotations": None},
        {"name": "post_message", "annotations": None},
    ]
    manager._connections["portal"] = {"name": "MAD MCP Portal"}

    policies = manager.get_readonly_action_policies()

    assert policies == {
        "mcp__portal__get_health": {"action_effect": "read"},
        "mcp__portal__status_services": {"action_effect": "read"},
    }
    read_call = {
        "name": "mcp__portal__get_health",
        "target": "mcp",
        "arguments": {},
        "capability_policy": policies["mcp__portal__get_health"],
    }
    unknown_call = {"name": "mcp__portal__post_message", "target": "mcp", "arguments": {}}
    assert action_effect_for(read_call) == "read"
    assert action_effect_for(unknown_call) == "unclassified"


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


def test_tool_cross_references_do_not_replace_an_uninstructed_broker_entrypoint():
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
        f"mcp__portal-fixture__{name}" for name in entrypoints
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
