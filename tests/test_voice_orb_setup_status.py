import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from routes import voice_routes
from src.agent_worker_adapters import WORKER_IDS


class _StatsService:
    def __init__(self, value):
        self.value = value

    def get_stats(self):
        if isinstance(self.value, Exception):
            raise self.value
        return dict(self.value)


class _Chat:
    def __init__(self):
        self.id = "chat-1"
        self.owner = "alice"
        self.endpoint_url = "https://configured.example.test/v1/chat/completions"
        self.model = "configured-model"
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


def _endpoint(router, name):
    return next(route.endpoint for route in router.routes if route.name == name)


def _request():
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/api/voice/test",
        "headers": [(b"sec-fetch-site", b"same-origin")],
        "query_string": b"",
        "scheme": "https",
        "server": ("testserver", 443),
        "client": ("127.0.0.1", 1234),
        "app": SimpleNamespace(state=SimpleNamespace()),
    })


def _seed_voice_state(path):
    path.write_text(json.dumps({
        "sessions": {
            "voice-1": {
                "id": "voice-1",
                "owner": "alice",
                "chat_session_id": "chat-1",
                "assistant": "Odysseus",
                "model": "configured-model",
                "status": "ready",
                "turns": [],
            }
        }
    }), encoding="utf-8")


async def _final_event(response):
    body = "".join([part async for part in response.body_iterator])
    events = [
        json.loads(line[5:].strip())
        for line in body.splitlines()
        if line.startswith("data:")
    ]
    return next(event for event in events if event.get("type") == "final")


def test_setup_status_command_is_exact_and_single_purpose():
    assert voice_routes._setup_status_command("Check voice setup.") is True
    assert voice_routes._setup_status_command("CHECK VOICE SETUP!") is True
    assert voice_routes._setup_status_command("Check voice setup and scan my Tailnet") is False
    assert voice_routes._setup_status_command("Check setup") is False
    assert voice_routes._setup_status_command("Discover agents") is False


@pytest.mark.asyncio
async def test_authenticated_status_and_voice_command_share_one_redacted_snapshot(
    tmp_path,
    monkeypatch,
):
    state_file = tmp_path / "voice_sessions.json"
    _seed_voice_state(state_file)
    monkeypatch.setattr(voice_routes, "VOICE_STATE_FILE", state_file)
    monkeypatch.setattr(voice_routes, "VOICE_ENDPOINT_ID", "secret-endpoint-id")
    monkeypatch.setattr(voice_routes, "VOICE_MODEL", "/home/private/model")
    monkeypatch.setattr(
        voice_routes,
        "_resolve_voice_runtime",
        lambda _owner: (
            "http://100.64.0.10:11434/v1",
            "/home/private/model",
            {"Authorization": "Bearer secret"},
        ),
    )

    async def statuses():
        return {
            "pc-codex": {
                "configured": True,
                "ready": True,
                "workspaces": ["client-workspace"],
                "connection": {"state": "connected", "reason": "http://100.64.0.11"},
            },
            "hermes": {
                "configured": True,
                "ready": False,
                "connection": {"state": "unreachable", "reason": "/secret/token"},
            },
            "vps-codex": {"configured": False, "ready": False},
            "arbitrary-agent": {"configured": True, "ready": True},
        }

    monkeypatch.setattr(voice_routes, "worker_statuses", statuses)
    stt = _StatsService({
        "available": True,
        "provider": "endpoint:secret-stt-id",
        "endpoint": "http://100.64.0.12",
        "error": "/secret/stt-token",
    })
    tts = _StatsService({
        "available": True,
        "provider": "browser",
        "voice": "Safe Voice",
        "speed": 1.25,
        "endpoint": "http://100.64.0.13",
    })
    chat = _Chat()
    router = voice_routes.setup_voice_routes(_Manager(chat), stt, tts)
    status = await _endpoint(router, "voice_status")("alice")

    setup = status["setup"]
    assert setup["core_ready"] is True
    assert setup["model"] == {"configured": True, "selection": "endpoint_override"}
    assert setup["speech_to_text"] == {"available": True, "provider": "endpoint"}
    assert setup["text_to_speech"] == {
        "available": True,
        "provider": "browser",
        "voice": "Safe Voice",
    }
    assert setup["workers"]["ready_count"] == 1
    assert [item["id"] for item in setup["workers"]["items"]] == list(WORKER_IDS)

    response = await _endpoint(router, "respond_to_voice_turn")(
        "voice-1",
        _request(),
        voice_routes.VoiceRespondRequest(text="Check voice setup."),
        "alice",
    )
    final = await _final_event(response)
    assert final["setup"] == setup
    assert final["text"] == setup["text"]
    assert chat.history[-1].content == setup["text"]

    public_blob = json.dumps(status)
    for private_value in (
        "secret-endpoint-id",
        "/home/private/model",
        "secret-stt-id",
        "100.64.0.10",
        "100.64.0.11",
        "100.64.0.12",
        "100.64.0.13",
        "/secret/token",
        "/secret/stt-token",
        "client-workspace",
        "arbitrary-agent",
    ):
        assert private_value not in public_blob


@pytest.mark.asyncio
async def test_setup_status_fails_closed_without_returning_source_errors(monkeypatch):
    monkeypatch.setattr(voice_routes, "VOICE_ENDPOINT_ID", "private-endpoint")
    monkeypatch.setattr(voice_routes, "VOICE_MODEL", "/secret/model-path")

    def unavailable_model(_owner):
        raise HTTPException(503, "http://10.20.30.40/private model error")

    async def unavailable_workers():
        raise RuntimeError("worker token at /secret/token")

    monkeypatch.setattr(voice_routes, "_resolve_voice_runtime", unavailable_model)
    monkeypatch.setattr(voice_routes, "worker_statuses", unavailable_workers)
    status = await voice_routes._voice_status_snapshot(
        "alice",
        _StatsService(RuntimeError("STT at http://10.20.30.41 failed")),
        _StatsService({
            "available": False,
            "provider": "endpoint:private-tts",
            "voice": "/secret/voice-file",
            "speed": "not-a-number",
        }),
    )

    assert status["setup"]["core_ready"] is False
    assert status["setup"]["model"]["configured"] is False
    assert status["setup"]["workers"]["ready_count"] == 0
    assert all(
        item["status"] == "not_configured"
        for item in status["setup"]["workers"]["items"]
    )
    assert status["stt"] == {"available": False, "provider": "disabled"}
    assert status["tts"] == {
        "available": False,
        "provider": "endpoint",
        "voice": "",
        "speed": 1.0,
    }

    blob = json.dumps(status)
    for private_value in (
        "private-endpoint",
        "/secret/model-path",
        "10.20.30.40",
        "10.20.30.41",
        "/secret/token",
        "private-tts",
        "/secret/voice-file",
    ):
        assert private_value not in blob
