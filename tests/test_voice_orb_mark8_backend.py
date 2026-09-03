from __future__ import annotations

import base64
import io
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from PIL import Image
from pydantic import ValidationError
from starlette.requests import Request

from routes import voice_routes
from src import document_processor


@pytest.fixture(autouse=True)
def _skip_tts_gate(monkeypatch):
    monkeypatch.setattr(voice_routes, "_require_server_tts", lambda _service: None)


def _image_bytes(image_format="PNG", size=(1, 1)):
    buffer = io.BytesIO()
    Image.new("RGB", size, "white").save(buffer, format=image_format)
    return buffer.getvalue()


PNG = _image_bytes()


def _frame(data=PNG, mime="image/png", width=1, height=1, **extra):
    return voice_routes.VoiceFrame(
        mime=mime,
        data_base64=base64.b64encode(data).decode("ascii"),
        width=width,
        height=height,
        **extra,
    )


class _Chat:
    def __init__(self):
        self.id = "chat-1"
        self.owner = "alice"
        self.endpoint_url = "https://models.example.test/v1/chat/completions"
        self.model = "active-vision-model"
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


def _request():
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/api/voice/sessions/voice-1/respond",
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
                "assistant": "Pandamonium",
                "model": "active-vision-model",
                "status": "ready",
                "turns": [],
            }
        }
    }), encoding="utf-8")


def _respond_endpoint(manager):
    router = voice_routes.setup_voice_routes(manager)
    return next(route.endpoint for route in router.routes if route.name == "stream_voice_response")


async def _body(response):
    return "".join([part async for part in response.body_iterator])


def test_frame_validation_is_strict_bounded_and_content_aware():
    assert voice_routes._decode_voice_frame(_frame()) == {
        "bytes": PNG,
        "mime": "image/png",
        "width": 1,
        "height": 1,
    }
    jpeg = _image_bytes("JPEG")
    assert voice_routes._decode_voice_frame(_frame(data=jpeg, mime="image/jpeg"))["bytes"] == jpeg

    with pytest.raises(ValidationError):
        _frame(selector="#camera")
    with pytest.raises(HTTPException, match="encoding"):
        voice_routes._decode_voice_frame(voice_routes.VoiceFrame(
            mime="image/png", data_base64="not-base64!", width=1, height=1
        ))
    with pytest.raises(HTTPException, match="type does not match"):
        voice_routes._decode_voice_frame(_frame(mime="image/jpeg"))
    with pytest.raises(HTTPException, match="dimensions do not match"):
        voice_routes._decode_voice_frame(_frame(width=2))
    with pytest.raises(HTTPException, match="dimensions exceed"):
        voice_routes._decode_voice_frame(_frame(
            data=_image_bytes(size=(1025, 1)), width=1024, height=1
        ))
    with pytest.raises(HTTPException, match="1 MiB"):
        voice_routes._decode_voice_frame(_frame(
            data=b"\x89PNG\r\n\x1a\n" + b"x" * (voice_routes.VOICE_FRAME_MAX_BYTES - 7),
        ))


def test_media_commands_are_exact_single_purpose_and_do_not_shadow_existing_routes():
    assert voice_routes._media_command("Open your eyes.") == "camera_open"
    assert voice_routes._media_command("What do you see?") == "camera_describe"
    assert voice_routes._media_command("Describe what you see.") == "camera_describe"
    assert voice_routes._media_command("Close your eyes.") == "camera_close"
    assert voice_routes._media_command("I need something motivational.") == "media_motivation"
    assert voice_routes._media_command("Open your eyes and describe what you see") is None
    assert voice_routes._media_command("Open camera at https://example.test") is None
    assert voice_routes._media_command("Run this script with selector #orb") is None
    assert voice_routes._media_command("What's on my calendar tomorrow?") is None
    assert voice_routes._calendar_read_args("What's on my calendar tomorrow?") is not None
    assert voice_routes._media_command("Ask Hermes in demo to inspect tests") is None
    assert voice_routes._worker_command("Ask Hermes in demo to inspect tests") is not None


@pytest.mark.asyncio
async def test_camera_and_media_emit_only_enumerated_controls(tmp_path, monkeypatch):
    state_file = tmp_path / "voice_sessions.json"
    _seed_voice_state(state_file)
    monkeypatch.setattr(voice_routes, "VOICE_STATE_FILE", state_file)
    manager = _Manager(_Chat())
    respond = _respond_endpoint(manager)

    expected = {
        "Open your eyes": '"ui_event": "camera_open"',
        "Close your eyes": '"ui_event": "camera_close"',
        "I need something motivational": '"ui_event": "media_play"',
    }
    for text, marker in expected.items():
        response = await respond(
            "voice-1", voice_routes.VoiceRespondRequest(text=text), _request(), "alice"
        )
        body = await _body(response)
        assert marker in body
        assert "selector" not in body
        assert '"script"' not in body
        assert "https://" not in body
    assert '"media_id": "motivational-abstract"' in body


