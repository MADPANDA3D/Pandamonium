from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
OBSERVER_PATH = ROOT / "services" / "vps-worker" / "jarvis_vps_observer.py"
SPEC = importlib.util.spec_from_file_location("jarvis_vps_observer", OBSERVER_PATH)
observer = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(observer)


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
    assert "User=jarvis-worker" in bridge
    assert "NoNewPrivileges=true" in bridge
    assert "SupplementaryGroups=jarvis-observer" in bridge
    assert "User=root" in observer_unit
    assert "RestrictAddressFamilies=AF_UNIX AF_NETLINK" in observer_unit
    assert "100." not in bridge


def test_worker_sources_do_not_embed_private_network_addresses():
    adapter_source = (ROOT / "src" / "agent_worker_adapters.py").read_text()
    bridge_source = (ROOT / "services" / "pc-codex-bridge" / "jarvis_codex_bridge.py").read_text()
    assert "192.168." not in adapter_source
    assert "100.119." not in adapter_source
    assert "/home/leo/" not in bridge_source
