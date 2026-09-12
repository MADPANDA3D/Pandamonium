"""Source-level guards for the always-on bash/web tool contract (MAD-882).

Leo's product direction: there is no owner-facing bash/web toggle in the chat
UI. The buttons are already absent from index.html, the adaptive router selects
the tool schemas from prompt intent, and bash/web stay enabled unless a
deliberate server-side policy (admin panel `disabled_tools` or `manage_settings`)
disables them.

The removed-toggle design exposed two latent scope traps in the chat send
path that these guards pin, plus they pin the absence of the old client-side
toggle wiring so it cannot creep back in.
"""
from pathlib import Path

import pytest

_STATIC = Path(__file__).resolve().parent.parent / "static"


@pytest.fixture(scope="module")
def chat_js():
    return (_STATIC / "js" / "chat.js").read_text()


@pytest.fixture(scope="module")
def slash_js():
    return (_STATIC / "js" / "slashCommands.js").read_text()


@pytest.fixture(scope="module")
def app_js():
    return (_STATIC / "app.js").read_text()


@pytest.fixture(scope="module")
def index_html():
    return (_STATIC / "index.html").read_text()


def test_no_visible_bash_web_toggle_buttons(index_html):
    assert 'id="web-toggle-btn"' not in index_html, (
        "the web capability toggle button must not exist in the chat UI"
    )
    assert 'id="bash-toggle-btn"' not in index_html, (
        "the bash capability toggle button must not exist in the chat UI"
    )


def test_slash_toggle_only_manages_research(slash_js):
    assert "toggleMap = { research: 'research-toggle' };" in slash_js, (
        "/toggle must not expose bash/web capability toggles"
    )
    assert "Toggle web search" not in slash_js
    assert "Toggle bash/shell" not in slash_js


def test_no_client_side_tool_toggle_persistence(app_js):
    assert "api/tools/toggle" not in app_js, (
        "the removed owner-facing toggle must not leave client persistence code"
    )
    assert "reconcileToolPrefsFromServer" not in app_js


def test_chat_send_declares_agent_turn_outside_try(chat_js):
    # The adaptive refactor deleted the old _isAgent declaration while leaving
    # three references behind. Any path with the hidden web-toggle checked
    # evaluates them and throws "ReferenceError: _isAgent is not defined",
    # killing the send before the stream starts.
    assert "const _isAgent = !!streamAgentTarget;" in chat_js, (
        "chat.js must declare _isAgent from the selected agent target"
    )


def test_chat_streaming_tts_is_catch_visible(chat_js):
    # streamingTTS was declared inside the try block, so any stream error
    # masked the original failure with a second ReferenceError in the catch.
    assert "let streamingTTS = false;" in chat_js, (
        "streamingTTS must be declared outside the try block"
    )
