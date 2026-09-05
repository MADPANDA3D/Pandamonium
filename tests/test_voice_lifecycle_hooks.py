from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_app_owns_voice_surface_and_precaches_contract():
    index = (ROOT / "static/index.html").read_text(encoding="utf-8")
    worker = (ROOT / "static/sw.js").read_text(encoding="utf-8")

    assert 'id="pandamonium-voice-surface-root"' in index
    assert "'/static/js/voiceLifecycle.js'" in worker


def test_voice_module_loader_is_fixed_and_same_origin():
    source = (ROOT / "static/js/voiceLifecycle.js").read_text(encoding="utf-8")

    assert "recorder: () => import('./voiceRecorder.js')" in source
    assert "tts: () => import('./tts-ai.js')" in source
    assert "import(moduleId)" not in source
    assert "eval(" not in source
    assert "new Function(" not in source


def test_core_modules_emit_the_documented_lifecycle_signals():
    recorder = (ROOT / "static/js/voiceRecorder.js").read_text(encoding="utf-8")
    chat = (ROOT / "static/js/chat.js").read_text(encoding="utf-8")
    tts = (ROOT / "static/js/tts-ai.js").read_text(encoding="utf-8")

    assert "'capture-started'" in recorder
    assert "'capture-stopped'" in recorder
    assert "'stream-complete'" in chat
    assert "'stream-interrupted'" in chat
    assert "import { emitVoiceLifecycle } from './voiceLifecycle.js';" in chat
    assert "'tts-started'" in tts
    assert "'tts-idle'" in tts
