"""MAD-932: Details auto-hides while a side-mounted panel is docked.

Source guards for the canonical dock-state observer and the Details panel's
auto-hide/restore/override wiring. The interactive behavior is covered by
tests/browser/mad-932-details-autohide.spec.js.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC = REPO_ROOT / "static"
MODAL_SNAP_JS = (STATIC / "js" / "modalSnap.js").read_text(encoding="utf-8")
CONTEXT_JS = (STATIC / "js" / "conversationContext.js").read_text(encoding="utf-8")


def test_dock_layer_exposes_one_side_panel_state_observer():
    # The canonical dock classes are the single source of truth.
    assert "left-dock-active" in MODAL_SNAP_JS
    assert "right-dock-active" in MODAL_SNAP_JS
    assert "export function sidePanelDocked()" in MODAL_SNAP_JS
    assert "export function watchSidePanelDock(" in MODAL_SNAP_JS
    assert "MutationObserver" in MODAL_SNAP_JS


def test_details_panel_reacts_to_the_dock_state_layer():
    assert "watchSidePanelDock" in CONTEXT_JS
    assert "sidePanelDocked" in CONTEXT_JS
    assert "from './modalSnap.js'" in CONTEXT_JS


def test_details_remembers_auto_hide_and_user_override():
    assert "_detailsAutoHidden" in CONTEXT_JS
    assert "_detailsUserOverride" in CONTEXT_JS
    # Auto hide/restore must not be recorded as a user preference.
    assert "setOpen(false, { auto: true })" in CONTEXT_JS
    assert "setOpen(true, { auto: true })" in CONTEXT_JS
    # The initial viewport-driven open is auto, so the dock governs it.
    assert "setOpen(window.matchMedia('(min-width: 1250px)').matches, { auto: true })" in CONTEXT_JS


def test_no_new_dependency_for_the_panel_state():
    import re

    imports = re.findall(r"^import .* from '([^']+)'", CONTEXT_JS, re.M)
    assert set(imports) <= {"./modelPicker.js", "./modalSnap.js"}, imports
