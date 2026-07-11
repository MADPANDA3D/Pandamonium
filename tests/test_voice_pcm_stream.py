import io
import json
import wave

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes import voice_routes
from src.voice_pcm import pcm_frames, take_speech_segment, wav_to_pcm16


def test_speech_segments_preserve_text_and_stay_bounded():
    source = (
        "Good morning, Leo. All systems are stable and the background workers are still checking their assigned sources. "
        "PC Codex has opened the Home Lab workspace, while Hermes is reviewing its operations notes. "
        "I will alert you at the next natural boundary if either worker needs a decision."
    )
    remaining = source
    chunks = []
    first = True
    while remaining:
        chunk, remaining = take_speech_segment(remaining, first=first, done=True)
        assert chunk
        chunks.append(chunk)
        first = False

    assert "".join(chunks).replace(" ", "") == source.replace(" ", "")
    assert len(chunks[0]) <= 220
    assert all(len(chunk) <= 360 for chunk in chunks[1:])


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


def test_voice_turn_audio_is_one_ordered_pcm_stream(monkeypatch, tmp_path):
    class FakeTTS:
        available = True

        def __init__(self):
            self.calls = []

        def synthesize(self, text, use_cache=True, *_args):
            self.calls.append((text, use_cache))
            payload = io.BytesIO()
            with wave.open(payload, "wb") as target:
                target.setnchannels(1)
                target.setsampwidth(2)
                target.setframerate(24_000)
                target.writeframes(b"\x01\x00" * max(2_400, len(text) * 120))
            return payload.getvalue()

    monkeypatch.setattr(voice_routes, "VOICE_STATE_FILE", tmp_path / "voice_sessions.json")
    voice_routes._SPEECH_TURNS.clear()
    tts = FakeTTS()
    app = FastAPI()
    app.include_router(voice_routes.setup_voice_routes(tts_service=tts))
    client = TestClient(app)
    session = client.post("/api/voice/sessions", json={"mode": "jarvis_call"}).json()
    speech_turn = voice_routes._register_speech_turn(session["id"])
    speech_turn.buffer = (
        "Good morning, Leo. The first response block is ready and should begin promptly. "
        "The coordinator is preparing the next block while this one is playing, which keeps the voice continuous. "
        "The complete response remains one ordered stream and is persisted only once."
    )
    speech_turn.finished = True

    response = client.get(f"/api/voice/sessions/{session['id']}/turns/{speech_turn.turn_id}/audio")
    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines() if line]
    assert events[0] == {"type": "start", "sample_rate": 24_000}
    assert events[-1]["type"] == "done"
    assert events[-1]["blocks"] == len(tts.calls)
    assert len(tts.calls) >= 2
    assert all(use_cache is False for _text, use_cache in tts.calls)
    assert all(event["type"] in {"start", "block", "audio", "done"} for event in events)

    completed = client.post(
        f"/api/voice/sessions/{session['id']}/turns/{speech_turn.turn_id}/playback",
        json={"state": "completed", "timings": {"scheduler_underruns": 0}},
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "ready"
