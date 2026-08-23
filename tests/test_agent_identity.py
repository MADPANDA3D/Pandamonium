from src.agent_identity import JARVIS_SYSTEM_PROMPT, is_jarvis_model, jarvis_chat_prompt


def test_jarvis_identity_tracks_agent_model_names():
    assert is_jarvis_model("jarvis") is True
    assert is_jarvis_model("qwen3.5-jarvis-v5:latest") is True
    assert is_jarvis_model("gordon") is False
    assert is_jarvis_model("gpt-5.5") is False


def test_jarvis_identity_is_prepended_without_losing_active_preset():
    prompt = jarvis_chat_prompt("jarvis", "Be concise.")

    assert prompt == f"{JARVIS_SYSTEM_PROMPT}\n\nBe concise."
    assert jarvis_chat_prompt("gpt-5.5", "Be concise.") == "Be concise."
    assert jarvis_chat_prompt("gordon", None) is None
