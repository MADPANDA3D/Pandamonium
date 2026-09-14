"""MAD-861: voice greetings follow the configured identity/model path.

The legacy deterministic "Jarvis" greeting must not run on a clean install.
It remains only as an explicit, installation-configurable low-latency mode
that uses the saved display name and stays generic when no identity is saved.
"""
import json
from types import SimpleNamespace

import pytest

import src.settings as settings_module
from routes import voice_routes


@pytest.fixture
def isolated_settings(monkeypatch, tmp_path):
    target = tmp_path / "settings.json"
    monkeypatch.setattr(settings_module, "SETTINGS_FILE", str(target))
    settings_module._invalidate_caches()
    yield target
    settings_module._invalidate_caches()


def _configure_identity(name: str = "Atlas") -> None:
    settings_module.save_settings({
        **settings_module.DEFAULT_SETTINGS,
        "agent_id": name.casefold(),
        "agent_display_name": name,
    })


def _enable_deterministic_greeting() -> None:
    current = settings_module.load_settings()
    current["voice_deterministic_greeting"] = True
    settings_module.save_settings(current)


def _model_session():
    return SimpleNamespace(
        endpoint_url="http://jarvis.test/v1/chat/completions",
        model="jarvis-model",
        headers={},
        get_context_messages=lambda: [{"role": "user", "content": "Hello"}],
    )


def _patch_model_session(monkeypatch):
    monkeypatch.setattr(
        voice_routes,
        "_SESSION_MANAGER",
        SimpleNamespace(get_session=lambda _session_id: _model_session()),
    )


async def _collect(gen):
    return [event async for event in gen]


# ── Default behavior: the model answers greetings ─────────────────────────

def test_deterministic_greeting_is_off_by_default(isolated_settings):
    assert voice_routes._deterministic_greeting_enabled() is False


def test_deterministic_greeting_is_installation_configurable(isolated_settings):
    _enable_deterministic_greeting()
    assert voice_routes._deterministic_greeting_enabled() is True


@pytest.mark.asyncio
async def test_casual_greeting_reaches_the_model_by_default(monkeypatch, isolated_settings):
    _patch_model_session(monkeypatch)
    calls = []

    async def model_stream(_endpoint_url, _model, messages, **kwargs):
        calls.append(messages)
        yield 'data: {"delta":"Hello from the model."}'
        yield 'data: {"type":"metrics","data":{}}'
        yield "data: [DONE]"

    def must_not_use_server_greeting(*_args, **_kwargs):
        raise AssertionError("a casual greeting silently bypassed the model")

    monkeypatch.setattr(voice_routes, "stream_agent_loop", model_stream)
    monkeypatch.setattr(voice_routes, "_casual_greeting_reply", must_not_use_server_greeting)

    events = await _collect(voice_routes._jarvis_events(
        "chat-1", "Hello there", "leo", {"target": "jarvis"},
    ))

    assert calls, "the model must handle a casual greeting by default"
    assert events[-1]["assistant_text"] == "Hello from the model."
    assert events[-1]["diagnostics"]["guard_reason"] != "casual_greeting"


@pytest.mark.asyncio
async def test_greeting_does_not_bypass_the_model_in_server_routed_events(
    monkeypatch, isolated_settings,
):
    def must_not_use_server_greeting(*_args, **_kwargs):
        raise AssertionError("a casual greeting silently bypassed the model")

    monkeypatch.setattr(voice_routes, "_casual_greeting_reply", must_not_use_server_greeting)

    events = await _collect(voice_routes._server_routed_events(
        "chat-1", "Hey, how are you?", "leo", {"target": "jarvis"},
    ))

    assert events == []


# ── Explicit deterministic mode uses the saved display name ───────────────

@pytest.mark.asyncio
async def test_deterministic_greeting_uses_saved_display_name(monkeypatch, isolated_settings):
    _configure_identity("Atlas")
    _enable_deterministic_greeting()
    _patch_model_session(monkeypatch)

    async def must_not_call_model(*_args, **_kwargs):
        raise AssertionError("deterministic mode must not call the model")
        yield  # pragma: no cover

    monkeypatch.setattr(voice_routes, "stream_agent_loop", must_not_call_model)

    events = await _collect(voice_routes._jarvis_events(
        "chat-1", "Good morning", "leo", {"target": "jarvis"},
    ))

    reply = events[-1]["assistant_text"]
    assert "Atlas" in reply
    assert "Jarvis" not in reply
    assert "Leo" not in reply
    assert events[-1]["diagnostics"]["guard_reason"] == "casual_greeting"


