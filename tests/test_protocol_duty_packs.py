"""Duty protocol packs mount by intent with a real budget (MAD-891)."""

import src.agent_identity as agent_identity
import src.protocol_registry as registry

INTENT_DOMAINS = {"calendar", "notes", "email", "ui", "integrations", "web", "research", "shell"}


def _installation_identity(**overrides):
    values = {
        "agent_id": "atlas",
        "agent_display_name": "Atlas",
        "agent_constitution": "Stay accurate.",
        "agent_constitution_version": "2026.1",
    }
    values.update(overrides)
    return values


def test_shipped_duty_packs_load_and_declare_real_domains():
    packs = registry.load_protocol_packs()
    duty = [pack for pack in packs if pack.scope == registry.DUTY_SCOPE]
    ids = {pack.id for pack in duty}
    assert {
        "jos-p2-context",
        "jos-p3-memory",
        "jos-p4-actions",
        "jos-p5-authority",
        "jos-p6-learning",
        "jos-p7-observability",
        "jos-ipav",
    } <= ids
    for pack in duty:
        assert pack.ok, pack.error
        assert set(pack.domains) <= INTENT_DOMAINS, pack.id


def test_every_duty_pack_body_fits_its_declared_budget():
    for pack in registry.load_protocol_packs():
        if pack.scope == registry.DUTY_SCOPE and pack.ok:
            estimate = max(1, int(len(pack.body) * 0.3))
            assert estimate <= pack.token_budget, (pack.id, estimate, pack.token_budget)


def test_duty_selection_matrix():
    shell = {pack.id for pack in registry.select_duty_protocol_packs(["shell"])}
    assert {"jos-p4-actions", "jos-p5-authority", "jos-p7-observability", "jos-ipav"} <= shell
    assert "jos-p2-context" not in shell

    web = {pack.id for pack in registry.select_duty_protocol_packs(["web"])}
    assert "jos-p2-context" in web
    assert "jos-p5-authority" not in web

    assert registry.select_duty_protocol_packs([]) == []
    assert registry.select_duty_protocol_packs(["unknown-domain"]) == []


def test_trivial_chat_mounts_core_only(monkeypatch):
    monkeypatch.setattr(agent_identity, "load_settings", lambda: _installation_identity())

    prompt = agent_identity.agent_system_prompt("hello there")

    assert "JOS-P0" in prompt and "JOS-P1" in prompt
    assert "JOS-P4" not in prompt
    assert "JOS-IPAV" not in prompt


def test_action_turn_mounts_duty_packs(monkeypatch):
    monkeypatch.setattr(agent_identity, "load_settings", lambda: _installation_identity())

    prompt = agent_identity.agent_system_prompt(
        "run uptime on the server", protocol_domains=["shell"]
    )

    assert "JOS-P4" in prompt
    assert "JOS-P5" in prompt
    assert "JOS-IPAV" in prompt


def test_duty_trace_includes_mounted_packs(monkeypatch):
    monkeypatch.setattr(agent_identity, "load_settings", lambda: _installation_identity())
    import src.operational_protocol as operational_protocol

    recorded = []
    monkeypatch.setattr(
        operational_protocol,
        "record_protocol_mount",
        lambda **kwargs: recorded.append(kwargs) or {"ok": True},
    )

    agent_identity.agent_system_prompt(
        "run uptime", trace_surface="agent", protocol_domains=["shell"]
    )

    ids = {pack["id"] for pack in recorded[0]["packs"]}
    assert {"jos-p0-engine", "jos-p1-identity", "jos-p4-actions", "jos-ipav"} <= ids


def test_render_budget_drops_overflow_packs():
    packs = registry.select_protocol_packs(registry.CORE_SCOPE) + registry.select_duty_protocol_packs(
        ["shell"]
    )

    generous = registry.render_protocol_block(packs)
    assert "JOS-IPAV" in generous

    tight = registry.render_protocol_block(packs, max_tokens=450)
    assert "JOS-P0" in tight
    assert "JOS-IPAV" not in tight
