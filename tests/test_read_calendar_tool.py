from __future__ import annotations

import json

import pytest

from src.tools import calendar


@pytest.mark.asyncio
async def test_read_calendar_syncs_then_delegates_owner_scoped_list(monkeypatch):
    calls = []

    async def sync(owner, direction):
        calls.append(("sync", owner, direction))
        return {"calendars": 1, "events": 2, "errors": []}

    async def read(content, owner=None):
        calls.append(("read", json.loads(content), owner))
        return {"response": "Found two events.", "events": [{"uid": "one"}], "exit_code": 0}

    monkeypatch.setattr("src.caldav_sync.sync_caldav_direction", sync)
    monkeypatch.setattr(calendar, "do_manage_calendar", read)

    result = await calendar.do_read_calendar(
        '{"action":"list_events","start":"2026-07-15","end":"2026-07-16"}',
        owner="leo",
    )

    assert calls == [
        ("sync", "leo", "pull"),
        ("read", {"action": "list_events", "start": "2026-07-15", "end": "2026-07-16"}, "leo"),
    ]
    assert result["calendar_freshness"] == "fresh"


@pytest.mark.asyncio
async def test_read_calendar_rejects_mutation_and_reports_sync_failure(monkeypatch):
    assert (await calendar.do_read_calendar('{"action":"create_event"}', owner="leo"))["error"] == (
        "read_calendar supports only list_events and list_calendars"
    )
    assert "Unsupported read_calendar fields" in (
        await calendar.do_read_calendar('{"action":"list_events","summary":"not allowed"}', owner="leo")
    )["error"]

    async def sync(_owner, _direction):
        return {"calendars": 0, "events": 0, "errors": ["calendar host unavailable"]}

    async def read(_content, owner=None):
        assert owner == "leo"
        return {"response": "Cached result.", "exit_code": 0}

    monkeypatch.setattr("src.caldav_sync.sync_caldav_direction", sync)
    monkeypatch.setattr(calendar, "do_manage_calendar", read)

    result = await calendar.do_read_calendar('{"action":"list_events"}', owner="leo")
    assert result["calendar_freshness"] == "sync_failed"
    assert result["sync_error_count"] == 1
    assert "calendar host unavailable" not in result["response"]
    assert result["response"].endswith("Cached result.")


@pytest.mark.asyncio
async def test_read_calendar_does_not_claim_freshness_without_a_connection(monkeypatch):
    async def sync(_owner, _direction):
        return {"calendars": 0, "events": 0, "errors": ["CalDAV is not configured"]}

    async def read(_content, owner=None):
        assert owner == "leo"
        return {"response": "No cached events.", "exit_code": 0}

    monkeypatch.setattr("src.caldav_sync.sync_caldav_direction", sync)
    monkeypatch.setattr(calendar, "do_manage_calendar", read)

    result = await calendar.do_read_calendar('{"action":"list_events"}', owner="leo")
    assert result["calendar_freshness"] == "sync_failed"
    assert result["sync_error_count"] == 1
    assert "could not be confirmed" in result["response"]
