"""Core JOS protocols mount into agent prompts and trace through P7."""

import src.agent_identity as agent_identity
import src.operational_protocol as operational_protocol
import src.protocol_registry as registry


def _installation_identity(**overrides):
    values = {
        "agent_id": "atlas",
        "agent_display_name": "Atlas",
        "agent_constitution": "Stay accurate and respect operator approval boundaries.",
        "agent_constitution_version": "2026.1",
    }
    values.update(overrides)
    return values


def test_core_protocols_mount_in_agent_system_prompt(monkeypatch):
    monkeypatch.setattr(agent_identity, "load_settings", lambda: _installation_identity())

    prompt = agent_identity.agent_system_prompt("Be concise.", model="test-engine")

    assert "Operating protocols" in prompt
    assert "JOS-P0" in prompt
    assert "JOS-P1" in prompt
    assert "Be concise." in prompt
    assert prompt.index("Operating protocols") < prompt.index("Be concise.")


def test_disabling_layer_restores_prior_prompt(monkeypatch):
    monkeypatch.setattr(
        agent_identity, "load_settings", lambda: _installation_identity(protocol_layer_enabled=False)
    )
    monkeypatch.setattr(registry, "load_settings", lambda: {"protocol_layer_enabled": False})

    prompt = agent_identity.agent_system_prompt("Be concise.")

    assert "Operating protocols" not in prompt
    assert "persistent agent identity is Atlas" in prompt
    assert prompt.endswith("Be concise.")


def test_prompt_trace_records_mounted_protocols(monkeypatch):
    monkeypatch.setattr(agent_identity, "load_settings", lambda: _installation_identity())
    recorded = []
    monkeypatch.setattr(
        operational_protocol,
        "record_protocol_mount",
        lambda **kwargs: recorded.append(kwargs) or {"event_id": "test"},
    )

    agent_identity.agent_system_prompt("hello", trace_surface="chat")

    assert recorded
    assert recorded[0]["surface"] == "chat"
    assert {pack["id"] for pack in recorded[0]["packs"]} == {"jos-p0-engine", "jos-p1-identity"}


def test_protocol_mount_event_is_fail_soft_and_skips_empty(monkeypatch):
    recorded = []
    monkeypatch.setattr(
        operational_protocol,
        "record_operational_event",
        lambda **kwargs: recorded.append(kwargs) or {"event_id": "test"},
    )

    assert operational_protocol.record_protocol_mount(surface="chat", packs=[]) is None
    event = operational_protocol.record_protocol_mount(
        surface="voice", packs=[{"id": "jos-p0-engine", "version": "0.2"}]
    )

    assert event is not None
    assert recorded[0]["component"] == "protocol-registry"
    assert recorded[0]["metadata"]["mounted_protocols"] == ["jos-p0-engine@0.2"]
    assert recorded[0]["metadata"]["surface"] == "voice"
