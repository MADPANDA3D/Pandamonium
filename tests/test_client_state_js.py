import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node binary not on PATH")


def _node_eval(source: str):
    result = subprocess.run(
        ["node", "--input-type=module", "-e", source],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_snapshot_is_versioned_allowlisted_and_scoped():
    result = _node_eval(
        """
        import {
          getClientStateSnapshot,
          markClientStateView,
          registerClientStateProvider,
        } from './static/js/clientState.js';

        registerClientStateProvider('calendar', () => ({
          version: 1, open: true, minimized: false, view: 'week', date: '2026-07-15',
          title: 'private event', html: '<secret>',
        }));
        registerClientStateProvider('document', () => ({
          version: 1, open: true, minimized: false, id: 'doc_123',
          content: 'private document', path: '/private/file',
        }));
        markClientStateView('document');
        markClientStateView('calendar');
        const all = getClientStateSnapshot();
        const documentOnlyWhileCalendarForeground = getClientStateSnapshot(['document']);
        markClientStateView('calendar', false);
        const documentOnly = getClientStateSnapshot(['document']);
        console.log(JSON.stringify({ all, documentOnlyWhileCalendarForeground, documentOnly }));
        """
    )

    assert result == {
        "all": {
            "version": 1,
            "active_view": "calendar",
            "slices": {
                "calendar": {
                    "version": 1,
                    "open": True,
                    "minimized": False,
                    "view": "week",
                    "date": "2026-07-15",
                },
                "document": {
                    "version": 1,
                    "open": True,
                    "minimized": False,
                    "id": "doc_123",
                },
            },
            "unavailable": [],
        },
        "documentOnly": {
            "version": 1,
            "active_view": "document",
            "slices": {
                "document": {
                    "version": 1,
                    "open": True,
                    "minimized": False,
                    "id": "doc_123",
                }
            },
            "unavailable": [],
        },
        "documentOnlyWhileCalendarForeground": {
            "version": 1,
            "active_view": "chat",
            "slices": {
                "document": {
                    "version": 1,
                    "open": True,
                    "minimized": False,
                    "id": "doc_123",
                }
            },
            "unavailable": [],
        },
    }


def test_calendar_view_enum_and_minimized_semantics():
    result = _node_eval(
        """
        import {
          getClientStateSnapshot,
          markClientStateView,
          registerClientStateProvider,
        } from './static/js/clientState.js';

        let view = 'month';
        let calendar = { version: 1, open: true, minimized: false, date: '2026-07-15' };
        let document = { version: 1, open: false, minimized: true, id: 'doc_123' };
        registerClientStateProvider('calendar', () => ({ ...calendar, view }));
        registerClientStateProvider('document', () => document);
        markClientStateView('calendar');
        markClientStateView('document');

        const acceptedViews = {};
        for (view of ['month', 'week', 'year', 'agenda']) {
          acceptedViews[view] = getClientStateSnapshot(['calendar']).slices.calendar.view;
        }
        view = 'day';
        const invalidView = getClientStateSnapshot(['calendar']);
        view = 'agenda';
        calendar = { ...calendar, open: false, minimized: true };
        const minimized = getClientStateSnapshot();
        calendar = { ...calendar, open: true, minimized: true };
        document = { ...document, open: true };
        const contradictory = getClientStateSnapshot();
        console.log(JSON.stringify({ acceptedViews, invalidView, minimized, contradictory }));
        """
    )

    assert result["acceptedViews"] == {
        "month": "month",
        "week": "week",
        "year": "year",
        "agenda": "agenda",
    }
    assert result["invalidView"] == {
        "version": 1,
        "active_view": "chat",
        "slices": {
            "calendar": {
                "version": 1,
                "open": False,
                "minimized": False,
                "view": None,
                "date": None,
            }
        },
        "unavailable": ["calendar"],
    }
    assert result["minimized"] == {
        "version": 1,
        "active_view": "chat",
        "slices": {
            "calendar": {
                "version": 1,
                "open": False,
                "minimized": True,
                "view": "agenda",
                "date": "2026-07-15",
            },
            "document": {
                "version": 1,
                "open": False,
                "minimized": True,
                "id": "doc_123",
            },
        },
        "unavailable": [],
    }
    assert result["contradictory"]["active_view"] == "chat"
    assert result["contradictory"]["unavailable"] == ["calendar", "document"]


def test_invalid_providers_fail_closed_without_invoking_unknown_getters():
    result = _node_eval(
        """
        import {
          getClientStateSnapshot,
          markClientStateView,
          registerClientStateProvider,
        } from './static/js/clientState.js';

        let secretRead = false;
        registerClientStateProvider('calendar', () => {
          const state = { version: 1, open: 'yes', minimized: false, view: 'month', date: '2026-07-15' };
          Object.defineProperty(state, 'secret', { enumerable: true, get() { secretRead = true; return 'token'; } });
          return state;
        });
        registerClientStateProvider('document', () => ({
          version: 1, open: true, minimized: false, id: 'x'.repeat(129),
        }));
        markClientStateView('calendar');
        markClientStateView('document');
        console.log(JSON.stringify({ snapshot: getClientStateSnapshot(), secretRead }));
        """
    )

    assert result == {
        "snapshot": {
            "version": 1,
            "active_view": "chat",
            "slices": {
                "calendar": {
                    "version": 1,
                    "open": False,
                    "minimized": False,
                    "view": None,
                    "date": None,
                },
                "document": {
                    "version": 1,
                    "open": False,
                    "minimized": False,
                    "id": None,
                },
            },
            "unavailable": ["calendar", "document"],
        },
        "secretRead": False,
    }


def test_registry_rejects_unknown_duplicate_and_unbounded_requests():
    result = _node_eval(
        """
        import {
          CLIENT_STATE_MAX_BYTES,
          getClientStateSnapshot,
          markClientStateView,
          registerClientStateProvider,
        } from './static/js/clientState.js';

        const provider = () => ({ version: 1, open: false, minimized: false, view: null, date: null });
        registerClientStateProvider('calendar', provider);
        const rejected = {};
        for (const [name, fn] of Object.entries({
          unknownProvider: () => registerClientStateProvider('browser', provider),
          duplicateProvider: () => registerClientStateProvider('calendar', provider),
          unknownView: () => markClientStateView('browser'),
          duplicateSlice: () => getClientStateSnapshot(['calendar', 'calendar']),
          unknownSlice: () => getClientStateSnapshot(['browser']),
          emptyRequest: () => getClientStateSnapshot([]),
        })) {
          try { fn(); rejected[name] = false; } catch (_) { rejected[name] = true; }
        }
        const snapshot = getClientStateSnapshot(['calendar']);
        console.log(JSON.stringify({
          rejected,
          withinLimit: JSON.stringify(snapshot).length <= CLIENT_STATE_MAX_BYTES,
        }));
        """
    )

    assert result == {
        "rejected": {
            "unknownProvider": True,
            "duplicateProvider": True,
            "unknownView": True,
            "duplicateSlice": True,
            "unknownSlice": True,
            "emptyRequest": True,
        },
        "withinLimit": True,
    }
