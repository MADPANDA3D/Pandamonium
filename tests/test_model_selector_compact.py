"""Compact model-selector source guards (MAD-888)."""
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static"


def test_menu_has_compact_heading_before_search():
    index = (STATIC / "index.html").read_text()
    heading = index.index('id="model-picker-heading"')
    search = index.index('class="model-picker-search-row"')
    assert heading < search


def test_rows_mark_selection_with_check():
    picker = (STATIC / "js" / "modelPicker.js").read_text()
    assert "model-switch-check" in picker
    assert "is-selected" in picker
    assert "aria-selected" in picker


def test_compact_styles_override_menu():
    css = (STATIC / "style.css").read_text()
    assert "Compact model selector (MAD-888)" in css
    assert ".model-picker-list .model-switch-item.is-selected" in css
    assert ".model-picker-menu .conversation-effort-card" in css
