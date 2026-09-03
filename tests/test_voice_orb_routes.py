import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request

from routes import voice_routes


@pytest.fixture(autouse=True)
def _skip_tts_gate(monkeypatch):
    monkeypatch.setattr(voice_routes, "_require_server_tts", lambda _service: None)


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


def _authenticated_request(username, *, is_admin):
    request = _request("GET")
    request.state.current_user = username
    request.app.state.auth_manager = SimpleNamespace(
        is_configured=True,
        is_admin=lambda user: is_admin and user == username,
    )
    return request


def _endpoint(router, name):
    return next(route.endpoint for route in router.routes if route.name == name)


def _seed_voice_state(path, owner="alice"):
    session = {
        "id": "voice-1",
        "chat_session_id": "chat-1",
        "assistant": "Pandamonium",
        "model": "example-model",
        "status": "ready",
        "turns": [],
    }
    if owner is not None:
        session["owner"] = owner
    path.write_text(json.dumps({"sessions": {"voice-1": session}}), encoding="utf-8")


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


def test_calendar_voice_intent_is_read_only_and_resolves_local_ranges():
    now = datetime(2026, 7, 15, 14, 30, tzinfo=timezone.utc)
    assert voice_routes._calendar_read_args("What's on my calendar tomorrow?", now) == {
        "action": "list_events",
        "start": "2026-07-16T00:00:00",
        "end": "2026-07-17T00:00:00",
    }
    assert voice_routes._calendar_read_args("Compare my calendar today and tomorrow", now) == {
        "action": "list_events",
        "start": "2026-07-15T00:00:00",
        "end": "2026-07-17T00:00:00",
    }
    assert voice_routes._calendar_read_args("Show my calendars", now) == {"action": "list_calendars"}
    assert voice_routes._calendar_read_args("Create a calendar event tomorrow", now) is None
    assert voice_routes._calendar_read_args("How does Calendar sync work?", now) is None


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
async def test_legacy_voice_session_backfills_linked_chat_owner(tmp_path, monkeypatch):
    state_file = tmp_path / "voice_sessions.json"
    _seed_voice_state(state_file, owner=None)
    monkeypatch.setattr(voice_routes, "VOICE_STATE_FILE", state_file)
    router = voice_routes.setup_voice_routes(_Manager(_Chat(owner="alice")))
    get_session = _endpoint(router, "get_voice_session")

    result = await get_session(
        "voice-1",
        _authenticated_request("alice", is_admin=False),
        "alice",
    )

    assert result["id"] == "voice-1"
    persisted = json.loads(state_file.read_text(encoding="utf-8"))
    assert persisted["sessions"]["voice-1"]["owner"] == "alice"


@pytest.mark.parametrize("is_admin", [False, True])
def test_legacy_voice_session_cannot_be_claimed_by_first_caller(
    tmp_path,
    monkeypatch,
    is_admin,
):
    state_file = tmp_path / "voice_sessions.json"
    _seed_voice_state(state_file, owner=None)
    monkeypatch.setattr(voice_routes, "VOICE_STATE_FILE", state_file)

    with pytest.raises(HTTPException) as exc:
        voice_routes._owned_voice_session(
            "voice-1",
            "bob",
            session_manager=_Manager(_Chat(owner="alice")),
            request=_authenticated_request("bob", is_admin=is_admin),
        )

    assert exc.value.status_code == 403
    persisted = json.loads(state_file.read_text(encoding="utf-8"))
    assert persisted["sessions"]["voice-1"]["owner"] == "alice"


@pytest.mark.asyncio
async def test_orphaned_ownerless_voice_session_is_denied_to_non_admin(tmp_path, monkeypatch):
    state_file = tmp_path / "voice_sessions.json"
    _seed_voice_state(state_file, owner=None)
    monkeypatch.setattr(voice_routes, "VOICE_STATE_FILE", state_file)
    monkeypatch.setenv("AUTH_ENABLED", "true")
    missing_chat = _Chat()
    missing_chat.id = "another-chat"
    get_session = _endpoint(
        voice_routes.setup_voice_routes(_Manager(missing_chat)),
        "get_voice_session",
    )

    with pytest.raises(HTTPException) as exc:
        await get_session(
            "voice-1",
            _authenticated_request("bob", is_admin=False),
            "bob",
        )

    assert exc.value.status_code == 403
    persisted = json.loads(state_file.read_text(encoding="utf-8"))
    assert "owner" not in persisted["sessions"]["voice-1"]


@pytest.mark.asyncio
async def test_orphaned_ownerless_voice_session_remains_unclaimed_for_admin(tmp_path, monkeypatch):
    state_file = tmp_path / "voice_sessions.json"
    _seed_voice_state(state_file, owner=None)
    monkeypatch.setattr(voice_routes, "VOICE_STATE_FILE", state_file)
    monkeypatch.setenv("AUTH_ENABLED", "true")
    missing_chat = _Chat()
    missing_chat.id = "another-chat"
    get_session = _endpoint(
        voice_routes.setup_voice_routes(_Manager(missing_chat)),
        "get_voice_session",
    )

    session = await get_session(
        "voice-1",
        _authenticated_request("admin", is_admin=True),
        "admin",
    )

    assert session["id"] == "voice-1"
    persisted = json.loads(state_file.read_text(encoding="utf-8"))
    assert "owner" not in persisted["sessions"]["voice-1"]


