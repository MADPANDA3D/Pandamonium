from pathlib import Path


APP_JS = Path("static/app.js")
SESSIONS_JS = Path("static/js/sessions.js")


def _slice(source, start_marker, end_marker):
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    return source[start:end]


def test_new_chat_navigation_does_not_wait_for_model_discovery():
    source = APP_JS.read_text(encoding="utf-8")
    shared_handler = _slice(
        source,
        "function _handleNewChatAction",
        "// New session button on icon rail",
    )

    assert "sessionModule.createBlankChat()" in shared_handler
    assert "await " not in shared_handler
    assert "_refreshDefaultChat()" not in shared_handler


def test_app_uses_the_canonical_unversioned_session_module():
    source = APP_JS.read_text(encoding="utf-8")
    index = Path("static/index.html").read_text(encoding="utf-8")

    assert "from './js/sessions.js';" in source
    assert "sessions.js?v=" not in source
    assert "/static/js/sessions.js?v=" not in index


def test_desktop_new_chat_actions_use_shared_immediate_handler():
    source = APP_JS.read_text(encoding="utf-8")

    shared_handler = _slice(
        source,
        "function _handleNewChatAction",
        "// New session button on icon rail",
    )
    rail_handler = _slice(
        source,
        "// New session button on icon rail",
        "// Mobile new chat button",
    )
    brand_handler = _slice(
        source,
        "// Logo click \u2192 new chat",
        "const sidebarNewChatBtn = el('sidebar-new-chat-btn');",
    )

    assert "sessionModule.createBlankChat()" in shared_handler
    assert "_handleNewChatAction();" in rail_handler
    assert "_handleNewChatAction();" in brand_handler
    assert "const dc = await _refreshDefaultChat();" not in rail_handler
    assert "const dc = await _refreshDefaultChat();" not in brand_handler


def test_mobile_new_chat_uses_shared_immediate_handler():
    source = APP_JS.read_text(encoding="utf-8")
    mobile_handler = _slice(
        source,
        "// Mobile new chat button",
        "// Logo click \u2192 new chat",
    )

    assert "_handleNewChatAction();" in mobile_handler
    assert "_startFreshChat();" not in mobile_handler


def test_blank_chat_uses_shared_reset_with_non_materializable_navigation_sentinel():
    source = SESSIONS_JS.read_text(encoding="utf-8")
    blank = _slice(
        source,
        "export function createBlankChat()",
        "export function createDirectChat",
    )
    prepare = _slice(
        source,
        "function _prepareNewChat(pendingChat)",
        "export function createBlankChat()",
    )

    assert "_prepareNewChat({ source: 'discovering' })" in blank
    assert "clearPendingAgentTarget()" in blank
    assert "_pendingChat = pendingChat" in prepare
    assert "currentSessionId = null" in prepare
    assert "history.replaceState(null, '', window.location.pathname)" in prepare
    assert "_pendingChat.url && _pendingChat.modelId" in source


def test_manual_model_choice_is_marked_against_late_default_discovery():
    sessions = SESSIONS_JS.read_text(encoding="utf-8")
    picker = Path("static/js/modelPicker.js").read_text(encoding="utf-8")

    assert "createDirectChat(url, modelId, endpointId, source = '')" in sessions
    assert "if (source === 'manual') clearPendingAgentTarget();" in sessions
    assert "_deps.createDirectChat(m.url, m.mid, m.endpointId, 'manual')" in picker
    assert "sessionModule.createDirectChat(url, mid, endpointId, 'manual')" in Path(
        "static/js/models.js"
    ).read_text(encoding="utf-8")
    assert "latestPending.source === 'manual'" in picker
