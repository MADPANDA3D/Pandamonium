from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from routes import voice_routes


class FakeSessionManager:
    def __init__(self):
        self.sessions = {}

    def create_session(self, session_id, name, endpoint_url, model, rag=False, owner=None):
        session = SimpleNamespace(
            id=session_id,
            name=name,
            endpoint_url=endpoint_url,
            model=model,
            owner=owner,
        )
        self.sessions[session_id] = session
        return session

    def get_session(self, session_id):
        return self.sessions[session_id]


def _client(manager: FakeSessionManager, *, api_token: bool = False) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def authenticate_for_test(request: Request, call_next):
        request.state.current_user = request.headers.get("X-Test-Owner")
        request.state.api_token = api_token
        if api_token:
            request.state.api_token_owner = request.headers.get("X-Test-Owner")
        return await call_next(request)

    app.include_router(voice_routes.setup_voice_routes(manager))
    return TestClient(app)


def test_voice_session_routes_enforce_owner(monkeypatch, tmp_path):
    manager = FakeSessionManager()
    monkeypatch.setattr(voice_routes, "VOICE_STATE_FILE", tmp_path / "voice_sessions.json")
    client = _client(manager)

    created = client.post(
        "/api/voice/sessions",
        headers={"X-Test-Owner": "alice"},
        json={"mode": "jarvis_call"},
    )

    assert created.status_code == 200
    session_id = created.json()["id"]
    assert created.json()["owner"] == "alice"
    assert client.get(
        f"/api/voice/sessions/{session_id}",
        headers={"X-Test-Owner": "alice"},
    ).status_code == 200
    denied = client.get(
        f"/api/voice/sessions/{session_id}",
        headers={"X-Test-Owner": "bob"},
    )
    assert denied.status_code == 403


def test_voice_routes_reject_bearer_api_token_identity(monkeypatch, tmp_path):
    monkeypatch.setattr(voice_routes, "VOICE_STATE_FILE", tmp_path / "voice_sessions.json")
    client = _client(FakeSessionManager(), api_token=True)

    response = client.post(
        "/api/voice/sessions",
        headers={"X-Test-Owner": "alice"},
        json={"mode": "jarvis_call"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "API tokens must use a scope-aware API route"


def test_legacy_voice_session_backfills_only_from_linked_chat_owner(monkeypatch, tmp_path):
    state_file = tmp_path / "voice_sessions.json"
    state_file.write_text(
        json.dumps(
            {
                "sessions": {
                    "legacy": {
                        "id": "legacy",
                        "chat_session_id": "chat-alice",
                        "turns": [],
                    }
                },
                "actions": {},
            }
        ),
        encoding="utf-8",
    )
    manager = FakeSessionManager()
    manager.sessions["chat-alice"] = SimpleNamespace(owner="alice")
    monkeypatch.setattr(voice_routes, "VOICE_STATE_FILE", state_file)
    client = _client(manager)

    denied = client.get(
        "/api/voice/sessions/legacy",
        headers={"X-Test-Owner": "bob"},
    )
    accepted = client.get(
        "/api/voice/sessions/legacy",
        headers={"X-Test-Owner": "alice"},
    )

    assert denied.status_code == 403
    assert accepted.status_code == 200
    assert json.loads(state_file.read_text(encoding="utf-8"))["sessions"]["legacy"]["owner"] == "alice"
