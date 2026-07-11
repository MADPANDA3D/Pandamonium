import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes import voice_routes


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
    async def fake_jarvis_reply(session, text, owner):
        return "At once, sir.", {
            "model": "jarvis-voice:latest",
            "transcript_chars": len(text),
            "assistant_chars": 13,
            "guard_reason": None,
        }, []

    manager = FakeSessionManager()
    monkeypatch.setattr(voice_routes, "VOICE_STATE_FILE", tmp_path / "voice_sessions.json")
    monkeypatch.setattr(voice_routes, "_jarvis_reply", fake_jarvis_reply)

    app = FastAPI()
    app.include_router(voice_routes.setup_voice_routes(manager))
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

    state = json.loads((tmp_path / "voice_sessions.json").read_text())
    voice_session = state["sessions"][session_payload["id"]]
    assert voice_session["chat_session_id"] == chat_session_id
    assert voice_session["stores_raw_audio"] is False


def test_voice_session_accepts_client_timing_diagnostics(monkeypatch, tmp_path):
    manager = FakeSessionManager()
    monkeypatch.setattr(voice_routes, "VOICE_STATE_FILE", tmp_path / "voice_sessions.json")

    app = FastAPI()
    app.include_router(voice_routes.setup_voice_routes(manager))
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


def test_voice_num_predict_stays_short_unless_detail_requested():
    assert voice_routes._num_predict_for_text("Who are you?") == voice_routes.VOICE_NORMAL_NUM_PREDICT
    assert voice_routes._num_predict_for_text("Explain this in detail.") == voice_routes.VOICE_LONG_NUM_PREDICT


def test_voice_prewarm_wakes_tts_without_cache(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return FakeResponse()

    class FakeTTS:
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
    assert response.json()["tts_ok"] is True
    assert tts.calls == [("Ready.", False)]
