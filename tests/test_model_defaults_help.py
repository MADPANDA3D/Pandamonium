"""MAD-931: guided help for the AI Defaults model sections.

Source guards for the per-section help controls, the human-only help copy, and
the model-defaults tour chapter. The interactive behavior is covered by
tests/browser/mad-931-model-defaults-help.spec.js.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC = REPO_ROOT / "static"
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
MODEL_HELP_JS = (STATIC / "js" / "modelHelp.js").read_text(encoding="utf-8")
SLASH_JS = (STATIC / "js" / "slashCommands.js").read_text(encoding="utf-8")
WIZARD_JS = (STATIC / "js" / "setupWizard.js").read_text(encoding="utf-8")
SETTINGS_JS = (STATIC / "js" / "settings.js").read_text(encoding="utf-8")

MODEL_SECTIONS = {
    "utility": "#set-utilityModelSelect",
    "vision": "#set-vlModelSelect",
    "research": "#set-researchModel",
    "image": "#set-imgModelSelect",
    "voice": "#set-ttsModelSelect",
}


def test_every_model_section_has_a_help_control():
    for key, select_id in MODEL_SECTIONS.items():
        assert f'data-model-help="{key}"' in INDEX
        assert select_id.lstrip("#") in INDEX
        # The help control is a real button (keyboard reachable) and says "?".
        marker = f'data-model-help="{key}"'
        button_start = INDEX.rindex("<button", 0, INDEX.index(marker) + len(marker))
        button_html = INDEX[button_start:INDEX.index("</button>", button_start)]
        assert 'type="button"' in button_html
        assert "settings-help-btn" in button_html
        assert "aria-label=" in button_html
        assert "?" in button_html


def test_help_button_sits_in_the_matching_model_card():
    import re

    for key, select_id in MODEL_SECTIONS.items():
        # The button and the lane's select share the same admin-card.
        card = re.search(
            r'<div class="admin-card"[^>]*>(?:(?!<div class="admin-card").)*?' + re.escape(select_id.lstrip("#")),
            INDEX,
            re.S,
        )
        assert card, select_id
        assert f'data-model-help="{key}"' in card.group(0), key


def test_model_help_module_exposes_all_five_guides_and_the_popup_api():
    for key in MODEL_SECTIONS:
        assert f"{key}:" in MODEL_HELP_JS
    for token in ("openModelHelp", "closeModelHelp", "initModelHelp", "MODEL_HELP"):
        assert token in MODEL_HELP_JS
    # Reuses the existing tour tooltip design instead of a new modal framework.
    assert "tour-tooltip" in MODEL_HELP_JS
    assert "tour-halo" in MODEL_HELP_JS
    assert "bindMenuDismiss" in MODEL_HELP_JS
    assert "setAttribute('role'" in MODEL_HELP_JS
    assert "'dialog'" in MODEL_HELP_JS


def test_help_copy_explains_what_to_pick_fallbacks_and_tradeoffs():
    for key in MODEL_SECTIONS:
        section_start = MODEL_HELP_JS.index(f"{key}: {{")
        next_keys = [MODEL_HELP_JS.find(f"{other}: {{", section_start + 1) for other in MODEL_SECTIONS]
        section_end = min([pos for pos in next_keys if pos != -1] or [len(MODEL_HELP_JS)])
        section = MODEL_HELP_JS[section_start:section_end]
        assert "what:" in section, key
        assert "pick:" in section, key
        assert "fallback" in section.lower(), key
        assert "cost:" in section, key
        assert len(section) > 300, key


def test_help_copy_never_leaks_backend_field_names_or_codes():
    forbidden = (
        "endpoint_id",
        "model_id",
        "api_key",
        "tts_provider",
        "reasoning_level",
        "utility_model",
        "vision_model",
        "image_model",
        "research_model",
        "provider_not_admitted",
        "traceback",
        "server_tts_required",
    )
    for token in forbidden:
        assert token not in MODEL_HELP_JS, token


def test_settings_wires_the_help_buttons():
    assert "initModelHelp" in SETTINGS_JS
    assert "modelHelp.js" in SETTINGS_JS


def test_tour_has_a_model_defaults_chapter_that_replays_without_resetting():
    assert "'tour-models'" in SLASH_JS or '"tour-models"' in SLASH_JS
    assert "_cmdTourModels" in SLASH_JS
    # Every model card is highlighted by the chapter.
    for select_id in MODEL_SECTIONS.values():
        assert f"has({select_id})" in SLASH_JS, select_id
    # Replay language: the chapter explains it can be re-run, and links the
    # guided setup rather than resetting anything.
    assert "replay" in SLASH_JS.lower()


def test_first_run_guide_links_to_the_model_defaults_chapter():
    assert "Model defaults" in WIZARD_JS
    assert "'/tour-models'" in WIZARD_JS
