"""Regression: a malformed tts_speed must not crash TTS.

services/tts/tts_service.py read `float(settings.get("tts_speed", "1"))` with no
guard in both synthesize() and get_stats(). The manage_settings agent tool maps
"speech speed"/"voice speed" to tts_speed and (because the default is a string)
writes the value through unvalidated, so an agent or a hand-edited settings.json
could store "fast"/"" and then GET /api/tts/stats and POST /api/tts/synthesize
both 500 with ValueError until the JSON is fixed by hand. The settings layer
tolerates corrupt config; this consumer now does too.
"""
from services.tts.tts_service import TTSService
from fastapi import FastAPI
from fastapi.testclient import TestClient
from routes.tts_routes import setup_tts_routes

_BAD_SETTINGS = {
    "tts_enabled": True, "tts_provider": "browser",
    "tts_model": "tts-1", "tts_voice": "alloy", "tts_speed": "fast",
}


def test_get_stats_does_not_crash_on_malformed_speed(monkeypatch, tmp_path):
    service = TTSService(cache_dir=str(tmp_path))
    monkeypatch.setattr(service, "_load_settings", lambda: dict(_BAD_SETTINGS))
    stats = service.get_stats()          # raised ValueError before the fix
    assert stats["speed"] == 1.0


def test_synthesize_does_not_crash_on_malformed_speed(monkeypatch, tmp_path):
    service = TTSService(cache_dir=str(tmp_path))
    monkeypatch.setattr(service, "_load_settings", lambda: dict(_BAD_SETTINGS))
    # 'browser' provider returns None after the (now guarded) speed parse;
    # the point is that the malformed speed no longer raises ValueError first.
    assert service.synthesize("hello", use_cache=False) is None


def test_tts_route_accepts_preview_overrides():
    class FakeTTS:
        available = True

        def __init__(self):
            self.last = None

        def _load_settings(self):
            return {"tts_provider": "endpoint:workstation-kokoro-tts", "tts_model": "kokoro-onnx", "tts_voice": "af_sarah", "tts_speed": "1"}

        def get_stats(self):
            return {"available": True, "ready": True, "voice": "af_sarah"}

        def list_voices(self):
            return [{"id": "af_sarah", "label": "Sarah"}]

        def synthesize(self, text, **kwargs):
            self.last = {"text": text, **kwargs}
            return b"RIFF....WAVE"

    fake = FakeTTS()
    app = FastAPI()
    app.include_router(setup_tts_routes(fake))
    client = TestClient(app)

    voices = client.get("/api/tts/voices")
    assert voices.status_code == 200
    assert voices.json()["voices"][0]["id"] == "af_sarah"

    audio = client.post("/api/tts/synthesize", json={
        "text": "sample",
        "format": "audio",
        "model": "kokoro-onnx",
        "voice": "am_adam",
        "speed": "0.75",
        "use_cache": False,
    })
    assert audio.status_code == 200
    assert fake.last["voice"] == "am_adam"
    assert fake.last["speed"] == "0.75"
    assert fake.last["use_cache"] is False
