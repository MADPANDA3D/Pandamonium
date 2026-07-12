import os
import tempfile
from services.stt.stt_service import STTService


def test_stt_local_transcribe_leak_on_error():
    service = STTService()

    class MockWhisper:
        def transcribe(self, *args, **kwargs):
            raise ValueError("Simulated transcribe error")

    service._get_whisper = lambda: MockWhisper()

    # Track WebM files in the temp directory before running transcription
    temp_dir = tempfile.gettempdir()
    webm_before = {f for f in os.listdir(temp_dir) if f.endswith(".webm")}

    # Run transcription, which will raise ValueError internally
    result = service._transcribe_local(b"dummy_audio_data")

    # Track WebM files in the temp directory after running transcription
    webm_after = {f for f in os.listdir(temp_dir) if f.endswith(".webm")}

    # Assert that it returned None (failure)
    assert result is None

    # Assert that no new temp files were leaked
    leaked = webm_after - webm_before
    assert len(leaked) == 0, f"Leaked files: {leaked}"


def test_stt_rejects_punctuation_only_transcripts():
    service = STTService()
    service._load_settings = lambda: {
        "stt_enabled": True,
        "stt_provider": "endpoint:test",
        "stt_model": "base",
        "stt_language": "en",
    }
    service._transcribe_api = lambda *_args: " . . . . . "
    assert service.transcribe(b"silence") == ""

    service._transcribe_api = lambda *_args: "  Olá, Leo!  "
    assert service.transcribe(b"speech") == "Olá, Leo!"
