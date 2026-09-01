"""Regression guard for review-before-add model discovery.

``admin.js`` is browser-coupled and cannot be imported directly in the Python
suite, so this pins the small wiring contract that is also exercised in the
CT105 browser acceptance: Scan is visible, findings have a live region, and
the scan handler only registers a server from an explicit result-button click.
"""

from pathlib import Path


_REPO = Path(__file__).resolve().parent.parent
_INDEX = (_REPO / "static" / "index.html").read_text(encoding="utf-8")
_ADMIN = (_REPO / "static" / "js" / "admin.js").read_text(encoding="utf-8")


def test_model_scan_is_visible_and_reviews_findings_before_add():
    scan_button = _INDEX.index('id="adm-epDiscoverBtn"')
    hidden_menu = _INDEX.index('id="adm-epLocalMoreMenu"')
    assert scan_button < hidden_menu, "Scan Models must not be hidden in the overflow menu"
    assert 'id="adm-epDiscoverResults"' in _INDEX
    assert 'aria-live="polite"' in _INDEX[_INDEX.index('id="adm-epDiscoverResults"') :][:240]

    scan_handler = _ADMIN[
        _ADMIN.index("const discoverBtn = el('adm-epDiscoverBtn')") :
        _ADMIN.index("document.querySelectorAll('.adm-quickstart-section')")
    ]
    assert "addBtn.addEventListener('click'" in scan_handler
    assert "fetch('/api/model-endpoints', { method: 'POST'" in scan_handler
    assert "for (const item of items)" not in scan_handler

