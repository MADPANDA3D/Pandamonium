import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    not shutil.which("node"), reason="node binary not on PATH"
)


def _node_eval(source: str) -> dict[str, object]:
    result = subprocess.run(
        ["node", "--input-type=module", "-e", source],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return json.loads(result.stdout)


def test_registry_invokes_only_registered_v1_action_and_unregisters_once():
    result = _node_eval(
        """
        import {
          FOREGROUND_ACTIONS,
          dispatchForegroundAction,
          invokeForegroundAction,
          registerForegroundAction,
        } from './static/js/foregroundActions.js';

        const calls = [];
        const unregister = registerForegroundAction(
          FOREGROUND_ACTIONS.OPEN_CALENDAR,
          payload => { calls.push({ payload, frozen: Object.isFrozen(payload) }); return 'opened'; },
        );
        const envelope = {
          version: 1,
          action: FOREGROUND_ACTIONS.OPEN_CALENDAR,
          payload: {},
        };
        const direct = invokeForegroundAction(envelope);
        const dispatched = dispatchForegroundAction({ ui_event: 'foreground_action', ...envelope });
        const firstUnregister = unregister();
        const secondUnregister = unregister();
        let afterUnregister = null;
        try { invokeForegroundAction(envelope); } catch (error) { afterUnregister = error.code; }
        console.log(JSON.stringify({
          direct,
          dispatched,
          calls,
          firstUnregister,
          secondUnregister,
          afterUnregister,
        }));
        """
    )

    assert result == {
        "direct": "opened",
        "dispatched": True,
        "calls": [
            {"payload": {}, "frozen": True},
            {"payload": {}, "frozen": True},
        ],
        "firstUnregister": True,
        "secondUnregister": False,
        "afterUnregister": "unregistered_action",
    }


def test_registry_fails_closed_before_calling_handler():
    result = _node_eval(
        """
        import {
          FOREGROUND_ACTIONS,
          dispatchForegroundAction,
          invokeForegroundAction,
          registerForegroundAction,
        } from './static/js/foregroundActions.js';

        let calls = 0;
        registerForegroundAction(FOREGROUND_ACTIONS.CLOSE_DOCUMENT, () => { calls += 1; });
        const code = fn => { try { fn(); return null; } catch (error) { return error.code; } };
        const base = { version: 1, action: FOREGROUND_ACTIONS.CLOSE_DOCUMENT, payload: {} };
        const results = {
          legacy: dispatchForegroundAction({ ui_event: 'toggle', toggle_name: 'web' }),
          unknownAction: code(() => invokeForegroundAction({ ...base, action: 'open_view:anything' })),
          wrongVersion: code(() => invokeForegroundAction({ ...base, version: 2 })),
          payloadField: code(() => invokeForegroundAction({ ...base, payload: { selector: 'body' } })),
          envelopeField: code(() => invokeForegroundAction({ ...base, url: 'https://example.test' })),
          messageField: code(() => dispatchForegroundAction({ ui_event: 'foreground_action', ...base, script: 'x' })),
          oversized: code(() => invokeForegroundAction({ ...base, action: 'x'.repeat(2000) })),
          oversizedMessage: code(() => dispatchForegroundAction({ ui_event: 'foreground_action', ...base, action: 'x'.repeat(2000) })),
          duplicate: code(() => registerForegroundAction(FOREGROUND_ACTIONS.CLOSE_DOCUMENT, () => {})),
          unknownRegistration: code(() => registerForegroundAction('custom', () => {})),
          invalidHandler: code(() => registerForegroundAction(FOREGROUND_ACTIONS.MINIMIZE_DOCUMENT, null)),
        };
        console.log(JSON.stringify({ calls, results }));
        """
    )

    assert result == {
        "calls": 0,
        "results": {
            "legacy": False,
            "unknownAction": "unknown_action",
            "wrongVersion": "unsupported_version",
            "payloadField": "invalid_payload",
            "envelopeField": "invalid_envelope",
            "messageField": "invalid_envelope",
            "oversized": "payload_too_large",
            "oversizedMessage": "payload_too_large",
            "duplicate": "duplicate_action",
            "unknownRegistration": "unknown_action",
            "invalidHandler": "invalid_handler",
        },
    }


def test_core_wiring_uses_registry_without_action_specific_stream_branches():
    app = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    stream = (ROOT / "static" / "js" / "chatStream.js").read_text(encoding="utf-8")
    service_worker = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")
    registry = (ROOT / "static" / "js" / "foregroundActions.js").read_text(
        encoding="utf-8"
    )

    for action in ("OPEN_CALENDAR", "CLOSE_DOCUMENT", "MINIMIZE_DOCUMENT"):
        assert f"registerForegroundAction(FOREGROUND_ACTIONS.{action}" in app
    calendar = (ROOT / "static" / "js" / "calendar.js").read_text(encoding="utf-8")
    assert calendar.index("Modals.isMinimized('calendar-modal')") < calendar.index(
        "if (_open) return;"
    )
    assert "dispatchForegroundAction(uiData)" in stream
    assert "uiEvent === 'open_view'" not in stream
    assert "uiEvent === 'close_view'" not in stream
    assert "uiEvent === 'minimize_view'" not in stream
    assert "querySelector" not in registry
    assert "eval(" not in registry
    assert "new Function" not in registry
    assert "'/static/js/foregroundActions.js'" in service_worker
