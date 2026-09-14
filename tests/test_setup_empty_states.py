"""MAD-925: no-model surfaces share one wizard entry.

These are text guards over the shipped surfaces (the browser flow is covered by
``tests/browser/setup-empty-states.spec.js`` and the helper logic by
``tests/test_setup_ui.js``). The wizard-linked error-copy guards live in
``tests/test_setup_error_copy.py``.
"""

import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC = REPO_ROOT / "static"

SETUP_UI_JS = (STATIC / "js" / "setupUi.js").read_text(encoding="utf-8")
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
MODELS_JS = (STATIC / "js" / "models.js").read_text(encoding="utf-8")
PICKER_JS = (STATIC / "js" / "modelPicker.js").read_text(encoding="utf-8")
INDEX_HTML = (STATIC / "index.html").read_text(encoding="utf-8")


def test_shared_entry_opens_the_wizard_at_the_model_step():
    # Import-free so the node copy tests can load the file directly.
    assert "import " not in SETUP_UI_JS
    assert "window.setupWizardModule" in SETUP_UI_JS
    assert "open({ step: 'model' })" in SETUP_UI_JS
    assert "Managed by your administrator" in SETUP_UI_JS
    assert "Connect a model engine" in SETUP_UI_JS


def test_every_no_model_surface_uses_the_shared_entry():
    assert "from './js/setupUi.js'" in APP_JS
    assert "from './setupUi.js'" in MODELS_JS
    assert "from './setupUi.js'" in PICKER_JS
    assert "createModelSetupEntry" in APP_JS
    assert "createModelSetupEntry" in MODELS_JS
    assert "createModelSetupEntry" in PICKER_JS


def test_no_setup_command_instruction_remains_in_no_model_copy():
    for source in (MODELS_JS, PICKER_JS, INDEX_HTML):
        assert "Type /setup" not in source
        assert "type /setup" not in source
    assert "Open Admin to add endpoints" not in MODELS_JS
    assert "Ask an admin to configure model endpoints" not in MODELS_JS


def test_non_admin_copy_is_unified_across_setup_surfaces():
    for source in (APP_JS, MODELS_JS, PICKER_JS):
        assert "MANAGED_BY_ADMIN_COPY" in source
    assert "Setup is managed by your administrator" not in APP_JS


def test_setup_ui_helper_passes_its_node_checks():
    subprocess.run(
        ["node", "tests/test_setup_ui.js"],
        check=True,
        cwd=REPO_ROOT,
    )
