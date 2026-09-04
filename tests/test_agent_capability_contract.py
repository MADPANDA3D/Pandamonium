"""Acceptance guards for platform truth and configured worker routing."""

import pytest

agent_loop = pytest.importorskip("src.agent_loop")


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
