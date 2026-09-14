"""The setup wizard replaces the passive checklist with guided, admin-aware steps."""

import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
APP_JS = (REPO_ROOT / "static" / "app.js").read_text(encoding="utf-8")
WIZARD_JS = (REPO_ROOT / "static" / "js" / "setupWizard.js").read_text(encoding="utf-8")
SLASH_JS = (REPO_ROOT / "static" / "js" / "slashCommands.js").read_text(encoding="utf-8")
CONNECT_JS = (REPO_ROOT / "static" / "js" / "modelConnect.js").read_text(encoding="utf-8")
STYLE_CSS = (REPO_ROOT / "static" / "style.css").read_text(encoding="utf-8")


def test_wizard_is_wired_to_the_guide_modal_and_guide_button():
    assert "guide-modal" in (REPO_ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert "userBarGuide.hidden = false" in APP_JS
    assert "setupWizardModule.open()" in APP_JS


def test_wizard_auto_opens_only_for_admins_with_incomplete_required_steps():
    assert "export async function maybeAutoOpen(authStatus)" in WIZARD_JS
    assert "if (!authStatus || !authStatus.is_admin) return;" in WIZARD_JS
    assert "_isDismissed()" in WIZARD_JS
    assert "status.identity?.configured && status.model?.usable" in WIZARD_JS


def test_wizard_never_renders_raw_backend_error_codes():
    forbidden = (
        "extension_",
        "marketplace_",
        "provider_not_admitted",
        "update_failed",
    )
    for token in forbidden:
        assert token not in WIZARD_JS
    assert "could not" in WIZARD_JS or "couldn't" in WIZARD_JS


def test_non_admin_welcome_copy_points_at_the_administrator():
    assert "MANAGED_BY_ADMIN_COPY" in APP_JS
    assert "window._isAdmin === false" in APP_JS
    assert "createModelSetupEntry" in APP_JS
    assert "Setup is managed by your administrator" not in APP_JS


def test_wizard_dismissal_is_persistent_not_session_scoped():
    assert "Storage.get(DISMISS_KEY)" in WIZARD_JS
    assert "Storage.set(DISMISS_KEY, '1')" in WIZARD_JS
    assert "sessionStorage" not in WIZARD_JS


def test_wizard_styles_replaced_the_retired_checklist_classes():
    assert ".setup-wizard" in STYLE_CSS
    assert ".setup-lane" in STYLE_CSS
    assert ".first-run-guide" not in STYLE_CSS


def test_wizard_model_step_connects_inline_without_opening_settings():
    assert "from './modelConnect.js'" in WIZARD_JS
    assert "connectDetectedEndpoint" in WIZARD_JS
    assert "Scan this machine" in WIZARD_JS
    assert "Skip for now" in WIZARD_JS
    assert "Open Add Models" not in WIZARD_JS


def test_chat_setup_flow_reuses_the_shared_model_connect_helper():
    assert "from './modelConnect.js'" in SLASH_JS
    assert "connectDetectedEndpoint" in SLASH_JS
    assert "detectProvider" in SLASH_JS or "connectDetectedEndpoint" in SLASH_JS
    assert "const PROVIDER_PATTERNS = [" not in SLASH_JS


def test_model_connect_helper_stays_pure_and_dom_free():
    assert "document." not in CONNECT_JS
    assert "import " not in CONNECT_JS
    assert "export function detectProvider" in CONNECT_JS
    assert "export async function connectDetectedEndpoint" in CONNECT_JS
    assert "export function connectResultMessage" in CONNECT_JS


def test_wizard_model_failure_copy_is_human_with_next_steps():
    assert "We couldn't" in CONNECT_JS
    assert "try again" in CONNECT_JS
    for token in ("provider_key_invalid", "invalid_api_key", "traceback"):
        assert token not in CONNECT_JS
    assert "any chat models" in CONNECT_JS
    assert "Found" in CONNECT_JS


def test_wizard_model_skip_keeps_the_requirement_honest():
    assert "Skip for now" in WIZARD_JS
    assert "connect a model" in WIZARD_JS
    assert "Leave it for now" not in WIZARD_JS


def test_model_connect_helper_passes_its_node_checks():
    subprocess.run(
        ["node", "tests/test_model_connect.js"],
        check=True,
        cwd=REPO_ROOT,
    )
