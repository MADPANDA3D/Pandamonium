from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes import voice_routes
from src import chat_helpers, document_processor


ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _frame(**updates):
    frame = {
        "mime": "image/png",
        "data_base64": base64.b64encode(ONE_PIXEL_PNG).decode("ascii"),
        "width": 1,
        "height": 1,
    }
    frame.update(updates)
    return voice_routes.VoiceFrame(**frame)


def test_media_commands_are_exact_and_single_purpose():
    assert voice_routes._media_command("Open your eyes.") == "camera_open"
    assert voice_routes._media_command("What do you see?") == "camera_describe"
    assert voice_routes._media_command("Describe what you see.") == "camera_describe"
    assert voice_routes._media_command("Close your eyes.") == "camera_close"
    assert voice_routes._media_command("I need something motivational.") == "media_motivation"
    assert voice_routes._media_command("Open your eyes and describe what you see") is None
    assert voice_routes._media_command("Open camera at https://example.test") is None


def test_voice_frame_validates_base64_magic_type_and_dimensions():
    decoded = voice_routes._decode_voice_frame(_frame())
    assert decoded == {
        "bytes": ONE_PIXEL_PNG,
        "mime": "image/png",
        "width": 1,
        "height": 1,
    }

    with pytest.raises(Exception, match="encoding"):
        voice_routes._decode_voice_frame(_frame(data_base64="not base64!"))
    with pytest.raises(Exception, match="type does not match"):
        voice_routes._decode_voice_frame(_frame(mime="image/jpeg"))
    with pytest.raises(Exception, match="dimensions do not match"):
        voice_routes._decode_voice_frame(_frame(width=2))


@pytest.mark.asyncio
async def test_camera_and_media_events_use_only_enumerated_controls():
    cases = {
        "Open your eyes": {"type": "ui_control", "ui_event": "camera_open"},
        "Close your eyes": {"type": "ui_control", "ui_event": "camera_close"},
        "I need something motivational": {
            "type": "ui_control",
            "ui_event": "media_play",
            "media_id": "motivational-abstract",
        },
    }
    for text, expected in cases.items():
        events = [
            event
            async for event in voice_routes._server_routed_events(
                "chat-1", text, "alice", {}
            )
        ]
        assert events[0] == expected
        assert events[-1]["type"] == "final"


@pytest.mark.asyncio
async def test_describe_uses_in_memory_frame_and_persists_only_model_metadata(monkeypatch):
    seen = {}

    def analyze(image_bytes, image_format, owner=None, preferred_model=None):
        seen.update(
            bytes=image_bytes,
            image_format=image_format,
            owner=owner,
            preferred_model=preferred_model,
        )
        return {"text": "I see a brightly lit workspace.", "model": "vision-test"}

    monkeypatch.setattr(chat_helpers, "model_supports_vision", lambda *_args: True)
    monkeypatch.setattr(document_processor, "analyze_image_bytes_with_vl_result", analyze)

    events = [
        event
        async for event in voice_routes._server_routed_events(
            "chat-1",
            "What do you see?",
            "alice",
            {"_frame": voice_routes._decode_voice_frame(_frame())},
        )
    ]

    assert seen == {
        "bytes": ONE_PIXEL_PNG,
        "image_format": "image/png",
        "owner": "alice",
        "preferred_model": voice_routes.JARVIS_MODEL,
    }
    assert events[0] == {"type": "assistant_delta", "text": "I see a brightly lit workspace."}
    assert events[-1]["diagnostics"]["vision_model"] == "vision-test"
    assert "frame" not in json.dumps(events[-1])


def test_voice_response_never_persists_frame(monkeypatch, tmp_path):
    class Manager:
        def __init__(self):
            self.sessions = {}

        def create_session(self, session_id, name, endpoint_url, model, rag=False, owner=None):
            self.sessions[session_id] = SimpleNamespace(owner=owner)

        def get_session(self, session_id):
            return self.sessions[session_id]

        def add_message(self, _session_id, _message):
            return None

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setattr(voice_routes, "VOICE_STATE_FILE", tmp_path / "voice_sessions.json")
    monkeypatch.setattr(chat_helpers, "model_supports_vision", lambda *_args: False)
    monkeypatch.setattr(
        document_processor,
        "analyze_image_bytes_with_vl_result",
        lambda *_args, **_kwargs: {"text": "A desk is visible.", "model": "vision-test"},
    )

    app = FastAPI()
    app.include_router(voice_routes.setup_voice_routes(Manager()))
    client = TestClient(app)
    session_id = client.post("/api/voice/sessions", json={}).json()["id"]
    encoded = base64.b64encode(ONE_PIXEL_PNG).decode("ascii")
    response = client.post(
        f"/api/voice/sessions/{session_id}/respond",
        json={"text": "What do you see?", "frame": _frame().model_dump()},
    )

    assert response.status_code == 200, response.text
    persisted = (tmp_path / "voice_sessions.json").read_text(encoding="utf-8")
    assert encoded not in persisted
    assert '"_frame"' not in persisted
    assert '"vision_model": "vision-test"' in persisted