@pytest.mark.asyncio
async def test_conversation_stream_uses_linked_model_and_persists_final(tmp_path, monkeypatch):
    state_file = tmp_path / "voice_sessions.json"
    _seed_voice_state(state_file)
    monkeypatch.setattr(voice_routes, "VOICE_STATE_FILE", state_file)
    monkeypatch.setattr(voice_routes, "VOICE_ENDPOINT_ID", "")
    monkeypatch.setattr(voice_routes, "VOICE_MODEL", "")

    async def fake_events(chat_session_id, _text, owner, _voice_session):
        assert chat_session_id == "chat-1"
        assert owner == "alice"
        yield {"type": "assistant_delta", "text": "Hello "}
        yield {"type": "assistant_delta", "text": "there."}
        yield {
            "type": "final",
            "assistant_text": "Hello there.",
            "diagnostics": {"model": "example-model", "task_ids": []},
            "task_ids": [],
        }

    monkeypatch.setattr(voice_routes, "_jarvis_events", fake_events)
    chat = _Chat()
    router = voice_routes.setup_voice_routes(_Manager(chat))
    respond = _endpoint(router, "stream_voice_response")
    response = await respond(
        "voice-1",
        voice_routes.VoiceRespondRequest(text="Say hello"),
        _request(),
        "alice",
    )
    body = "".join([part async for part in response.body_iterator])
    assert '"type": "final"' in body
    assert '"assistant_text": "Hello there."' in body
    assert [message.role for message in chat.history] == ["user", "assistant"]
    assert chat.history[-1].content == "Hello there."


@pytest.mark.asyncio
async def test_foreground_response_emits_only_allowlisted_ui_control(tmp_path, monkeypatch):
    state_file = tmp_path / "voice_sessions.json"
    _seed_voice_state(state_file)
    monkeypatch.setattr(voice_routes, "VOICE_STATE_FILE", state_file)
    chat = _Chat()
    router = voice_routes.setup_voice_routes(_Manager(chat))
    respond = _endpoint(router, "stream_voice_response")
    response = await respond(
        "voice-1",
        voice_routes.VoiceRespondRequest(text="open calendar"),
        _request(),
        "alice",
    )
    body = "".join([part async for part in response.body_iterator])
    assert '"ui_event": "open_view"' in body
    assert '"view": "calendar"' in body
    assert "selector" not in body
    assert '"script"' not in body


@pytest.mark.asyncio
async def test_calendar_voice_read_uses_owner_scoped_wrapper_without_enabling_tools(tmp_path, monkeypatch):
    state_file = tmp_path / "voice_sessions.json"
    _seed_voice_state(state_file)
    monkeypatch.setattr(voice_routes, "VOICE_STATE_FILE", state_file)
    monkeypatch.setattr(voice_routes, "VOICE_ENDPOINT_ID", "")
    monkeypatch.setattr(voice_routes, "VOICE_MODEL", "")
    seen = {}

    async def read_calendar(content, owner=None):
        seen["calendar_args"] = json.loads(content)
        seen["owner"] = owner
        return {
            "response": "One event.",
            "events": [{"summary": "Planning", "dtstart": "2026-07-16T09:00:00"}],
            "calendar_freshness": "fresh",
            "exit_code": 0,
        }

    monkeypatch.setattr(voice_routes, "do_read_calendar", read_calendar)
    chat = _Chat()
    router = voice_routes.setup_voice_routes(_Manager(chat))
    respond = _endpoint(router, "stream_voice_response")
    response = await respond(
        "voice-1",
        voice_routes.VoiceRespondRequest(text="What's on my calendar tomorrow?"),
        _request(),
        "alice",
    )

    body = "".join([part async for part in response.body_iterator])
    assert seen["owner"] == "alice"
    assert seen["calendar_args"]["action"] == "list_events"
    assert '"type": "final"' in body
    assert "One event." in body


@pytest.mark.asyncio
async def test_calendar_voice_read_always_speaks_freshness_failure(tmp_path, monkeypatch):
    state_file = tmp_path / "voice_sessions.json"
    _seed_voice_state(state_file)
    monkeypatch.setattr(voice_routes, "VOICE_STATE_FILE", state_file)

    async def read_calendar(_content, owner=None):
        assert owner == "alice"
        return {
            "response": "Cached: no events.",
            "events": [],
            "calendar_freshness": "sync_failed",
            "sync_error_count": 1,
            "exit_code": 0,
        }

    monkeypatch.setattr(voice_routes, "do_read_calendar", read_calendar)
    chat = _Chat()
    respond = _endpoint(voice_routes.setup_voice_routes(_Manager(chat)), "stream_voice_response")
    response = await respond(
        "voice-1",
        voice_routes.VoiceRespondRequest(text="What's on my calendar tomorrow?"),
        _request(),
        "alice",
    )

    body = "".join([part async for part in response.body_iterator])
    assert "Calendar freshness could not be confirmed" in body
    assert "private host" not in body
