"""Regression test for the agent loop-breaker's runaway backstop.

A legitimate batch of DISTINCT tool calls (e.g. creating 18 calendar events at
once) must not be flagged as a runaway loop. Only the SAME exact call repeated
an absurd number of times is a real runaway. Previously the backstop counted
per-tool-type totals, so any batch of >=15 distinct calls to one tool was
aborted and the calls were silently discarded.
"""
import sys
import collections
from unittest.mock import MagicMock

# Mock heavy deps so importing src.agent_loop doesn't load the full app stack.
_MOCKED = [
    'sqlalchemy', 'sqlalchemy.orm', 'sqlalchemy.ext', 'sqlalchemy.ext.declarative',
    'sqlalchemy.ext.hybrid', 'sqlalchemy.sql', 'sqlalchemy.sql.expression',
    'src.database', 'src.agent_tools', 'core.models', 'core.database',
]
for _m in _MOCKED:
    sys.modules.setdefault(_m, MagicMock())

from src.agent_loop import _detect_runaway_call, _record_repeated_api_failure


def _freq(sigs):
    c = collections.Counter()
    for s in sigs:
        c[s] += 1
    return c


def test_distinct_batch_is_not_runaway():
    # 18 distinct manage_calendar create_event calls (the "add 18 birthdays" case)
    sigs = [f'manage_calendar:{{"action":"create_event","summary":"Birthday {n}"}}'
            for n in range(18)]
    assert _detect_runaway_call(_freq(sigs)) is None


def test_many_distinct_same_tool_is_not_runaway():
    sigs = [f'bash:echo {i}' for i in range(30)]
    assert _detect_runaway_call(_freq(sigs)) is None


def test_identical_call_repeated_is_runaway():
    sigs = ['manage_calendar:{"action":"list_events"}'] * 15
    assert _detect_runaway_call(_freq(sigs)) == 'manage_calendar'


def test_below_threshold_is_not_runaway():
    sigs = ['bash:ls'] * 14
    assert _detect_runaway_call(_freq(sigs)) is None


def test_threshold_is_configurable():
    sigs = ['web_search:python'] * 5
    assert _detect_runaway_call(_freq(sigs), threshold=5) == 'web_search'
    assert _detect_runaway_call(_freq(sigs), threshold=6) is None


def test_empty_is_not_runaway():
    assert _detect_runaway_call(collections.Counter()) is None


def test_distinct_api_paths_with_same_http_failure_are_bounded():
    failures = collections.Counter()
    result = {"error": "HTTP 302\nMoved", "exit_code": 1}

    first = _record_repeated_api_failure(
        failures,
        "api_call",
        '{"integration_id":"relay","method":"POST","path":"/one"}',
        result,
    )
    second = _record_repeated_api_failure(
        failures,
        "api_call",
        '{"integration_id":"relay","method":"POST","path":"/two"}',
        result,
    )
    third = _record_repeated_api_failure(
        failures,
        "api_call",
        '{"integration_id":"relay","method":"POST","path":"/three"}',
        result,
    )

    assert first is None
    assert second is None
    assert third == "api_call:relay:POST:HTTP 302"


def test_api_failure_bound_does_not_collapse_different_integrations_or_statuses():
    failures = collections.Counter()
    calls = [
        ('{"integration_id":"one","method":"GET","path":"/a"}', "HTTP 302\nMoved"),
        ('{"integration_id":"two","method":"GET","path":"/b"}', "HTTP 302\nMoved"),
        ('{"integration_id":"one","method":"GET","path":"/c"}', "HTTP 404\nMissing"),
    ]

    assert all(
        _record_repeated_api_failure(
            failures,
            "api_call",
            content,
            {"error": error, "exit_code": 1},
        ) is None
        for content, error in calls
    )
