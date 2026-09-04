"""Adaptive tool discovery and legacy caller constraints.

Bug: allow_bash and allow_web_search were only read from form_data, so JSON
API callers (Content-Type: application/json) always had bash disabled.

Fix: (1) Read true and false JSON values without collapsing false.
     (2) Keep bash on the privilege fallback when unset.
     (3) Let prompt intent expose healthy web schemas when no legacy deny exists.
"""

import ast
from pathlib import Path

import pytest

from src.action_intents import classify_tool_intent
from src.tool_policy import (
    WEB_TOOL_NAMES,
    is_web_search_explicitly_denied,
    web_search_enabled_for_turn,
)

_CHAT_ROUTES = Path(__file__).resolve().parent.parent / "routes" / "chat_routes.py"


# ── Source-level guards ─────────────────────────────────────────


def test_allow_bash_reads_from_body_as_fallback():
    """chat_stream must read allow_bash from the JSON body, not just form_data."""
    source = _CHAT_ROUTES.read_text(encoding="utf-8")
    tree = ast.parse(source)

    # Find the chat_stream function
    chat_stream_func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "chat_stream":
            chat_stream_func = node
            break
    assert chat_stream_func is not None, "chat_stream function not found"

    assert 'if allow_bash is None and "allow_bash" in body:' in source
    assert 'allow_bash = body["allow_bash"]' in source


def test_allow_web_search_reads_from_body_as_fallback():
    """chat_stream must read allow_web_search from the JSON body, not just form_data."""
    source = _CHAT_ROUTES.read_text(encoding="utf-8")
    tree = ast.parse(source)

    chat_stream_func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "chat_stream":
            chat_stream_func = node
            break
    assert chat_stream_func is not None

    assert 'if allow_web_search is None and "allow_web_search" in body:' in source
    assert 'allow_web_search = body["allow_web_search"]' in source


def test_disabled_tools_respects_adaptive_defaults_and_legacy_denies():
    source = _CHAT_ROUTES.read_text(encoding="utf-8")

    # The fix changes:
    #   if str(allow_bash).lower() != "true":
    # to:
    #   if allow_bash is not None and str(allow_bash).lower() != "true":
    assert "allow_bash is not None" in source, (
        "disabled_tools check must guard against allow_bash being None"
    )
    assert "is_web_search_explicitly_denied(allow_web_search)" in source
    assert "disabled_tools.update(WEB_TOOL_NAMES)" in source, (
        "explicit legacy denial must still add web_search/web_fetch"
    )
    assert "_forced_tools = set(WEB_TOOL_NAMES)" in source, (
        "web intent must present the relevant web schemas"
    )


def test_hermes_agent_api_bypasses_local_context_preface():
    """Hermes owns its context; the local harness sends only the latest user turn."""
    source = _CHAT_ROUTES.read_text(encoding="utf-8")
    assert "messages = _hermes_agent_context_messages(sess)" in source
    assert "messages = _ensure_current_request_is_latest_user(ctx.messages, _model_message)" in source


def test_non_streaming_chat_has_no_unbound_hermes_flag():
    """A completed /api/chat call must not fail while scheduling follow-up work."""
    source = _CHAT_ROUTES.read_text(encoding="utf-8")
    tree = ast.parse(source)
    chat_endpoint = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "chat_endpoint"
    )

    hermes_reads = [
        node
        for node in ast.walk(chat_endpoint)
        if isinstance(node, ast.Name)
        and node.id == "hermes_agent_api"
        and isinstance(node.ctx, ast.Load)
    ]
    hermes_writes = [
        node
        for node in ast.walk(chat_endpoint)
        if isinstance(node, ast.Name)
        and node.id == "hermes_agent_api"
        and isinstance(node.ctx, ast.Store)
    ]

    assert not hermes_reads or hermes_writes


def test_text_chat_forwards_authority_approval_events():
    source = _CHAT_ROUTES.read_text(encoding="utf-8")

    assert '"authority_approval_required"' in source
    assert '_forced_tools.add("ui_control")' in source


# ── Functional tests of the disabled-tools logic ───────────────


def _build_disabled_tools(
    allow_bash=None,
    allow_web_search=None,
    use_web=None,
    can_use_bash=True,
    can_use_browser=True,
    explicit_web_intent=False,
    global_disabled=None,
):
    """Replicate the disabled-tools logic from chat_stream for unit testing.

    Returns the set of tool names that would be disabled.
    """
    disabled_tools = set()

    # Issue #3229 fix: only disable bash when explicitly set to a falsy value.
    if allow_bash is not None and str(allow_bash).lower() != "true":
        disabled_tools.add("bash")
    search_enabled = web_search_enabled_for_turn(allow_web_search, use_web)
    web_explicitly_denied = is_web_search_explicitly_denied(allow_web_search)
    if web_explicitly_denied:
        disabled_tools.update(WEB_TOOL_NAMES)
    if explicit_web_intent:
        disabled_tools.update({
            "bash", "python",
            "search_chats", "manage_skills", "manage_memory",
            "read_file", "write_file", "edit_file",
            "create_document", "edit_document", "update_document",
            "send_email", "reply_to_email",
            "manage_notes", "manage_calendar", "read_calendar", "manage_tasks",
            "api_call", "builtin_browser",
        })
        if not web_explicitly_denied:
            disabled_tools.difference_update(WEB_TOOL_NAMES)
        else:
            disabled_tools.update(WEB_TOOL_NAMES)
    elif search_enabled:
        disabled_tools.difference_update(WEB_TOOL_NAMES)

    # Enforce per-user privileges
    if not can_use_bash:
        disabled_tools.update({"bash", "python", "read_file", "write_file"})
    if not can_use_browser:
        disabled_tools.add("builtin_browser")
    if global_disabled and isinstance(global_disabled, list):
        disabled_tools.update(global_disabled)

    return disabled_tools


