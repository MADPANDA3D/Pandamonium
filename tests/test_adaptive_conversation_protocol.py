"""Regression coverage for the single adaptive conversation protocol."""

from pathlib import Path

from src.action_intents import resolve_conversation_execution_mode


ROOT = Path(__file__).resolve().parent.parent


def test_conversation_is_adaptive_without_a_user_mode_switch():
    """Normal turns can use tools while Compare keeps its explicit baselines."""
    for requested in ("", "adaptive", "chat", "agent", "unexpected"):
        assert resolve_conversation_execution_mode(requested) == "agent"

    assert resolve_conversation_execution_mode("chat", compare_mode=True) == "chat"
    assert resolve_conversation_execution_mode("agent", compare_mode=True) == "agent"

    index = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    chat_js = (ROOT / "static" / "js" / "chat.js").read_text(encoding="utf-8")
    app_js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    agent_loop = (ROOT / "src" / "agent_loop.py").read_text(encoding="utf-8")

    assert 'id="mode-agent-btn"' not in index
    assert 'id="mode-chat-btn"' not in index
    assert "Agent / Chat" not in index
    assert "fd.append('mode', 'adaptive')" in chat_js
    assert "initModeToggle" not in app_js
    assert "set_mode agent/chat" not in agent_loop
