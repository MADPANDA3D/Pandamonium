"""MAD-925: wizard-linked setup surfaces never render raw backend codes.

The plugin marketplace and the updater are the surfaces the setup wizard links
to; their error copy must always come from the shared mapper. The mapper itself
is exercised by ``tests/test_setup_ui.js``.
"""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC = REPO_ROOT / "static"

SETUP_UI_JS = (STATIC / "js" / "setupUi.js").read_text(encoding="utf-8")
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
MARKETPLACE_JS = (STATIC / "js" / "marketplace.js").read_text(encoding="utf-8")
UPDATER_JS = (STATIC / "js" / "updater.js").read_text(encoding="utf-8")


def test_wizard_linked_surfaces_use_the_shared_error_mapper():
    assert "from './setupUi.js'" in MARKETPLACE_JS
    assert MARKETPLACE_JS.count("humanSetupError(error") >= 4
    # updater.js stays import-free (release-bridge test loads it via data: URL)
    # and receives the shared mapper from app.js instead.
    assert "import " not in UPDATER_JS
    assert "window.humanSetupError" in UPDATER_JS
    assert "window.humanSetupError = humanSetupError" in APP_JS
    assert "humanSetupError(data.detail" in UPDATER_JS


def test_no_display_path_renders_a_raw_backend_code():
    # No display path concatenates a raw error message anymore.
    assert "error?.message" not in MARKETPLACE_JS
    assert "payload.detail ||" in MARKETPLACE_JS  # always wrapped by humanSetupError


def test_raw_code_catalog_lives_in_the_shared_helper():
    assert "extension_scan_not_found" in SETUP_UI_JS
    assert "extension_action_denied" in SETUP_UI_JS
    assert "update_failed" in SETUP_UI_JS
    assert "updater_lock_held" in SETUP_UI_JS


def test_wizard_itself_never_renders_raw_backend_codes():
    wizard_js = (STATIC / "js" / "setupWizard.js").read_text(encoding="utf-8")
    for token in ("extension_", "marketplace_", "manifest_", "updater_"):
        assert token not in wizard_js
