import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from routes import voice_routes
from src.user_time import get_user_tz_name, get_user_tz_offset


@pytest.fixture(autouse=True)
def _single_user_voice_mode(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setattr(
        voice_routes,
        "load_settings",
        lambda: {
            "tts_enabled": True,
            "tts_provider": "endpoint:test-tts",
            "tts_agent_voices": {
                "Jarvis": "jarvis_chatterbox",
                "Gordon": "gordon_chatterbox",
                "Friday": "",
            },
        },
    )
    monkeypatch.setattr(
        voice_routes,
        "resolve_endpoint",
        lambda *_args, **_kwargs: ("http://selected.test/v1/chat/completions", "selected-model", {}),
    )


class FakeServerTTS:
    available = True


class FakeSessionManager:
    def __init__(self):
        self.created = {}
        self.messages = {}

    def create_session(self, session_id, name, endpoint_url, model, rag=False, owner=None):
        self.created[session_id] = {
            "name": name,
            "endpoint_url": endpoint_url,
            "model": model,
            "owner": owner,
        }
        self.messages[session_id] = []

    def add_message(self, session_id, message):
        self.messages.setdefault(session_id, []).append(message)


def test_voice_session_creates_chat_session_and_persists_text_turns(monkeypatch, tmp_path):
    async def fake_jarvis_reply(session, text, owner, voice_session=None):
        return "At once, sir.", {
            "model": "jarvis-voice:latest",
            "transcript_chars": len(text),
            "assistant_chars": 13,
            "guard_reason": None,
        }, ["task-1"]

    manager = FakeSessionManager()
    monkeypatch.setattr(voice_routes, "VOICE_STATE_FILE", tmp_path / "voice_sessions.json")
    monkeypatch.setattr(voice_routes, "_jarvis_reply", fake_jarvis_reply)

    app = FastAPI()
    app.include_router(voice_routes.setup_voice_routes(manager, FakeServerTTS()))
    client = TestClient(app)

    created = client.post("/api/voice/sessions", json={"mode": "jarvis_call"})
    assert created.status_code == 200
    session_payload = created.json()
    chat_session_id = session_payload["chat_session_id"]
    assert chat_session_id in manager.created

    response = client.post(
        f"/api/voice/sessions/{session_payload['id']}/respond",
        json={"text": "open the voice transcript"},
    )
    assert response.status_code == 200
    assert [m.role for m in manager.messages[chat_session_id]] == ["user", "assistant"]
    assert manager.messages[chat_session_id][0].content == "open the voice transcript"
    assert manager.messages[chat_session_id][1].content == "At once, sir."
    assert manager.messages[chat_session_id][0].metadata["source"] == "jarvis_voice"
    assert manager.messages[chat_session_id][1].metadata["task_id"] == "task-1"

    state = json.loads((tmp_path / "voice_sessions.json").read_text())
    voice_session = state["sessions"][session_payload["id"]]
    assert voice_session["chat_session_id"] == chat_session_id
    assert voice_session["stores_raw_audio"] is False


def test_direct_gordon_turn_persists_foreground_identity(monkeypatch, tmp_path):
    async def fake_gordon_reply(_session, text, _owner, voice_session=None):
        assert voice_session["target"] == "hermes"
        return "Good evening, Leo. This is Gordon.", {
            "model": "hermes-agent",
            "transcript_chars": len(text),
            "assistant_chars": 34,
            "guard_reason": "direct_gordon",
            "direct_target": "hermes",
            "character_name": "Gordon",
        }, []

    manager = FakeSessionManager()
    monkeypatch.setattr(voice_routes, "VOICE_STATE_FILE", tmp_path / "voice_sessions.json")
    monkeypatch.setattr(voice_routes, "_jarvis_reply", fake_gordon_reply)
    app = FastAPI()
    app.include_router(voice_routes.setup_voice_routes(manager, FakeServerTTS()))
    client = TestClient(app)

    created = client.post("/api/voice/sessions", json={"mode": "jarvis_call"}).json()
    state = json.loads((tmp_path / "voice_sessions.json").read_text())
    state["sessions"][created["id"]]["target"] = "hermes"
    (tmp_path / "voice_sessions.json").write_text(json.dumps(state), encoding="utf-8")

    response = client.post(
        f"/api/voice/sessions/{created['id']}/respond",
        json={"text": "Good evening. Is this Gordon?"},
    )

    assert response.status_code == 200
    assistant = manager.messages[created["chat_session_id"]][-1]
    assert assistant.content == "Good evening, Leo. This is Gordon."
    assert assistant.metadata["source"] == "direct_worker_voice"
    assert assistant.metadata["character_name"] == "Gordon"
    assert assistant.metadata["target"] == "hermes"
    assert assistant.metadata.get("task_id") is None


def test_stream_keeps_slow_agent_alive_and_opens_audio_on_first_sentence(monkeypatch, tmp_path):
    completed = False

    async def slow_events(*_args, **_kwargs):
        nonlocal completed
        await asyncio.sleep(0.03)
        completed = True
        yield {"type": "assistant_delta", "text": "Verified.", "model": "Gordon"}
        yield voice_routes._server_final_event(
            "Check it",
            "Verified.",
            "direct_gordon",
            direct_target="hermes",
            character_name="Gordon",
            model="hermes-agent",
        )

    manager = FakeSessionManager()
    monkeypatch.setattr(voice_routes, "VOICE_STATE_FILE", tmp_path / "voice_sessions.json")
    monkeypatch.setattr(voice_routes, "VOICE_EVENT_HEARTBEAT_SECONDS", 0.01)
    monkeypatch.setattr(voice_routes, "_jarvis_events", slow_events)
    voice_routes._SPEECH_TURNS.clear()
    app = FastAPI()
    app.include_router(voice_routes.setup_voice_routes(manager, FakeServerTTS()))
    client = TestClient(app)
    created = client.post("/api/voice/sessions", json={"mode": "jarvis_call"}).json()

    response = client.post(
        f"/api/voice/sessions/{created['id']}/respond/stream",
        json={"text": "Check it"},
    )

    event_types = [
        json.loads(line[6:]).get("type")
        for line in response.text.splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]
    assert response.status_code == 200
    assert ": heartbeat" in response.text
    assert event_types.index("audio_ready") < event_types.index("final")
    assert completed is True


def test_direct_gordon_speech_uses_the_shared_artifact_handoff_policy():
    full_answer = (
        "I finished the shell script. It validates the target before creating a timestamped backup.\n\n"
        "```bash\nrsync -a --delete source/ destination/\n```"
    )
    final = {
        "assistant_text": full_answer,
        "diagnostics": {"direct_target": "hermes"},
    }

    spoken = asyncio.run(voice_routes._spoken_text_for_final("Write a backup script", final))

    assert spoken == "I finished the shell script. It validates the target before creating a timestamped backup."
    assert "rsync" not in spoken


def test_direct_gordon_uses_his_voice_and_router_failures_keep_the_default():
    assert voice_routes._tts_voice_for_final({
        "diagnostics": {"character_name": "Gordon", "direct_target": "hermes"},
    }) == "gordon_chatterbox"
    assert voice_routes._tts_voice_for_final({
        "diagnostics": {"character_name": "Pandamonium", "direct_target": "hermes"},
    }) is None


def test_voice_session_title_uses_browser_timezone_context(monkeypatch, tmp_path):
    seen = []

    def fake_now_user_local():
        seen.append((get_user_tz_offset(), get_user_tz_name()))
        return datetime(2026, 7, 11, 21, 30, tzinfo=timezone.utc)

    manager = FakeSessionManager()
    monkeypatch.setattr(voice_routes, "VOICE_STATE_FILE", tmp_path / "voice_sessions.json")
    monkeypatch.setattr(voice_routes, "now_user_local", fake_now_user_local)
    app = FastAPI()
    app.include_router(voice_routes.setup_voice_routes(manager, FakeServerTTS()))

    response = TestClient(app).post(
        "/api/voice/sessions",
        json={"mode": "jarvis_call"},
        headers={"X-TZ-Offset": "-240", "X-TZ-Name": "America/New_York"},
    )

    assert response.status_code == 200
    assert seen == [(-240, "America/New_York")]
    assert manager.created[response.json()["chat_session_id"]]["name"] == "Assistant Voice 9:30 PM"


def test_voice_session_accepts_client_timing_diagnostics(monkeypatch, tmp_path):
    manager = FakeSessionManager()
    monkeypatch.setattr(voice_routes, "VOICE_STATE_FILE", tmp_path / "voice_sessions.json")

    app = FastAPI()
    app.include_router(voice_routes.setup_voice_routes(manager, FakeServerTTS()))
    client = TestClient(app)

    created = client.post("/api/voice/sessions", json={"mode": "jarvis_call"}).json()
    response = client.post(
        f"/api/voice/sessions/{created['id']}/diagnostics",
        json={"label": "client_turn", "timings": {"stt_ms": 12.345, "raw_audio": {"no": "thanks"}}},
    )

    assert response.status_code == 200
    state = json.loads((tmp_path / "voice_sessions.json").read_text())
    diagnostic = state["sessions"][created["id"]]["diagnostics"][-1]
    assert diagnostic["client"] is True
    assert diagnostic["stt_ms"] == 12.35
    assert "raw_audio" not in diagnostic


def test_voice_session_requires_server_tts_at_start_and_use(monkeypatch, tmp_path):
    settings = {"tts_enabled": True, "tts_provider": "endpoint:test-tts"}
    tts = FakeServerTTS()
    manager = FakeSessionManager()
    monkeypatch.setattr(voice_routes, "load_settings", lambda: settings)
    monkeypatch.setattr(voice_routes, "VOICE_STATE_FILE", tmp_path / "voice_sessions.json")
    app = FastAPI()
    app.include_router(voice_routes.setup_voice_routes(manager, tts))
    client = TestClient(app)

    created = client.post("/api/voice/sessions", json={})
    assert created.status_code == 200
    assert client.get("/api/voice/status").json()["server_tts_ready"] is True

    settings["tts_provider"] = "browser"
    for response in (
        client.post("/api/voice/sessions", json={}),
        client.post("/api/voice/prewarm"),
        client.post(
            f"/api/voice/sessions/{created.json()['id']}/respond",
            json={"text": "This must not run without server speech."},
        ),
    ):
        assert response.status_code == 503
        assert response.json()["detail"] == {
            "code": "server_tts_required",
            "message": voice_routes.VOICE_SERVER_TTS_ERROR,
        }
    assert client.get("/api/voice/status").json()["server_tts_ready"] is False

    settings["tts_provider"] = "endpoint:test-tts"
    tts.available = False
    assert client.post("/api/voice/sessions", json={}).status_code == 503
    state = json.loads((tmp_path / "voice_sessions.json").read_text())
    assert state["sessions"][created.json()["id"]]["turns"] == []


def test_voice_num_predict_stays_short_unless_detail_requested():
    assert voice_routes._num_predict_for_text("Who are you?") == 1200
    assert voice_routes._num_predict_for_text("Explain this in detail.") == 2400


def test_spoken_text_policy_keeps_complete_answers_and_honors_read_all():
    short = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
    long = "A long response with details. " * 80

    assert asyncio.run(voice_routes._select_spoken_text("Tell me", short)) == short
    assert asyncio.run(voice_routes._select_spoken_text("Tell me", long)) == long.strip()
    assert asyncio.run(voice_routes._select_spoken_text("Read it all", "x" * 4000)) == "x" * 4000
    assert asyncio.run(voice_routes._select_spoken_text("Read it all", "x" * 5000)) == "x" * 5000


def test_spoken_text_policy_hands_off_artifacts_without_reading_them_aloud():
    response = (
        "I finished the Python script. It validates the input, writes a backup, and reports failures clearly.\n\n"
        "```python\nprint('full artifact stays in chat')\n```"
    )

    spoken = asyncio.run(voice_routes._select_spoken_text("Write me a Python script", response))

    assert spoken == "I finished the Python script. It validates the input, writes a backup, and reports failures clearly."
    assert "print" not in spoken


def test_spoken_text_policy_uses_a_human_fallback_for_an_artifact_without_a_summary():
    response = "```python\nprint('full artifact stays in chat')\n```"

    spoken = asyncio.run(voice_routes._select_spoken_text("Create a Python script", response))

    assert spoken == "I finished the script. It's in the chat for you to review."


@pytest.mark.parametrize(
    ("target", "endpoint", "model", "character"),
    [
        ("jarvis", "http://freetoken.test/v1/chat/completions", "jarvis", "Assistant"),
        ("friday", "https://chatgpt.com/backend-api/codex/responses", "gpt-5-codex", "Friday"),
    ],
)
def test_voice_uses_selected_chat_brain(monkeypatch, target, endpoint, model, character):
    captured = {}

    class LinkedSession:
        endpoint_url = endpoint
        headers = {"Authorization": "Bearer selected"}

        def __init__(self):
            self.model = model

        def get_context_messages(self):
            return [{"role": "user", "content": "How are you?"}]

    class Manager:
        def get_session(self, session_id):
            assert session_id == "chat-selected"
            return LinkedSession()

    async def fake_stream(endpoint_url, selected_model, messages, **kwargs):
        captured.update(
            endpoint_url=endpoint_url,
            model=selected_model,
            messages=messages,
            headers=kwargs.get("headers"),
        )
        yield 'data: {"delta":"Selected brain online."}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(voice_routes, "_SESSION_MANAGER", Manager())
    monkeypatch.setattr(voice_routes, "stream_agent_loop", fake_stream)

    events = asyncio.run(_collect_voice_events("chat-selected", target, origin_target=target))

    assert captured["endpoint_url"] == endpoint
    assert captured["model"] == model
    assert captured["headers"] == {"Authorization": "Bearer selected"}
    assert events[-1]["diagnostics"]["model"] == model
    assert events[-1]["diagnostics"]["character_name"] == character
    if target == "friday":
        assert events[-1]["diagnostics"]["direct_target"] == "friday"


def test_transferred_voice_uses_the_target_agent_endpoint(monkeypatch):
    captured = {}

    class LinkedJarvisSession:
        endpoint_url = "http://freetoken.test/v1/chat/completions"
        model = "jarvis"
        headers = {}

        def get_context_messages(self):
            return [{"role": "user", "content": "Transfer context"}]

    class Manager:
        def get_session(self, _session_id):
            return LinkedJarvisSession()

    async def fake_stream(endpoint_url, selected_model, _messages, **kwargs):
        captured.update(endpoint_url=endpoint_url, model=selected_model, headers=kwargs.get("headers"))
        yield 'data: {"delta":"Friday online."}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(voice_routes, "_SESSION_MANAGER", Manager())
    monkeypatch.setattr(
        voice_routes,
        "_resolve_voice_target_endpoint",
        lambda target, _owner: (
            "https://chatgpt.com/backend-api/codex/responses",
            "gpt-5-codex",
            {"Authorization": "Bearer friday"},
        ) if target == "friday" else None,
    )
    monkeypatch.setattr(voice_routes, "stream_agent_loop", fake_stream)

    events = asyncio.run(_collect_voice_events("chat-selected", "friday", origin_target="jarvis"))

    assert captured == {
        "endpoint_url": "https://chatgpt.com/backend-api/codex/responses",
        "model": "gpt-5-codex",
        "headers": {"Authorization": "Bearer friday"},
    }
    assert events[-1]["diagnostics"]["character_name"] == "Friday"


async def _collect_voice_events(chat_session_id, target, origin_target=None):
    return [
        event
        async for event in voice_routes._jarvis_events(
            chat_session_id,
            "Explain the selected runtime in one sentence.",
            "",
            {"target": target, "origin_target": origin_target or target},
        )
    ]


def test_voice_prewarm_wakes_tts_without_cache(monkeypatch):
    assert voice_routes.VOICE_TTS_PREWARM_TIMEOUT_SECONDS == 30.0

    class FakeResponse:
        def raise_for_status(self):
            return None

    class FakeAsyncClient:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return FakeResponse()

    class FakeTTS:
        available = True

        def __init__(self):
            self.calls = []

        def synthesize(self, text, use_cache=True):
            self.calls.append((text, use_cache))
            return b"wav"

    tts = FakeTTS()
    monkeypatch.setattr(voice_routes.httpx, "AsyncClient", FakeAsyncClient)
    app = FastAPI()
    app.include_router(voice_routes.setup_voice_routes(tts_service=tts))

    response = TestClient(app).post("/api/voice/prewarm")

    assert response.status_code == 200
    assert response.json()["tts_state"] == "warmed"
    assert response.json()["tts_ok"] is True
    assert tts.calls == [("Ready.", False)]


def test_voice_prewarm_does_not_contend_with_active_tts(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

    class FakeAsyncClient:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return FakeResponse()

    class BusyLock:
        def locked(self):
            return True

    class FakeTTS:
        available = True

        def synthesize(self, *_args, **_kwargs):
            raise AssertionError("busy prewarm must not call TTS")

    monkeypatch.setattr(voice_routes.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(voice_routes, "TTS_INFERENCE_LOCK", BusyLock())
    app = FastAPI()
    app.include_router(voice_routes.setup_voice_routes(tts_service=FakeTTS()))

    response = TestClient(app).post("/api/voice/prewarm")

    assert response.status_code == 200
    assert response.json()["tts_state"] == "busy"
    assert response.json()["tts_ok"] is None
