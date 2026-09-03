import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from src.mcp_manager import _format_mcp_connection_error, _static_http_headers, McpManager


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
