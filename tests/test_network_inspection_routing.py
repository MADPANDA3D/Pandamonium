"""Regression coverage for constrained current-network inspection."""

import asyncio

from src import agent_loop
from src.action_protocol import _READ_ONLY
from src.agent_tools import TOOL_HANDLERS, TOOL_TAGS
from src.agent_tools import network_tools
from src.authority_protocol import action_effect_for
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
from src.tool_security import NON_ADMIN_BLOCKED_TOOLS, PLAN_MODE_READONLY_TOOLS


REPRO = "Ok Jarvis make a diagram of my network please"


def _selected_tools(intent):
    tools = set()
    for domain in intent["domains"]:
        tools.update(agent_loop._DOMAIN_TOOL_MAP.get(domain, set()))
    return tools


def test_current_network_diagram_mounts_read_only_inspection_capability():
    intent = agent_loop._classify_agent_request([], REPRO)
    selected = _selected_tools(intent)

    assert intent["low_signal"] is False
    assert intent["domains"] == {"network_inspection"}
    assert selected == {"inspect_network"}
    assert any(
        "fixed, bounded, read-only probes" in rule
        for rule in agent_loop._domain_rules_for_tools(selected)
    )


def test_network_scope_followups_keep_the_original_inspection_intent():
    first_followup = "High‑level overview (e.g., LAN, Wi‑Fi, internet)"
    messages = [
        {"role": "user", "content": REPRO},
        {
            "role": "assistant",
            "content": (
                "To create an accurate network diagram, could you tell me "
                "what elements you'd like included?"
            ),
        },
        {"role": "user", "content": first_followup},
    ]
    first_intent = agent_loop._classify_agent_request(messages, first_followup)

    assert first_intent["continuation"] is True
    assert "network_inspection" in first_intent["domains"]
    assert "inspect_network" in _selected_tools(first_intent)

    correction = (
        "That is not the network you're on; you need to run commands to verify it."
    )
    second_followup = "Provide detailed components"
    messages.extend([
        {"role": "assistant", "content": "Here is a typical home network."},
        {"role": "user", "content": correction},
        {
            "role": "assistant",
            "content": (
                "Could you provide the specific components or confirm the "
                "high-level layout?"
            ),
        },
        {"role": "user", "content": second_followup},
    ])
    second_intent = agent_loop._classify_agent_request(messages, second_followup)

    assert second_intent["continuation"] is True
    assert "network_inspection" in second_intent["domains"]
    assert "inspect_network" in _selected_tools(second_intent)


def test_first_person_current_network_phrasing_mounts_only_inspection_tool():
    prompts = (
        "Map the network I'm on",
        "Show the Wi-Fi I am connected to",
        "Draw the LAN I’m using",
    )

    for prompt in prompts:
        intent = agent_loop._classify_agent_request([], prompt)
        assert "network_inspection" in intent["domains"]
        assert "files" not in intent["domains"]
        selected = _selected_tools(intent)
        assert "inspect_network" in selected
        assert selected.isdisjoint(agent_loop._DOMAIN_TOOL_MAP["files"])


def test_inspection_tool_is_parameterless_owner_scoped_and_read_only():
    schema = next(
        item["function"]
        for item in FUNCTION_TOOL_SCHEMAS
        if item["function"]["name"] == "inspect_network"
    )

    assert schema["parameters"] == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    assert "inspect_network" in TOOL_TAGS
    assert "inspect_network" in TOOL_HANDLERS
    assert "inspect_network" in PLAN_MODE_READONLY_TOOLS
    assert "inspect_network" in NON_ADMIN_BLOCKED_TOOLS
    assert "inspect_network" in _READ_ONLY
    assert action_effect_for({"name": "inspect_network", "arguments": {}}) == "read"


def test_inspection_ignores_supplied_commands_and_executes_only_fixed_probes(monkeypatch):
    calls = []

    class FakeProcess:
        returncode = 0

        async def communicate(self):
            return b"verified output", b""

        def kill(self):
            raise AssertionError("a successful fixed probe must not be killed")

    async def fake_create_subprocess_exec(*argv, **kwargs):
        calls.append((argv, kwargs))
        return FakeProcess()

    monkeypatch.setattr(network_tools, "_resolve_executable", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = asyncio.run(
        network_tools.NetworkInspectionTool().execute(
            '{"command":"touch /tmp/should-not-run","path":"/etc/shadow"}',
            {"owner": "leo"},
        )
    )

    expected = [
        (f"/usr/bin/{argv[0]}", *argv[1:])
        for _name, argv in network_tools.NETWORK_PROBES
    ]
    assert [argv for argv, _kwargs in calls] == expected
    assert all(kwargs["stdout"] == asyncio.subprocess.PIPE for _argv, kwargs in calls)
    assert result["inspection_available"] is True
    assert result["exit_code"] == 0
    assert "touch" not in result["output"]


def test_unavailable_inspection_requires_an_honest_limitation():
    requirement = "If inspection is unavailable or fails"

    assert requirement in agent_loop._AGENT_RULES
    assert requirement in agent_loop._API_AGENT_RULES
    assert "do not fill gaps with a typical setup or memory" in agent_loop._AGENT_RULES


def test_conceptual_network_question_does_not_mount_shell_tools():
    intent = agent_loop._classify_agent_request([], "Explain what a network topology is")

    assert "files" not in intent["domains"]
    assert "bash" not in _selected_tools(intent)
    assert "network_inspection" not in intent["domains"]
