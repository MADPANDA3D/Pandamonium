"""Compact model-selector source guards (MAD-888, MAD-898)."""
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
    assert ".composer-effort-popover" in css


def test_effort_control_sits_in_composer_not_menu():
    """MAD-898: the effort card is a composer popover next to the picker, and
    the collapsed composer shows a chip with the current value."""
    index = (STATIC / "index.html").read_text()
    list_at = index.index('id="model-picker-list"')
    card_at = index.index('id="conversation-effort-card"')
    # The menu closes (list close + menu close) before the effort card starts.
    assert index[list_at:card_at].count('</div>') >= 2, (
        "the effort card must be outside the switching menu"
    )
    assert 'id="composer-effort-btn"' in index
    assert 'id="composer-effort-value"' in index
    wrap_at = index.index('id="model-picker-wrap"')
    assert wrap_at < card_at, "the popover must anchor inside the picker wrap"
    assert 'composer-effort-popover' in index


def test_effort_chip_updates_and_closes():
    context = (STATIC / "js" / "conversationContext.js").read_text()
    assert "composer-effort-value" in context
    assert "closeEffortPopover" in context
    assert "effort-open" in context
    assert "composer-effort-btn" in context
