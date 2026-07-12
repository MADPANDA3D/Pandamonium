import asyncio
import io
import json
import wave

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes import tts_routes, voice_routes
from src.voice_pcm import pcm_frames, wav_to_pcm16


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


def test_voice_turn_audio_uses_one_complete_inference(monkeypatch, tmp_path):
    class FakeTTS:
        available = True

        def __init__(self):
            self.calls = []

        def synthesize(self, text, use_cache=True):
            self.calls.append((text, use_cache))
            return b"ID3complete-audio"

    monkeypatch.setattr(voice_routes, "VOICE_STATE_FILE", tmp_path / "voice_sessions.json")
    voice_routes._SPEECH_TURNS.clear()
    tts = FakeTTS()
    app = FastAPI()
    app.include_router(voice_routes.setup_voice_routes(tts_service=tts))
    client = TestClient(app)
    session = client.post("/api/voice/sessions", json={"mode": "jarvis_call"}).json()
    speech_turn = voice_routes._register_speech_turn(session["id"])
    spoken = (
        "Good morning, Leo. The first response block is ready and should begin promptly. "
        "The complete response remains one inference and is persisted only once."
    )
    asyncio.run(speech_turn.complete(spoken))

    response = client.get(f"/api/voice/sessions/{session['id']}/turns/{speech_turn.turn_id}/audio")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/mpeg")
    assert response.content == b"ID3complete-audio"
    assert tts.calls == [(spoken, False)]

    completed = client.post(
        f"/api/voice/sessions/{session['id']}/turns/{speech_turn.turn_id}/playback",
        json={"state": "completed", "timings": {"scheduler_underruns": 0}},
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "ready"


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
