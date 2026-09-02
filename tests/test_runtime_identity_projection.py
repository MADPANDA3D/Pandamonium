"""Reference-neutral regression coverage for reduced runtime prompt paths."""

from types import SimpleNamespace

import src.agent_identity as agent_identity
import src.agent_loop as agent_loop


def _atlas_settings():
    return {
        "agent_id": "atlas",
        "agent_display_name": "Atlas",
        "agent_constitution": "Remain truthful about identity and runtime capabilities.",
        "agent_constitution_version": "2026.1",
    }


def test_reduced_prompt_paths_keep_configured_identity_model_independent(monkeypatch):
    monkeypatch.setattr(agent_identity, "load_settings", _atlas_settings)
    messages = [{"role": "user", "content": "Who are you?"}]
    document = SimpleNamespace(title="Draft", language="markdown", current_content="Hello")

    prompt_sets = (
        agent_loop._minimal_plain_chat_messages(messages),
        agent_loop._minimal_odysseus_general_messages(messages),
        agent_loop._minimal_odysseus_notes_messages(messages),
        agent_loop._minimal_odysseus_doc_messages(messages, document),
    )

    for prompt_messages in prompt_sets:
        system_prompt = prompt_messages[0]["content"]
        assert "persistent agent identity is Atlas" in system_prompt
        assert "identify yourself as Atlas" in system_prompt
        assert "replaceable reasoning engines" in system_prompt
        assert "You are Odysseus" not in system_prompt
        assert prompt_messages[-1] == {"role": "user", "content": "Who are you?"}
