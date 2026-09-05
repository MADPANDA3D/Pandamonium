"""Acceptance guards for platform truth and configured worker routing."""

import json
from pathlib import Path

import pytest

agent_loop = pytest.importorskip("src.agent_loop")

ROOT = Path(__file__).resolve().parents[1]
CAPABILITY_CONTRACT = ROOT / "specs" / "pandamonium-capability-authorization-contract.md"
DISCOVERY_SCHEMA = ROOT / "specs" / "schemas" / "pandamonium-discovery-v1.schema.json"

ENTITY_KINDS = {
    "model", "agent", "worker", "workspace", "knowledge_source",
    "connection", "plugin", "tool",
}
ACTION_EFFECTS = {
    "read",
    "reversible_write",
    "destructive_or_difficult_to_recover",
    "external_publication_or_communication",
    "purchase",
    "credential_or_auth_change",
    "privilege_expansion",
    "outside_workspace_boundary",
}


def _selected_tools(domains):
    tools = set()
    for domain in domains:
        tools.update(agent_loop._DOMAIN_TOOL_MAP.get(domain, set()))
    return tools


def test_failed_friday_acceptance_prompt_selects_real_worker_tools():
    prompt = (
        "Use Friday through the home-lab workspace for a read-only task. "
        "Report the exact working directory and current Git branch. Do not modify files."
    )

    intent = agent_loop._classify_agent_request([], prompt)
    selected = _selected_tools(intent["domains"])

    assert "workers" in intent["domains"]
    assert {"get_runtime_status", "start_agent_task", "read_agent_task"} <= selected
    assert "get_workspace" not in agent_loop._DOMAIN_TOOL_MAP["workers"]


def test_platform_architecture_prompt_requires_runtime_and_source_evidence():
    prompt = (
        "Explain how Pandamonium separates its UI, Jarvis OS protocols, "
        "persistent memory, worker agents, and replaceable model engine."
    )

    intent = agent_loop._classify_agent_request([], prompt)
    selected = _selected_tools(intent["domains"])
    rules = agent_loop._domain_rules_for_tools(selected)

    assert "platform" in intent["domains"]
    assert {"get_runtime_status", "manage_mcp", "start_agent_task"} <= selected
    assert any("Do not extrapolate frameworks" in rule for rule in rules)
    assert any("running `application_version`" in rule for rule in rules)
    assert any("KV cache" in rule and "MoE expert cache" in rule for rule in rules)
    assert all("embedding-matrix allocation" in rule for rule in rules if "context-capacity" in rule)


def test_worker_runtime_context_is_sanitized_and_uses_configured_labels(monkeypatch):
    monkeypatch.setattr(
        "src.agent_worker_adapters.worker_catalog",
        lambda: {
            "pc-codex": {
                "label": "Friday",
                "configured": True,
                "enabled": True,
                "ready": True,
                "workspaces": ["home-lab"],
                "url": "https://private.example.test",
                "token": "super-private-token-123",
            },
            "unused": {
                "label": "Unused",
                "configured": False,
                "enabled": False,
                "ready": False,
                "workspaces": [],
            },
        },
    )

    message = agent_loop._worker_catalog_context_message()
    assert message is not None
    payload = message["content"]
    assert "Friday" in payload
    assert "home-lab" in payload
    assert "private.example.test" not in payload
    assert "super-private-token-123" not in payload
    assert "Unused" not in payload
    assert '"id": "pc-codex"' in payload


def test_books_rules_reject_placeholder_content_queries():
    rules = agent_loop._DOMAIN_RULES["books"]

    assert 'stop word such as "the"' in rules
    assert "ask what the user wants searched" in rules


def test_pandamonium_discovery_contract_covers_all_entities_and_effects():
    schema = json.loads(DISCOVERY_SCHEMA.read_text(encoding="utf-8"))

    assert schema["properties"]["schema_version"]["const"] == "pandamonium.discovery.v1"
    entity = schema["$defs"]["entity"]
    assert set(entity["properties"]["kind"]["enum"]) == ENTITY_KINDS
    assert set(schema["$defs"]["action"]["properties"]["effect"]["enum"]) == ACTION_EFFECTS

    example_entities = schema["examples"][0]["entities"]
    assert {item["kind"] for item in example_entities} == ENTITY_KINDS
    assert len({(item["kind"], item["id"]) for item in example_entities}) == len(ENTITY_KINDS)
    for item in example_entities:
        assert item["permissions"]["requires_authenticated_request"] is True
        assert item["permissions"]["configured_scopes"]
        assert item["source"]["ref"]
        source_path = item["source"]["ref"].split("#", 1)[0]
        assert (ROOT / source_path).is_file(), source_path


def test_pandamonium_contract_is_repo_grounded_and_channel_neutral():
    contract = CAPABILITY_CONTRACT.read_text(encoding="utf-8")

    for path in (
        "routes/chat_routes.py",
        "routes/voice_routes.py",
        "routes/mcp_routes.py",
        "routes/extension_routes.py",
        "routes/model_routes.py",
        "routes/agent_task_routes.py",
        "src/authority_protocol.py",
        "src/agent_worker_adapters.py",
        "static/js/chatRenderer.js",
        "static/js/jarvisVoice.js",
    ):
        assert path in contract
        assert (ROOT / path).is_file(), path

    for required_rule in (
        "authenticated operator's explicit request",
        "Destructive or difficult to recover",
        "External publication or communication",
        "Purchase",
        "Credential or authentication change",
        "Privilege expansion",
        "Outside configured Workspace boundary",
        "approve-once/deny",
        "ORACLE and every other fullscreen extension must yield",
        "MAD-779 is the successor; no MAD-779 implementation",
    ):
        assert required_rule in contract
