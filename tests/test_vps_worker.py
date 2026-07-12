from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
OBSERVER_PATH = ROOT / "services" / "vps-worker" / "jarvis_vps_observer.py"
SPEC = importlib.util.spec_from_file_location("jarvis_vps_observer", OBSERVER_PATH)
observer = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(observer)

MCP_PATH = ROOT / "services" / "vps-worker" / "jarvis_vps_observer_mcp.py"
sys.path.insert(0, str(MCP_PATH.parent))
MCP_SPEC = importlib.util.spec_from_file_location("jarvis_vps_observer_mcp", MCP_PATH)
mcp = importlib.util.module_from_spec(MCP_SPEC)
assert MCP_SPEC and MCP_SPEC.loader
MCP_SPEC.loader.exec_module(mcp)


def test_observer_exposes_only_fixed_read_only_actions():
    assert set(observer.ACTIONS) == {
        "health", "resources", "ports", "services", "journal", "containers", "nginx", "deployments"
    }
    assert not {"shell", "command", "restart", "delete", "install"} & set(observer.ACTIONS)


def test_observer_rejects_unlisted_service():
    with pytest.raises(ValueError, match="service_not_allowed"):
        observer.observe_services({"target": ["arbitrary.service"]})
    with pytest.raises(ValueError, match="service_not_allowed"):
        observer.observe_journal({"target": ["../../etc/shadow"]})


def test_vps_worker_units_preserve_privilege_boundary():
    bridge = (ROOT / "services" / "vps-worker" / "jarvis-vps-codex.service").read_text()
    observer_unit = (ROOT / "services" / "vps-worker" / "jarvis-vps-observer.service").read_text()
    apparmor = (ROOT / "services" / "vps-worker" / "apparmor.jarvis-vps-codex-bwrap").read_text()
    assert "User=jarvis-worker" in bridge
    assert "NoNewPrivileges=true" in bridge
    assert "SupplementaryGroups=jarvis-observer" in bridge
    assert "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK" in bridge
    assert "JARVIS_CODEX_BIN=/home/jarvis-worker/.local/bin/codex" in bridge
    assert "User=root" in observer_unit
    assert "RestrictAddressFamilies=AF_UNIX AF_NETLINK" in observer_unit
    assert "userns," in apparmor
    assert "/home/jarvis-worker/.local/lib/node_modules/@openai/codex/" in apparmor
    assert "100." not in bridge


def test_worker_sources_do_not_embed_private_network_addresses():
    adapter_source = (ROOT / "src" / "agent_worker_adapters.py").read_text()
    agent_source = (ROOT / "src" / "jarvis_agent.py").read_text()
    voice_source = (ROOT / "routes" / "voice_routes.py").read_text()
    bridge_source = (ROOT / "services" / "pc-codex-bridge" / "jarvis_codex_bridge.py").read_text()
    reusable_source = "\n".join((adapter_source, agent_source, voice_source))
    assert "192.168." not in reusable_source
    assert "100.119." not in reusable_source
    assert "MADPANDA3D" not in reusable_source
    assert "/home/leo/" not in bridge_source


def test_observer_mcp_is_read_only_and_has_no_generic_shell(monkeypatch):
    assert len(mcp.TOOLS) == 8
    assert not {"shell", "command", "restart", "install", "delete"} & set(mcp.TOOL_ACTIONS)
    assert all(tool["annotations"] == mcp.ANNOTATIONS for tool in mcp.TOOLS)
    monkeypatch.setattr(mcp, "request", lambda action, target="", lines=80: {
        "ok": True, "action": action, "target": target, "lines": lines,
    })
    response = mcp.handle({
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {"name": "server_service_status", "arguments": {"target": "tailscaled.service"}},
    })
    assert response["result"]["structuredContent"] == {
        "ok": True,
        "action": "services",
        "target": "tailscaled.service",
        "lines": 80,
    }