def test_json_body_allow_bash_true_enables_bash():
    """API caller sending {"allow_bash": true} gets bash enabled."""
    disabled = _build_disabled_tools(allow_bash="true")
    assert "bash" not in disabled


def test_json_body_allow_bash_false_disables_bash():
    """API caller sending {"allow_bash": false} gets bash disabled."""
    disabled = _build_disabled_tools(allow_bash="false")
    assert "bash" in disabled


def test_json_body_allow_web_search_true_enables_web():
    """API caller sending {"allow_web_search": true} gets web tools enabled."""
    disabled = _build_disabled_tools(allow_web_search="true")
    assert "web_search" not in disabled
    assert "web_fetch" not in disabled


def test_json_body_allow_web_search_false_disables_web():
    """API caller sending {"allow_web_search": false} gets web tools disabled."""
    disabled = _build_disabled_tools(allow_web_search="false")
    assert "web_search" in disabled
    assert "web_fetch" in disabled


def test_chat_mode_use_web_true_enables_web():
    """Chat pre-search sends use_web=true as the explicit web setting."""
    disabled = _build_disabled_tools(use_web="true")
    assert "web_search" not in disabled
    assert "web_fetch" not in disabled


def test_allow_web_search_false_wins_over_use_web_true():
    """The agent web toggle hard-denies web even if another path says use_web=true."""
    disabled = _build_disabled_tools(allow_web_search="false", use_web="true")
    assert "web_search" in disabled
    assert "web_fetch" in disabled


@pytest.mark.parametrize(
    "message",
    [
        "please use web search for current CVEs",
        "search the web for current CVEs",
        "can you look up the latest docs",
    ],
)
def test_explicit_false_disables_web_despite_prompt_web_intent(message):
    """Explicit allow_web_search=false is a hard deny even when the prompt
    asks for web search."""
    intent = classify_tool_intent(message)
    assert intent is not None
    assert intent.category == "web"

    disabled = _build_disabled_tools(
        allow_web_search="false",
        explicit_web_intent=True,
    )
    assert "web_search" in disabled
    assert "web_fetch" in disabled


def test_prompt_web_intent_enables_web_without_legacy_setting():
    """Prompt intent presents configured web schemas without composer flags."""
    intent = classify_tool_intent("look up the latest docs")
    assert intent is not None
    assert intent.category == "web"

    disabled = _build_disabled_tools(
        allow_web_search=None,
        use_web=None,
        explicit_web_intent=True,
    )
    assert "web_search" not in disabled
    assert "web_fetch" not in disabled


def test_admin_user_gets_bash_enabled_by_default():
    """When allow_bash is not set and user has can_use_bash privilege,
    bash must NOT be disabled.
    """
    disabled = _build_disabled_tools(allow_bash=None, can_use_bash=True)
    assert "bash" not in disabled


def test_missing_web_setting_does_not_mask_discovery():
    """Missing first-party flags are not an implicit deny."""
    disabled = _build_disabled_tools(allow_web_search=None)
    assert "web_search" not in disabled
    assert "web_fetch" not in disabled


def test_non_privileged_user_without_explicit_flag_still_disabled():
    """A user without can_use_bash privilege who doesn't send allow_bash
    should still have bash disabled via the privilege check.
    """
    disabled = _build_disabled_tools(allow_bash=None, can_use_bash=False)
    assert "bash" in disabled


def test_non_privileged_user_explicit_true_overridden_by_privilege():
    """Even if allow_bash=true is sent, a user without can_use_bash
    privilege still gets bash disabled by the privilege gate.
    """
    disabled = _build_disabled_tools(allow_bash="true", can_use_bash=False)
    assert "bash" in disabled


def test_global_disabled_web_wins_over_explicit_web_enable():
    """Admin-level disabled tools are still a hard deny."""
    disabled = _build_disabled_tools(
        allow_web_search="true",
        global_disabled=["web_search", "web_fetch"],
    )
    assert "web_search" in disabled
    assert "web_fetch" in disabled


def test_json_false_remains_an_explicit_legacy_deny():
    body = {"allow_bash": False, "allow_web_search": False}
    disabled = _build_disabled_tools(
        allow_bash=body["allow_bash"],
        allow_web_search=body["allow_web_search"],
    )
    assert "bash" in disabled
    assert WEB_TOOL_NAMES <= disabled


def test_explicit_false_disables_even_for_admin():
    """An admin who explicitly sends allow_bash=false should have bash disabled."""
    disabled = _build_disabled_tools(
        allow_bash="false", can_use_bash=True,
    )
    assert "bash" in disabled


# ── Frontend source-level guards ──────────────────────────────

_CHAT_JS = Path(__file__).resolve().parent.parent / "static" / "js" / "chat.js"


def test_adaptive_frontend_omits_per_turn_bash_flag():
    source = _CHAT_JS.read_text(encoding="utf-8")
    assert "allow_bash" not in source


def test_adaptive_frontend_omits_per_turn_web_flag_and_controls():
    source = _CHAT_JS.read_text(encoding="utf-8")
    index = (Path(__file__).resolve().parent.parent / "static" / "index.html").read_text(encoding="utf-8")
    assert "allow_web_search" not in source
    assert 'id="web-toggle-btn"' not in index
    assert 'id="bash-toggle-btn"' not in index
