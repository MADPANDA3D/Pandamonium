"""UI source guards for the operator access-level control (MAD-885).

A shield button sits immediately left of the Jarvis sphere in the chat input
bar and opens a 3-option menu with Leo's exact labels. These guards pin the
markup, the wiring in app.js, and the option set.
"""
from pathlib import Path

import pytest

_STATIC = Path(__file__).resolve().parent.parent / "static"


@pytest.fixture(scope="module")
def index_html():
    return (_STATIC / "index.html").read_text()


@pytest.fixture(scope="module")
def app_js():
    return (_STATIC / "app.js").read_text()


@pytest.fixture(scope="module")
def access_mode_js():
    return (_STATIC / "js" / "accessMode.js").read_text()


def test_access_button_is_left_of_jarvis_sphere(index_html):
    btn = index_html.index('id="access-mode-btn"')
    sphere = index_html.index('id="jarvis-input-sphere"')
    assert btn < sphere, "the access-mode button must sit left of the Jarvis sphere"


def test_access_menu_has_exact_three_options(index_html):
    menu_markup = index_html[index_html.index('id="access-mode-menu"'):index_html.index('</div>', index_html.index('id="access-mode-menu"'))]
    assert "Ask for approval" in menu_markup
    assert "Always ask to edit external files and use the internet" in menu_markup
    assert "Approve for me" in menu_markup
    assert "Only ask for actions detected as potentially unsafe" in menu_markup
    assert "Full access" in menu_markup
    assert "Unrestricted access to the internet and any file on your computer" in menu_markup
    assert menu_markup.count('class="access-mode-option"') == 3


def test_app_wires_access_mode_module(app_js):
    assert "accessModeModule" in app_js, (
        "app.js must import the access-mode module"
    )
    assert "accessModeModule.initAccessMode()" in app_js, (
        "app.js must initialize the access-mode control"
    )


def test_access_mode_module_persists_via_prefs(access_mode_js):
    assert "api/prefs/access_mode" in access_mode_js, (
        "the access-mode control must persist through the prefs endpoint"
    )
    assert "ask_for_approval" in access_mode_js
    assert "approve_for_me" in access_mode_js
    assert "full_access" in access_mode_js