"""MAD-928: the protocol settings panel and raw JSON editors are removed.

Runtime protocol mounting is deliberately unchanged (see the
``tests/test_protocol_*.py`` suites); only the operator UI is gone. Model
context windows and input token budgets keep resolving from the discovered
provider catalog.
"""
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static"

REMOVED_MARKERS = (
    "set-protocolCard",
    "set-protocolLayerEnabled",
    "set-protocolPackList",
    "set-protocolStatus",
    "set-protocolMsg",
    "set-protocolSave",
    "set-protocolReset",
    "set-modelContextWindows",
    "set-modelInputTokenBudgets",
)

REMOVED_SETTINGS_KEYS = (
    "protocol_layer_enabled",
    "disabled_protocol_packs",
    "model_context_windows",
    "model_input_token_budgets",
)


def test_index_has_no_protocol_ui():
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    for marker in REMOVED_MARKERS:
        assert marker not in index, f"protocol UI marker still present: {marker}"


def test_settings_js_has_no_protocol_wiring():
    settings = (STATIC / "js" / "settings.js").read_text(encoding="utf-8")
    assert "initProtocolSettings" not in settings
    for key in REMOVED_SETTINGS_KEYS:
        assert key not in settings, f"settings.js still references {key}"


def test_identity_card_and_model_defaults_remain():
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    # MAD-929 moved the identity editor to its own tab; it still ships.
    assert 'data-settings-tab="identities"' in index
    assert 'id="set-identityList"' in index
    assert 'id="set-defaultEpSelect"' in index


def test_runtime_protocol_suites_remain():
    tests_dir = Path(__file__).resolve().parent
    for name in (
        "test_protocol_registry.py",
        "test_protocol_mounting.py",
        "test_protocol_duty_packs.py",
        "test_protocol_controls.py",
        "test_protocol_parity.py",
    ):
        assert (tests_dir / name).is_file(), f"runtime protocol suite missing: {name}"
