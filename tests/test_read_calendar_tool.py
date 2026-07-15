from __future__ import annotations

import json

import pytest

from src.tools import calendar


OWNER = "user@example.test"


def test_read_calendar_is_registered_as_a_read_only_tool():
    from src.agent_loop import TOOL_SECTIONS
    from src.agent_tools import TOOL_TAGS
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
    from src.tool_security import NON_ADMIN_BLOCKED_TOOLS, PLAN_MODE_READONLY_TOOLS

    schema_names = {schema["function"]["name"] for schema in FUNCTION_TOOL_SCHEMAS}
    assert "read_calendar" in schema_names
    assert "read_calendar" in TOOL_TAGS
    assert "read_calendar" in TOOL_SECTIONS
    assert "read_calendar" in PLAN_MODE_READONLY_TOOLS
    assert "read_calendar" not in NON_ADMIN_BLOCKED_TOOLS


@pytest.mark.asyncio
async def test_read_calendar_syncs_before_owner_scoped_list(monkeypatch):
    calls = []

    async def sync(owner, direction):
        calls.append(("sync", owner, direction))
        return {"calendars": 1, "events": 2, "errors": []}

    async def read(content, owner=None):
        calls.append(("read", json.loads(content), owner))
        return {
            "response": "Found two events.",
            "events": [{"uid": "one", "calendar_href": "must-not-leak"}],
            "exit_code": 0,
        }

    monkeypatch.setattr("src.caldav_sync.sync_caldav_direction", sync)
    monkeypatch.setattr(calendar, "do_manage_calendar", read)

    result = await calendar.do_read_calendar(
        '{"action":"list_events","start":"2026-07-15","end":"2026-07-16"}',
        owner=OWNER,
    )

    assert calls == [
        ("sync", OWNER, "pull"),
        ("read", {"action": "list_events", "start": "2026-07-15", "end": "2026-07-16"}, OWNER),
    ]
    assert result["calendar_freshness"] == "fresh"
    assert "calendar_href" not in result["events"][0]


@pytest.mark.asyncio
async def test_read_calendar_lists_names_without_calendar_hrefs(monkeypatch):
    async def sync(_owner, _direction):
        return {"calendars": 2, "events": 0, "errors": []}

    async def read(_content, owner=None):
        assert owner == OWNER
        return {
            "response": "IDs must be replaced.",
            "calendars": [
                {"name": "Personal", "href": "must-not-leak-remote-ref"},
                {"name": "Work", "href": "opaque-id"},
            ],
            "exit_code": 0,
        }

    monkeypatch.setattr("src.caldav_sync.sync_caldav_direction", sync)
    monkeypatch.setattr(calendar, "do_manage_calendar", read)

    result = await calendar.do_read_calendar('{"action":"list_calendars"}', owner=OWNER)
    assert result["calendars"] == [{"name": "Personal"}, {"name": "Work"}]
    assert result["response"] == "Found 2 calendar(s):\n- Personal\n- Work"
    assert "must-not-leak" not in json.dumps(result)


@pytest.mark.asyncio
async def test_read_calendar_rejects_mutation_unknown_fields_and_missing_owner():
    assert (await calendar.do_read_calendar('{"action":"list_events"}'))["error"] == (
        "Calendar owner is required"
    )
    assert (await calendar.do_read_calendar('{"action":"create_event"}', owner=OWNER))["error"] == (
        "read_calendar supports only list_events and list_calendars"
    )
    assert "Unsupported read_calendar fields" in (
        await calendar.do_read_calendar(
            '{"action":"list_events","summary":"not allowed"}',
            owner=OWNER,
        )
    )["error"]


@pytest.mark.asyncio
async def test_read_calendar_reports_sync_failure_without_leaking_details(monkeypatch):
    async def sync(_owner, _direction):
        return {"calendars": 0, "events": 0, "errors": ["private host unavailable"]}

    async def read(_content, owner=None):
        assert owner == OWNER
        return {"response": "Cached result.", "exit_code": 0}

    monkeypatch.setattr("src.caldav_sync.sync_caldav_direction", sync)
    monkeypatch.setattr(calendar, "do_manage_calendar", read)

    result = await calendar.do_read_calendar('{"action":"list_events"}', owner=OWNER)
    assert result["calendar_freshness"] == "sync_failed"
    assert result["sync_error_count"] == 1
    assert "private host unavailable" not in result["response"]
    assert "freshness could not be confirmed" in result["response"]
    assert result["response"].endswith("Cached result.")


@pytest.mark.asyncio
async def test_read_calendar_does_not_claim_freshness_without_caldav(monkeypatch):
    async def sync(_owner, _direction):
        return {"calendars": 0, "events": 0, "errors": ["CalDAV is not configured"]}

    async def read(_content, owner=None):
        assert owner == OWNER
        return {"response": "No cached events.", "exit_code": 0}

    monkeypatch.setattr("src.caldav_sync.sync_caldav_direction", sync)
    monkeypatch.setattr(calendar, "do_manage_calendar", read)

    result = await calendar.do_read_calendar('{"action":"list_events"}', owner=OWNER)
    assert result["calendar_freshness"] == "sync_failed"
    assert result["sync_error_count"] == 1
