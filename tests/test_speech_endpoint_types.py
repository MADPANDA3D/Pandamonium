"""Speech endpoint types (MAD-901): STT and TTS can be configured from the
Add Local Models type select without leaking into chat model selection."""
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / "static"


def test_local_endpoint_type_select_offers_speech_types():
    index = (STATIC / "index.html").read_text()
    select_start = index.index('id="adm-epLocalType"')
    select_html = index[select_start:index.index("</select>", select_start)]
    for value in ("llm", "image", "stt", "tts"):
        assert f'value="{value}"' in select_html


def test_route_validates_endpoint_types():
    source = (REPO / "routes" / "model_routes.py").read_text()
    assert 'MODEL_ENDPOINT_TYPES = ("llm", "image", "stt", "tts")' in source
    assert "normalize_model_endpoint_type" in source
    assert "Unsupported model type" in source


def test_speech_endpoints_do_not_seed_chat_default():
    source = (REPO / "routes" / "model_routes.py").read_text()
    assert 'if model_type == "llm" and _default_endpoint_needs_assignment(' in source


def test_admin_does_not_autoselect_speech_endpoints_for_chat():
    source = (STATIC / "js" / "admin.js").read_text()
    assert "async function _selectAddedModelInChat(endpoint)" in source
    assert "modelType !== 'llm'" in source


def test_chat_picker_skips_non_llm_endpoints():
    source = (STATIC / "js" / "modelPicker.js").read_text()
    assert source.count("(item.model_type || 'llm') !== 'llm'") >= 3
