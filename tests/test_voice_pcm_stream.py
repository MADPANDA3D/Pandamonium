import asyncio
import io
import json
import wave

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from routes import tts_routes, voice_routes
from src.voice_pcm import pcm_frames, speech_blocks, wav_to_pcm16


@pytest.fixture(autouse=True)
def _single_user_voice_mode(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setattr(
        voice_routes,
        "load_settings",
        lambda: {"tts_enabled": True, "tts_provider": "endpoint:test-tts"},
    )


def _wav_payload(sample_rate=24_000, frames=24_000):
    payload = io.BytesIO()
    with wave.open(payload, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(sample_rate)
        target.writeframes(b"\x00\x00" * frames)
    return payload.getvalue()


def test_wav_pcm_validation_and_frame_alignment():
    payload = io.BytesIO()
    with wave.open(payload, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(48_000)
        target.writeframes(b"\x00\x00" * 60_000)

    sample_rate, pcm = wav_to_pcm16(payload.getvalue())
    frames = list(pcm_frames(pcm))
    assert sample_rate == 48_000
    assert b"".join(frames) == pcm
    assert all(len(frame) % 2 == 0 for frame in frames)


def test_speech_blocks_preserve_text_and_prefer_paragraphs():
    first = " ".join(["The first verified paragraph stays together."] * 5)
    second = " ".join(["The second paragraph is synthesized independently."] * 5)
    text = f"{first}\n\n{second}"

    blocks = speech_blocks(text)

    assert len(blocks) == 2
    assert blocks == [first, second]
    assert " ".join(blocks) == " ".join(text.split())


def test_speech_blocks_bound_long_paragraphs_without_dropping_words():
    text = " ".join(["Jarvis keeps each semantic block natural and complete."] * 18)

    blocks = speech_blocks(text)

    assert len(blocks) >= 3
    assert max(map(len, blocks)) <= 360
    assert " ".join(blocks) == text


def test_voice_turn_audio_streams_semantic_chatterbox_blocks(monkeypatch, tmp_path):
    class FakeTTS:
        available = True

        def __init__(self):
            self.calls = []

        def synthesize(self, text, use_cache=True, voice=None):
            self.calls.append((text, use_cache, voice, voice_routes.TTS_INFERENCE_LOCK.locked()))
            return _wav_payload()

    monkeypatch.setattr(voice_routes, "VOICE_STATE_FILE", tmp_path / "voice_sessions.json")
    voice_routes._SPEECH_TURNS.clear()
    tts = FakeTTS()
    app = FastAPI()
    app.include_router(voice_routes.setup_voice_routes(tts_service=tts))
    client = TestClient(app)
    session = client.post("/api/voice/sessions", json={"mode": "jarvis_call"}).json()
    speech_turn = voice_routes._register_speech_turn(session["id"])
    speech_turn.voice = voice_routes.CHARACTER_TTS_VOICES["Gordon"]
    spoken = "\n\n".join((
        " ".join(["The first paragraph reports verified progress without losing context."] * 7),
        " ".join(["The second paragraph remains clear because Chatterbox starts a fresh block."] * 7),
    ))
    asyncio.run(speech_turn.complete(spoken))

    response = client.get(f"/api/voice/sessions/{session['id']}/turns/{speech_turn.turn_id}/audio")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-accel-buffering"] == "no"
    events = [json.loads(line) for line in response.text.splitlines()]
    blocks = speech_blocks(spoken)
    assert events[0] == {"type": "start", "sample_rate": 24_000}
    assert events[-1]["type"] == "done"
    assert events[-1]["blocks"] == len(blocks)
    assert [event["index"] for event in events if event["type"] == "block"] == list(range(len(blocks)))
    assert any(event["type"] == "audio" and event["pcm_base64"] for event in events)
    assert tts.calls == [(block, False, "gordon_chatterbox", True) for block in blocks]
    assert " ".join(call[0] for call in tts.calls) == " ".join(spoken.split())

    completed = client.post(
        f"/api/voice/sessions/{session['id']}/turns/{speech_turn.turn_id}/playback",
        json={"state": "completed", "timings": {"scheduler_underruns": 0}},
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "ready"


def test_voice_turn_audio_does_not_emit_stale_audio_after_interrupt(monkeypatch, tmp_path):
    class InterruptingTTS:
        available = True

        def synthesize(self, _text, _use_cache=True, voice=None):
            speech_turn.cancelled = True
            return _wav_payload()

    monkeypatch.setattr(voice_routes, "VOICE_STATE_FILE", tmp_path / "voice_sessions.json")
    voice_routes._SPEECH_TURNS.clear()
    app = FastAPI()
    app.include_router(voice_routes.setup_voice_routes(tts_service=InterruptingTTS()))
    client = TestClient(app)
    session = client.post("/api/voice/sessions", json={"mode": "jarvis_call"}).json()
    speech_turn = voice_routes._register_speech_turn(session["id"])
    asyncio.run(speech_turn.complete("This block is interrupted while Chatterbox is synthesizing it."))

    response = client.get(f"/api/voice/sessions/{session['id']}/turns/{speech_turn.turn_id}/audio")

    events = [json.loads(line) for line in response.text.splitlines()]
    assert [event["type"] for event in events] == ["error"]
    assert "interrupted" in events[0]["error"].lower()
    assert client.get(f"/api/voice/sessions/{session['id']}").json()["status"] == "interrupted"


def test_compatibility_tts_stream_uses_one_upstream_inference(monkeypatch):
    class FakeTTS:
        available = True

    calls = []

    async def fake_stream(_tts, text, **_kwargs):
        calls.append(text)
        yield {"type": "start", "sample_rate": 24_000}
        yield {"type": "done", "generation_ms": 1, "audio_ms": 1}

    monkeypatch.setattr(tts_routes, "stream_tts_pcm_segment", fake_stream)
    app = FastAPI()
    app.include_router(tts_routes.setup_tts_routes(FakeTTS()))
    text = "One complete utterance. " * 40

    response = TestClient(app).post("/api/tts/stream", json={"text": text})

    assert response.status_code == 200
    assert [json.loads(line)["type"] for line in response.text.splitlines()] == ["start", "done"]
    assert calls == [text.strip()]


def test_tts_synthesize_uses_shared_lock_for_audio_and_base64():
    class FakeTTS:
        available = True

        def __init__(self):
            self.calls = []

        def synthesize(self, text, **_kwargs):
            self.calls.append(("audio", text, tts_routes.TTS_INFERENCE_LOCK.locked()))
            return b"ID3audio"

        def synthesize_to_base64(self, text, **_kwargs):
            self.calls.append(("base64", text, tts_routes.TTS_INFERENCE_LOCK.locked()))
            return "YXVkaW8="

    tts = FakeTTS()
    app = FastAPI()
    app.include_router(tts_routes.setup_tts_routes(tts))
    client = TestClient(app)

    assert client.post("/api/tts/synthesize", json={"text": "audio"}).status_code == 200
    assert client.post("/api/tts/synthesize", json={"text": "base64", "format": "base64"}).json() == {"audio": "YXVkaW8="}
    assert tts.calls == [("audio", "audio", True), ("base64", "base64", True)]
