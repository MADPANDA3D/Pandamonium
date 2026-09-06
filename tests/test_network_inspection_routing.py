"""Regression coverage for current-network inspection routing."""

from src import agent_loop


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
    assert "files" in intent["domains"]
    assert "bash" in selected
    assert any(
        "inspect with read-only commands" in rule
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
    assert "files" in first_intent["domains"]
    assert "bash" in _selected_tools(first_intent)

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
    assert "files" in second_intent["domains"]
    assert "bash" in _selected_tools(second_intent)


def test_unavailable_inspection_requires_an_honest_limitation():
    requirement = "If inspection is unavailable or fails"

    assert requirement in agent_loop._AGENT_RULES
    assert requirement in agent_loop._API_AGENT_RULES
    assert "do not fill gaps with a typical setup or memory" in agent_loop._AGENT_RULES


def test_conceptual_network_question_does_not_mount_shell_tools():
    intent = agent_loop._classify_agent_request([], "Explain what a network topology is")

    assert "files" not in intent["domains"]
    assert "bash" not in _selected_tools(intent)
