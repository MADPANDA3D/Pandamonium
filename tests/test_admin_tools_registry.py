"""Registry wiring for the config/integration admin tools (#3629).

manage_endpoints/mcp/webhooks/tokens/settings moved from tool_implementations
into agent_tools.admin_tools. These pin the registration + the single
owner-threading adapter factory, without touching the DB (the do_* impls
themselves are exercised by their own suites).
"""
import asyncio
import json

from src.agent_tools import TOOL_HANDLERS
from src.agent_tools.admin_tools import (
    ADMIN_TOOL_HANDLERS, _owner_adapter,
    do_manage_endpoints, do_manage_mcp, do_manage_webhooks,
    do_manage_tokens, do_manage_settings,
)

_NAMES = ["manage_endpoints", "manage_mcp", "manage_webhooks", "manage_tokens", "manage_settings"]


def test_all_registered_in_tool_handlers():
    for n in _NAMES:
        assert n in TOOL_HANDLERS, f"{n} missing from TOOL_HANDLERS"
        assert n in ADMIN_TOOL_HANDLERS


def test_re_exported_from_agent_tools():
    # Back-compat: importers that used `from src.agent_tools import do_manage_*`
    # keep working after the move.
    from src.agent_tools import (  # noqa: F401
        do_manage_endpoints, do_manage_mcp, do_manage_webhooks,
        do_manage_tokens, do_manage_settings,
    )


def test_owner_adapter_threads_owner_from_ctx():
    seen = {}

    async def _spy(content, owner):
        seen["content"] = content
        seen["owner"] = owner
        return {"response": "ok", "exit_code": 0}

    handler = _owner_adapter(_spy)
    res = asyncio.run(handler('{"action":"list"}', {"owner": "alice", "session_id": "s1"}))
    assert res["exit_code"] == 0
    assert seen == {"content": '{"action":"list"}', "owner": "alice"}


def test_owner_adapter_defaults_owner_to_none():
    captured = {}

    async def _spy(content, owner):
        captured["owner"] = owner
        return {"exit_code": 0}

    asyncio.run(_owner_adapter(_spy)("{}", {}))  # ctx without owner
    assert captured["owner"] is None


def test_parse_tool_args_lives_in_tool_utils_single_source():
    # The helper was de-duplicated into tool_utils; every consumer imports it
    # from there rather than carrying its own copy. After the tool_implementations
    # split, _common and the facade must also re-export the same object.
    from src.tool_utils import _parse_tool_args
    from src.agent_tools import admin_tools, document_tools
    from src.tools import _common
    import src.tool_implementations as ti
    assert admin_tools._parse_tool_args is _parse_tool_args
    assert document_tools._parse_tool_args is _parse_tool_args
    assert _common._parse_tool_args is _parse_tool_args
    assert ti._parse_tool_args is _parse_tool_args
    assert _parse_tool_args('{"action":"add"}') == {"action": "add"}
    # body-envelope unwrap still works
    assert _parse_tool_args('{"body":{"action":"x"}}') == {"action": "x"}

    # non-dict JSON values should return {}
    assert _parse_tool_args('[1, 2]') == {}
    assert _parse_tool_args('42') == {}
    assert _parse_tool_args('"hello"') == {}


def test_manage_mcp_inventory_groups_live_integrations(monkeypatch):
    class FakeMcp:
        def get_all_statuses(self):
            return {
                "mad-mcp-portal": {
                    "name": "MAD MCP Portal",
                    "status": "connected",
                    "tool_count": 2,
                },
                "email": {"name": "Built-in: Email", "status": "connected", "tool_count": 1},
            }

        def get_all_tools(self):
            return [
                {"server_id": "mad-mcp-portal", "server_name": "MAD MCP Portal", "name": "portal.list_services", "is_disabled": False},
                {"server_id": "mad-mcp-portal", "server_name": "MAD MCP Portal", "name": "portal.call_read_tool", "is_disabled": False},
                {"server_id": "email", "server_name": "Built-in: Email", "name": "read_email", "is_disabled": False},
            ]

        def is_builtin(self, server_id):
            return server_id == "email"

        def is_extension_server(self, _server_id):
            return False

    class FakeRegistry:
        def snapshot(self):
            return {
                "extensions": {
                    "oracle": {
                        "enabled": True,
                        "manifest": {"name": "ORACLE", "runtime": {"type": "web"}},
                        "effective_capabilities": [{"name": "inspect_scene"}],
                        "admitted_skills": [],
                    }
                }
            }

    monkeypatch.setattr("src.agent_tools.admin_tools.get_mcp_manager", lambda: FakeMcp())
    monkeypatch.setattr("src.extension_registry.ExtensionRegistry", FakeRegistry)
    monkeypatch.setattr(
        "src.integrations.load_integrations",
        lambda: [{"id": "home", "name": "Home Assistant", "enabled": True, "api_key": "never-return"}],
    )

    result = asyncio.run(do_manage_mcp(json.dumps({"action": "inventory"})))

    assert result["exit_code"] == 0
    assert result["mcp_servers"] == [{
        "id": "mad-mcp-portal",
        "name": "MAD MCP Portal",
        "status": "connected",
        "capabilities": ["portal.call_read_tool", "portal.list_services"],
    }]
    assert result["extensions"] == [{
        "id": "oracle",
        "name": "ORACLE",
        "enabled": True,
        "runtime": "web",
        "capabilities": ["inspect_scene"],
        "skills": [],
    }]
    assert result["api_integrations"] == [{"id": "home", "name": "Home Assistant"}]
    assert "never-return" not in json.dumps(result)