def test_clean_install_greeting_is_generic_and_requires_no_private_name(
    monkeypatch, isolated_settings,
):
    # A clean fixture with a different agent name must not require any private
    # name or phrase: the default identity is the public "Assistant".
    _enable_deterministic_greeting()

    for text in ("Good evening", "Hey, how are you?", "Hello there"):
        reply = voice_routes._casual_greeting_reply(text, {"turns": []})
        assert "Jarvis" not in reply
        assert "Leo" not in reply
        assert "Good evening" in reply or "doing well" in reply or "Good to hear" in reply


def test_clean_fixture_with_different_agent_name_uses_that_name(
    monkeypatch, isolated_settings,
):
    _configure_identity("Differently Named Agent")
    _enable_deterministic_greeting()
    reply = voice_routes._casual_greeting_reply("Hello there", {"turns": []})
    assert "Differently Named Agent" in reply
    assert "Jarvis" not in reply
    assert "Leo" not in reply


def test_saved_private_installation_keeps_its_chosen_identity(
    monkeypatch, isolated_settings,
):
    # Backward-compatible: a private installation that already saved an
    # identity keeps it; the new generic default does not rewrite it, and the
    # deterministic mode stays off until the operator enables it.
    isolated_settings.write_text(json.dumps({
        "agent_id": "jarvis",
        "agent_display_name": "Jarvis",
    }), encoding="utf-8")
    settings_module._invalidate_caches()

    assert voice_routes._deterministic_greeting_enabled() is False
    assert voice_routes._voice_character_name({"target": "jarvis"}) == "Jarvis"
    reply = voice_routes._casual_greeting_reply("Hello there", {"turns": []})
    assert "Jarvis" in reply
    assert "Leo" not in reply


# ── Identity instead of hardcoded Jarvis/Leo in voice copy ────────────────

@pytest.mark.asyncio
async def test_target_switch_to_agent_uses_saved_identity(monkeypatch, isolated_settings):
    _configure_identity("Atlas")

    # The full suite can initialize a real session manager that has no
    # "chat-1" (created lazily from the DB); the voice target-switch path must
    # tolerate a missing chat session instead of raising KeyError.
    class _MissingSessionManager:
        @staticmethod
        def get_session(_session_id):
            raise KeyError("Session chat-1 not found")

    monkeypatch.setattr(voice_routes, "_SESSION_MANAGER", _MissingSessionManager())
    monkeypatch.setattr(
        voice_routes,
        "_resolve_voice_target_endpoint",
        lambda _target, _owner: ("http://jarvis.test/v1/chat/completions", "jarvis-model", {}),
    )

    events = await _collect(voice_routes._server_routed_events(
        "chat-1", "Please switch me back to Jarvis", "leo",
        {"target": "pc-codex", "origin_target": "pc-codex"},
    ))

    final = events[-1]
    assert final["diagnostics"]["guard_reason"] == "target_switch_jarvis"
    assert "Jarvis" not in final["assistant_text"]
    assert "Leo" not in final["assistant_text"]
    assert "Atlas" in final["assistant_text"]


def test_voice_character_name_uses_saved_identity_for_the_direct_agent(
    isolated_settings,
):
    _configure_identity("Atlas")
    assert voice_routes._voice_character_name({"target": "jarvis"}) == "Atlas"
    # An unknown/clean identity is the public default, never a private label.
    isolated_settings.write_text("{}", encoding="utf-8")
    settings_module._invalidate_caches()
    assert voice_routes._voice_character_name({"target": "jarvis"}) == "Assistant"


def test_static_voice_ui_ships_generic_labels():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    index = (root / "static" / "index.html").read_text(encoding="utf-8")
    voice_js = (root / "static" / "js" / "jarvisVoice.js").read_text(encoding="utf-8")
    settings_js = (root / "static" / "js" / "settings.js").read_text(encoding="utf-8")

    voice_section = index[index.index('id="jarvis-call-panel"'):]
    voice_section = voice_section[:voice_section.index("</section>")]
    assert "Jarvis live voice" not in voice_section
    assert "Speak to Jarvis" not in voice_section
    assert ">Jarvis<" not in voice_section

    assert "jarvis: 'Jarvis'" not in voice_js
    assert "task?.presenter || 'Jarvis'" not in voice_js
    assert "function agentDisplayName()" in voice_js
    assert "pandamonium-identity-updated" in voice_js

    assert "Jarvis: el('set-ttsJarvisVoiceSelect')" not in settings_js
    assert "set-ttsAgentVoiceLabel" in settings_js
