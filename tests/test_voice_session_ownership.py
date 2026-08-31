from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
import pytest

from routes import voice_routes
from src.extension_host import ExtensionRuntimeHost
from src.extension_registry import ExtensionRegistry


@pytest.fixture(autouse=True)
def _server_tts_settings(monkeypatch):
    monkeypatch.setattr(
        voice_routes,
        "load_settings",
        lambda: {"tts_enabled": True, "tts_provider": "endpoint:test-tts"},
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

    app.include_router(voice_routes.setup_voice_routes(manager, FakeServerTTS()))
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


def test_generic_extension_result_route_correlates_owner_extension_and_tool(monkeypatch, tmp_path):
    revision = "a" * 40
    manifest = {
        "protocol_version": "jos-extension.v1",
        "extension_id": "atlas",
        "name": "Atlas Fixture",
        "version": "1.0.0",
        "source": {"url": "https://github.com/example/atlas.git", "revision": revision},
        "runtime": {"type": "web", "entrypoint": "ui/index.html"},
        "capabilities": {
            "descriptor": {"type": "inline"},
            "schemas": [{
                "name": "create_mesh",
                "description": "Create a fixture mesh",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            }],
        },
        "permissions": {"default": "bounded_write", "capabilities": {}},
        "health": {"type": "catalog", "timeout_seconds": 3},
        "lifecycle": {"install": [], "start": [], "stop": [], "remove": []},
        "data_boundaries": {"read": [], "write": [], "network": []},
        "removal": {"remove_paths": [], "preserve_paths": []},
        "rollback": {"strategy": "pinned_revision", "retain_revisions": 2},
    }
    registry = ExtensionRegistry(tmp_path / "extensions.json")
    registry.register(manifest, source_revision=revision, health_available=True)
    monkeypatch.setattr(voice_routes, "extension_registry", registry)
    monkeypatch.setattr(
        voice_routes,
        "extension_runtime_host",
        ExtensionRuntimeHost({"atlas": "https://atlas.example.test/runtime/"}),
    )
    manager = FakeSessionManager()
    state_file = tmp_path / "voice_sessions.json"
    monkeypatch.setattr(voice_routes, "VOICE_STATE_FILE", state_file)
    client = _client(manager)
    created = client.post(
        "/api/voice/sessions",
        headers={"X-Test-Owner": "alice"},
        json={"mode": "jarvis_call"},
    )
    assert created.json()["extension_surfaces"] == [{
        "extension_id": "atlas",
        "name": "Atlas Fixture",
        "url": "https://atlas.example.test/runtime/ui/index.html",
        "origin": "https://atlas.example.test",
    }]
    assert voice_routes.extension_runtime_host.available("atlas") is False
    session_id = created.json()["id"]
    state = json.loads(state_file.read_text(encoding="utf-8"))
    state["sessions"][session_id]["engaged_extensions"] = ["atlas"]
    state_file.write_text(json.dumps(state), encoding="utf-8")
    loop = asyncio.new_event_loop()
    future = loop.create_future()
    key = (session_id, "atlas", "extension-call-1")
    voice_routes._EXTENSION_TOOL_CALLS[key] = {
        "future": future,
        "owner": "alice",
        "tool": "create_mesh",
    }
    try:
        wrong_owner = client.post(
            f"/api/voice/sessions/{session_id}/extensions/atlas/results",
            headers={"X-Test-Owner": "bob"},
            json={"call_id": "extension-call-1", "tool": "create_mesh", "result": {"ok": True}},
        )
        wrong_call = client.post(
            f"/api/voice/sessions/{session_id}/extensions/atlas/results",
            headers={"X-Test-Owner": "alice"},
            json={"call_id": "extension-call-other", "tool": "create_mesh", "result": {"ok": True}},
        )
        wrong_extension = client.post(
            f"/api/voice/sessions/{session_id}/extensions/oracle/results",
            headers={"X-Test-Owner": "alice"},
            json={"call_id": "extension-call-1", "tool": "create_mesh", "result": {"ok": True}},
        )
        wrong_tool = client.post(
            f"/api/voice/sessions/{session_id}/extensions/atlas/results",
            headers={"X-Test-Owner": "alice"},
            json={"call_id": "extension-call-1", "tool": "other_tool", "result": {"ok": True}},
        )
        too_large = client.post(
            f"/api/voice/sessions/{session_id}/extensions/atlas/results",
            headers={"X-Test-Owner": "alice"},
            json={"call_id": "extension-call-1", "tool": "create_mesh", "result": {"data": "x" * 1_000_001}},
        )
        registry.disable("atlas")
        disabled = client.post(
            f"/api/voice/sessions/{session_id}/extensions/atlas/results",
            headers={"X-Test-Owner": "alice"},
            json={"call_id": "extension-call-1", "tool": "create_mesh", "result": {"ok": True}},
        )
        registry.register(manifest, source_revision=revision, health_available=True)
        registry.unregister("atlas")
        uninstalled = client.post(
            f"/api/voice/sessions/{session_id}/extensions/atlas/results",
            headers={"X-Test-Owner": "alice"},
            json={"call_id": "extension-call-1", "tool": "create_mesh", "result": {"ok": True}},
        )
        registry.register(manifest, source_revision=revision, health_available=True)
        accepted = client.post(
            f"/api/voice/sessions/{session_id}/extensions/atlas/results",
            headers={"X-Test-Owner": "alice"},
            json={"call_id": "extension-call-1", "tool": "create_mesh", "result": {"ok": True}},
        )
    finally:
        voice_routes._EXTENSION_TOOL_CALLS.pop(key, None)
        loop.close()

    assert wrong_owner.status_code == 403
    assert wrong_call.status_code == 404
    assert wrong_extension.status_code == 404
    assert wrong_tool.status_code == 409
    assert too_large.status_code == 413
    assert disabled.status_code == 409
    assert uninstalled.status_code == 409
    assert accepted.status_code == 200
    assert future.result() == {"ok": True}


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