@pytest.mark.asyncio
async def test_compound_camera_phrase_is_plain_conversation_and_frame_is_not_forwarded(tmp_path, monkeypatch):
    state_file = tmp_path / "voice_sessions.json"
    _seed_voice_state(state_file)
    monkeypatch.setattr(voice_routes, "VOICE_STATE_FILE", state_file)
    encoded = base64.b64encode(PNG).decode("ascii")

    monkeypatch.setattr(
        voice_routes,
        "analyze_image_bytes_with_vl_result",
        lambda *_args, **_kwargs: pytest.fail("compound command must not invoke vision"),
    )
    response = await _respond_endpoint(_Manager(_Chat()))(
        "voice-1",
        voice_routes.VoiceRespondRequest(
            text="Open your eyes and describe what you see", frame=_frame()
        ),
        _request(),
        "alice",
    )
    body = await _body(response)
    assert "ui_control" not in body
    assert "one allowlisted" in body
    assert encoded not in state_file.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_description_prefers_active_vision_model_and_never_persists_frame(tmp_path, monkeypatch):
    state_file = tmp_path / "voice_sessions.json"
    _seed_voice_state(state_file)
    monkeypatch.setattr(voice_routes, "VOICE_STATE_FILE", state_file)
    seen = {}

    def supports(model, url):
        seen["supports"] = (model, url)
        return True

    def analyze(image_bytes, image_format, owner=None, preferred_candidate=None):
        seen.update(
            image_bytes=image_bytes,
            image_format=image_format,
            owner=owner,
            preferred_candidate=preferred_candidate,
        )
        return {"text": "I see a brightly lit workspace.", "model": "active-vision-model"}

    monkeypatch.setattr(voice_routes, "model_supports_vision", supports)
    monkeypatch.setattr(voice_routes, "analyze_image_bytes_with_vl_result", analyze)
    chat = _Chat()
    response = await _respond_endpoint(_Manager(chat))(
        "voice-1",
        voice_routes.VoiceRespondRequest(text="What do you see?", frame=_frame()),
        _request(),
        "alice",
    )
    body = await _body(response)

    assert seen == {
        "supports": (chat.model, chat.endpoint_url),
        "image_bytes": PNG,
        "image_format": "image/png",
        "owner": "alice",
        "preferred_candidate": (chat.endpoint_url, chat.model, chat.headers),
    }
    assert "I see a brightly lit workspace." in body
    assert base64.b64encode(PNG).decode("ascii") not in body
    persisted = state_file.read_text(encoding="utf-8")
    assert base64.b64encode(PNG).decode("ascii") not in persisted
    assert "data_base64" not in persisted
    assert "frame" not in persisted
    assert "I see a brightly lit workspace." in persisted
    assert '"model": "active-vision-model"' in persisted
    assert [message.role for message in chat.history] == ["user", "assistant"]
    assert chat.history[-1].metadata["source"] == "jarvis_voice"
    assert chat.history[-1].metadata["diagnostics"]["vision_model"] == "active-vision-model"


@pytest.mark.asyncio
async def test_description_skips_active_model_when_it_is_not_vision_capable(tmp_path, monkeypatch):
    state_file = tmp_path / "voice_sessions.json"
    _seed_voice_state(state_file)
    monkeypatch.setattr(voice_routes, "VOICE_STATE_FILE", state_file)
    seen = {}
    monkeypatch.setattr(voice_routes, "model_supports_vision", lambda _model, _url: False)

    def analyze(_bytes, _format, owner=None, preferred_candidate=None):
        seen.update(owner=owner, preferred_candidate=preferred_candidate)
        return {"text": "The configured vision model sees a desk.", "model": "configured-vision"}

    monkeypatch.setattr(voice_routes, "analyze_image_bytes_with_vl_result", analyze)
    response = await _respond_endpoint(_Manager(_Chat()))(
        "voice-1",
        voice_routes.VoiceRespondRequest(text="Describe what you see.", frame=_frame()),
        _request(),
        "alice",
    )
    assert "configured vision model sees a desk" in (await _body(response))
    assert seen == {"owner": "alice", "preferred_candidate": None}


def test_byte_vision_uses_configured_fallbacks_owner_scoped_and_rejects_media_echo(monkeypatch):
    calls = []
    monkeypatch.setattr(
        document_processor,
        "_load_vl_settings",
        lambda: {"vision_enabled": True, "vision_model": "configured-model"},
    )

    def resolve_configured(spec, owner=None):
        assert (spec, owner) == ("configured-model", "alice")
        return ("https://configured.example.test/v1", "configured-model", {"X-Test": "configured"})

    monkeypatch.setattr(document_processor, "_resolve_vl_model", resolve_configured)
    from src import endpoint_resolver

    monkeypatch.setattr(
        endpoint_resolver,
        "resolve_vision_fallback_candidates",
        lambda owner=None: [("https://fallback.example.test/v1", "fallback-model", {})]
        if owner == "alice" else [],
    )

    def llm(url, model, _messages, **_kwargs):
        calls.append((url, model))
        if model != "fallback-model":
            raise RuntimeError("unavailable")
        return "A safe description."

    monkeypatch.setattr(document_processor, "llm_call", llm)
    result = document_processor.analyze_image_bytes_with_vl_result(
        PNG,
        "image/png",
        owner="alice",
        preferred_candidate=("https://active.example.test/v1", "active-model", {}),
    )
    assert result == {"text": "A safe description.", "model": "fallback-model"}
    assert calls == [
        ("https://active.example.test/v1", "active-model"),
        ("https://configured.example.test/v1", "configured-model"),
        ("https://fallback.example.test/v1", "fallback-model"),
    ]

    encoded = base64.b64encode(PNG).decode("ascii")
    monkeypatch.setattr(document_processor, "llm_call", lambda *_args, **_kwargs: f"data:image/png;base64,{encoded}")
    leaked = document_processor.analyze_image_bytes_with_vl_result(PNG, "image/png", owner="alice")
    assert leaked == {
        "text": "[Vision response rejected because it contained inline image data]",
        "model": "configured-model",
    }
    assert encoded not in leaked["text"]
