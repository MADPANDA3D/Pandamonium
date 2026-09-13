"""MAD-924: optional setup wizard steps show live state, skip honestly, and
reuse the existing voice-preview and MAD MCP Portal connect flows."""

import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
WIZARD_JS = (REPO_ROOT / "static" / "js" / "setupWizard.js").read_text(encoding="utf-8")
SETTINGS_JS = (REPO_ROOT / "static" / "js" / "settings.js").read_text(encoding="utf-8")
MAD_MCP_JS = (REPO_ROOT / "static" / "js" / "madMcp.js").read_text(encoding="utf-8")
VOICE_PREVIEW_JS = (REPO_ROOT / "static" / "js" / "voicePreview.js").read_text(encoding="utf-8")
PORTAL_CONNECT_JS = (REPO_ROOT / "static" / "js" / "portalConnect.js").read_text(encoding="utf-8")


def test_voice_step_reads_the_existing_voice_status_setup_object():
    assert "/api/voice/status" in WIZARD_JS
    assert "renderVoice" in WIZARD_JS
    assert "setup.core_ready" in WIZARD_JS
    assert "_openStep('voice')" in WIZARD_JS
    assert "guidance" in WIZARD_JS


def test_voice_test_action_reuses_one_shared_preview_implementation():
    assert "from './voicePreview.js'" in WIZARD_JS
    assert "startVoicePreview" in WIZARD_JS
    assert "/api/tts/synthesize" not in WIZARD_JS
    assert "/api/tts/synthesize" in VOICE_PREVIEW_JS
    assert "speechSynthesis" in VOICE_PREVIEW_JS
    assert "from './voicePreview.js'" in SETTINGS_JS
    assert "startVoicePreview" in SETTINGS_JS


def test_portal_step_reuses_the_proven_mad_mcp_connect_flow():
    assert "from './portalConnect.js'" in WIZARD_JS
    assert "connectPortal" in WIZARD_JS
    assert "/api/mcp/portal/connect" not in WIZARD_JS
    assert "/api/mcp/portal/connect" in PORTAL_CONNECT_JS
    assert "master_key" in PORTAL_CONNECT_JS
    assert "portal_url" in PORTAL_CONNECT_JS
    assert "from './portalConnect.js'" in MAD_MCP_JS
    assert "connectPortal" in MAD_MCP_JS


def test_portal_step_shows_live_connected_state():
    assert "/api/mcp/portal/status" in WIZARD_JS
    assert "portal_connected" in WIZARD_JS
    assert "tool_count" in WIZARD_JS
    assert "MAD MCP Portal is connected" in PORTAL_CONNECT_JS


def test_optional_skips_persist_and_lanes_say_skipped():
    assert "pandamonium-setup-wizard-skips" in WIZARD_JS
    assert "_markSkipped(" in WIZARD_JS
    assert "_isSkipped(" in WIZARD_JS
    assert "Skipped —" in WIZARD_JS
    assert "sessionStorage" not in WIZARD_JS
    for lane in ("voice", "integrations", "extensions", "gallery", "update"):
        assert "_skipButton('" + lane + "'" in WIZARD_JS


def test_plugins_step_reports_installed_count_and_the_admin_only_note():
    assert "renderPlugins" in WIZARD_JS
    assert "extensions.installed" in WIZARD_JS
    assert "openMarketplace" in WIZARD_JS
    assert "Add Plugins" in WIZARD_JS
    assert "administrator can add plugins" in WIZARD_JS


def test_gallery_step_uses_discovery_and_the_existing_connect_entry():
    assert "renderGallery" in WIZARD_JS
    assert "/api/gallery/discovery" in WIZARD_JS
    assert "openGallery" in WIZARD_JS
    assert "sources" in WIZARD_JS


def test_updates_step_is_summary_only_with_no_privileged_actions():
    assert "renderUpdates" in WIZARD_JS
    assert "rollback_available" in WIZARD_JS
    assert "_openStep('updates')" in WIZARD_JS
    for token in ("/api/updates/", "apply_update", "rollback_update", "start_update"):
        assert token not in WIZARD_JS


def test_whats_next_finish_links_replay_the_tour_and_the_guide():
    assert "What's next" in WIZARD_JS
    assert "Take the product tour" in WIZARD_JS
    assert "Replay the setup guide" in WIZARD_JS
    assert "'/tour'" in WIZARD_JS
    assert "'/setup'" in WIZARD_JS


def test_optional_steps_never_render_raw_backend_codes():
    forbidden = (
        "extension_",
        "marketplace_",
        "provider_not_admitted",
        "update_failed",
        "portal_unavailable",
        "server_tts_required",
        "invalid_api_key",
        "traceback",
    )
    for source in (WIZARD_JS, VOICE_PREVIEW_JS, PORTAL_CONNECT_JS):
        for token in forbidden:
            assert token not in source, token


def test_optional_step_helpers_pass_their_node_checks():
    subprocess.run(["node", "tests/test_voice_preview.js"], check=True, cwd=REPO_ROOT)
    subprocess.run(["node", "tests/test_portal_connect.js"], check=True, cwd=REPO_ROOT)
