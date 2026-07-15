import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request

from routes import voice_routes


class _Chat:
    def __init__(self, owner="alice"):
        self.id = "chat-1"
        self.owner = owner
        self.endpoint_url = "https://models.example.test/v1/chat/completions"
        self.model = "example-model"
        self.headers = {"Authorization": "Bearer test-only"}
        self.history = []


class _Manager:
    def __init__(self, chat):
        self.chat = chat

    def get_session(self, session_id):
        if session_id != self.chat.id:
            raise KeyError(session_id)
        return self.chat

    def add_message(self, session_id, message):
        assert session_id == self.chat.id
        self.chat.history.append(message)


def _request(method="POST"):
    return Request({
        "type": "http",
        "method": method,
        "path": "/api/voice/test",
        "headers": [(b"sec-fetch-site", b"same-origin")],
        "query_string": b"",
        "scheme": "https",
        "server": ("testserver", 443),
        "client": ("127.0.0.1", 1234),
        "app": SimpleNamespace(state=SimpleNamespace()),
    })


def _endpoint(router, name):
    return next(route.endpoint for route in router.routes if route.name == name)


def _seed_voice_state(path, owner="alice"):
    path.write_text(json.dumps({
        "sessions": {
            "voice-1": {
                "id": "voice-1",
                "owner": owner,
                "chat_session_id": "chat-1",
                "assistant": "Odysseus",
                "model": "example-model",
                "status": "ready",
                "turns": [],
            }
        }
    }), encoding="utf-8")


def test_voice_payloads_reject_unknown_control_fields():
    with pytest.raises(ValidationError):
        voice_routes.VoiceRespondRequest(text="hello", selector="#anything")
    with pytest.raises(ValidationError):
        voice_routes.VoiceRespondRequest(
            text="hello",
            client_state={"active_view": "chat", "script": "alert(1)"},
        )


def test_foreground_commands_are_exact_and_enumerated():
    assert voice_routes._foreground_command("Open Calendar!") == ("open_view", "calendar")
    assert voice_routes._foreground_command("close this document") == ("close_view", "document")
    assert voice_routes._foreground_command("minimize this document") == ("minimize_view", "document")
    assert voice_routes._foreground_command("what view is open?") == ("report_view_state", None)
    assert voice_routes._foreground_command("open https://example.test") is None
    assert voice_routes._foreground_command("run this script") is None


def test_runtime_prefers_linked_session_without_endpoint_override(monkeypatch):
    chat = _Chat()
    monkeypatch.setattr(voice_routes, "VOICE_ENDPOINT_ID", "")
    monkeypatch.setattr(voice_routes, "VOICE_MODEL", "")
    assert voice_routes._resolve_voice_runtime("alice", chat) == (
        chat.endpoint_url,
        chat.model,
        chat.headers,
    )


def test_runtime_supports_owner_scoped_voice_override(monkeypatch):
    seen = {}

    def resolve(endpoint_id, model, owner):
        seen.update(endpoint_id=endpoint_id, model=model, owner=owner)
        return "https://voice.example.test/v1/chat/completions", "voice-model", {"X-Test": "yes"}

    monkeypatch.setattr(voice_routes, "VOICE_ENDPOINT_ID", "voice-endpoint")
    monkeypatch.setattr(voice_routes, "VOICE_MODEL", "voice-model")
    monkeypatch.setattr(voice_routes, "resolve_endpoint_by_id", resolve)
    result = voice_routes._resolve_voice_runtime("alice", _Chat())
    assert result[1] == "voice-model"
    assert seen == {"endpoint_id": "voice-endpoint", "model": "voice-model", "owner": "alice"}


def test_cross_owner_voice_session_is_denied(tmp_path, monkeypatch):
    state_file = tmp_path / "voice_sessions.json"
    _seed_voice_state(state_file, owner="alice")
    monkeypatch.setattr(voice_routes, "VOICE_STATE_FILE", state_file)
    with pytest.raises(HTTPException) as exc:
        voice_routes._owned_voice_session("voice-1", "bob")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_conversation_stream_uses_linked_model_and_persists_final(tmp_path, monkeypatch):
    state_file = tmp_path / "voice_sessions.json"
    _seed_voice_state(state_file)
    monkeypatch.setattr(voice_routes, "VOICE_STATE_FILE", state_file)
    monkeypatch.setattr(voice_routes, "VOICE_ENDPOINT_ID", "")
    monkeypatch.setattr(voice_routes, "VOICE_MODEL", "")

    async def fake_stream(*args, **kwargs):
        assert args[0] == "https://models.example.test/v1/chat/completions"
        assert args[1] == "example-model"
        assert kwargs["tool_choice_none"] is True
        yield 'data: {"delta": "Hello "}\n\n'
        yield 'data: {"delta": "there."}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(voice_routes, "stream_llm", fake_stream)
    chat = _Chat()
    router = voice_routes.setup_voice_routes(_Manager(chat))
    respond = _endpoint(router, "respond_to_voice_turn")
    response = await respond(
        "voice-1",
        _request(),
        voice_routes.VoiceRespondRequest(text="Say hello"),
        "alice",
    )
    body = "".join([part async for part in response.body_iterator])
    assert '"type": "final"' in body
    assert '"text": "Hello there."' in body
    assert [message.role for message in chat.history] == ["user", "assistant"]
    assert chat.history[-1].content == "Hello there."


@pytest.mark.asyncio
async def test_foreground_response_emits_only_allowlisted_ui_control(tmp_path, monkeypatch):
    state_file = tmp_path / "voice_sessions.json"
    _seed_voice_state(state_file)
    monkeypatch.setattr(voice_routes, "VOICE_STATE_FILE", state_file)
    chat = _Chat()
    router = voice_routes.setup_voice_routes(_Manager(chat))
    respond = _endpoint(router, "respond_to_voice_turn")
    response = await respond(
        "voice-1",
        _request(),
        voice_routes.VoiceRespondRequest(text="open calendar"),
        "alice",
    )
    body = "".join([part async for part in response.body_iterator])
    assert '"ui_event": "open_view"' in body
    assert '"view": "calendar"' in body
    assert "selector" not in body
    assert "script" not in body
