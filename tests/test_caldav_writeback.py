"""Issue #800 — CalDAV write-back pushes local changes to the remote server.

Unit-tests the pure pieces against a fake caldav calendar (no network): the
iCalendar serialization, hash-based remote-calendar discovery, and the
create/update/delete orchestration.
"""

import asyncio
import sys
import types
from datetime import datetime

from icalendar import Calendar

from src.caldav_writeback import (
    build_event_ical,
    find_remote_calendar,
    push_event,
    _stable_cal_id,
)

REMOTE_URL = "https://p69-caldav.icloud.com/123/calendars/home/"
CAL_ID = _stable_cal_id(REMOTE_URL)
BASIC_REMOTE_ICAL = "\r\n".join((
    "BEGIN:VCALENDAR",
    "VERSION:2.0",
    "PRODID:-//Remote//EN",
    "BEGIN:VEVENT",
    "UID:evt-1",
    "SUMMARY:Old",
    "DTSTART:20260610T140000Z",
    "DTEND:20260610T150000Z",
    "END:VEVENT",
    "END:VCALENDAR",
    "",
))


def _patch_credentials(monkeypatch):
    import src.caldav_credentials as credentials

    store = {}

    def set_credentials(owner, account_id, **updates):
        from src.secret_storage import decrypt

        for purpose, value in updates.items():
            key = (owner, account_id, purpose)
            if value:
                store[key] = decrypt(value)
            else:
                store.pop(key, None)

    monkeypatch.setattr(credentials, "set_credentials", set_credentials)
    monkeypatch.setattr(
        credentials,
        "get_secret",
        lambda owner, account_id, purpose: store.get((owner, account_id, purpose), ""),
    )
    monkeypatch.setattr(credentials, "retain_accounts", lambda owner, ids: None)


class FakeEvent:
    def __init__(self, url="https://p69-caldav.icloud.com/123/calendars/home/evt-1.ics", data=None):
        self.url = url
        self.etag = '"abc123"'
        self.data = BASIC_REMOTE_ICAL if data is None else data
        self.saved = False
        self.deleted = False

    def save(self):
        self.saved = True

    def delete(self):
        self.deleted = True


class FakeCalendar:
    def __init__(self, url, existing=None):
        self.url = url
        self._existing = existing
        self.saved_ical = None
        self.created = FakeEvent(str(url).rstrip("/") + "/created.ics")

    def event_by_uid(self, uid):
        if self._existing is None:
            raise Exception("not found")
        return self._existing

    def save_event(self, ical):
        self.saved_ical = ical
        return self.created


def _ev(**over):
    base = dict(
        uid="evt-1", summary="Dentist", description="bring x-rays",
        location="Clinic", dtstart=datetime(2026, 6, 10, 14, 0),
        dtend=datetime(2026, 6, 10, 15, 0), all_day=False, is_utc=True, rrule="",
    )
    base.update(over)
    return base


def test_build_ical_timed_event_has_core_fields():
    ical = build_event_ical(_ev())
    assert "BEGIN:VEVENT" in ical and "END:VEVENT" in ical
    assert "UID:evt-1" in ical
    assert "SUMMARY:Dentist" in ical
    # is_utc -> UTC instant (Z suffix)
    assert "DTSTART:20260610T140000Z" in ical
    assert "DTEND:20260610T150000Z" in ical


def test_build_ical_all_day_uses_date_values():
    ical = build_event_ical(_ev(all_day=True, is_utc=False))
    assert "DTSTART;VALUE=DATE:20260610" in ical


def test_build_ical_includes_rrule():
    ical = build_event_ical(_ev(rrule="FREQ=WEEKLY;BYDAY=MO"))
    assert "RRULE:FREQ=WEEKLY" in ical


def test_build_ical_includes_recurrence_exdates():
    ical = build_event_ical(_ev(recurrence_exdates=["2026-06-17T14:00"]))
    assert "EXDATE:20260617T140000Z" in ical


def test_find_remote_calendar_matches_by_hash():
    cals = [FakeCalendar("https://other/x/"), FakeCalendar(REMOTE_URL)]
    found = find_remote_calendar(cals, CAL_ID)
    assert found is cals[1]
    assert find_remote_calendar([FakeCalendar("https://nope/")], CAL_ID) is None


def test_push_create_calls_save_event():
    cal = FakeCalendar(REMOTE_URL, existing=None)  # event_by_uid raises -> create
    res = push_event([cal], CAL_ID, _ev(), delete=False)
    assert res["ok"] and res.get("created")
    assert cal.saved_ical and "UID:evt-1" in cal.saved_ical
    assert res["calendar_url"] == REMOTE_URL
    assert res["remote_href"].endswith("/created.ics")


def test_push_update_replaces_modeled_fields():
    existing = FakeEvent()
    cal = FakeCalendar(REMOTE_URL, existing=existing)
    res = push_event([cal], CAL_ID, _ev(summary="Moved"), delete=False)
    assert res["ok"] and res.get("updated")
    assert existing.saved and "SUMMARY:Moved" in existing.data
    assert cal.saved_ical is None  # used update path, not create
    assert res["remote_href"].endswith("evt-1.ics")
    assert res["remote_etag"] == '"abc123"'


def test_push_update_preserves_remote_meeting_fields_alarms_and_overrides():
    remote_ical = "\r\n".join((
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Remote Provider//EN",
        "CALSCALE:GREGORIAN",
        "BEGIN:VEVENT",
        "UID:evt-1",
        "SUMMARY:Remote title",
        "DESCRIPTION:Remote description",
        "LOCATION:Remote room",
        "DTSTART:20260610T140000Z",
        "DTEND:20260610T150000Z",
        "RRULE:FREQ=WEEKLY;COUNT=4",
        "EXDATE:20260617T140000Z",
        "ORGANIZER;CN=Host:mailto:host@example.com",
        "ATTENDEE;CN=Leo;PARTSTAT=ACCEPTED:mailto:leo@example.com",
        "CONFERENCE;VALUE=URI:https://meet.example.com/room",
        "X-GOOGLE-CONFERENCE:https://meet.google.com/abc-defg-hij",
        "STATUS:CONFIRMED",
        "TRANSP:OPAQUE",
        "BEGIN:VALARM",
        "ACTION:DISPLAY",
        "TRIGGER:-PT15M",
        "DESCRIPTION:Meeting reminder",
        "END:VALARM",
        "END:VEVENT",
        "BEGIN:VEVENT",
        "UID:evt-1",
        "RECURRENCE-ID:20260624T140000Z",
        "SUMMARY:Remote moved occurrence",
        "DTSTART:20260624T160000Z",
        "DTEND:20260624T170000Z",
        "END:VEVENT",
        "BEGIN:VTODO",
        "UID:remote-task",
        "SUMMARY:Provider task",
        "END:VTODO",
        "END:VCALENDAR",
        "",
    ))
    existing = FakeEvent(data=remote_ical)
    result = push_event(
        [FakeCalendar(REMOTE_URL, existing=existing)],
        CAL_ID,
        _ev(
            summary="Pandamonium title",
            rrule="FREQ=WEEKLY;COUNT=2",
            recurrence_exdates=["2026-07-01T14:00"],
        ),
    )

    assert result["ok"] and existing.saved
    parsed = Calendar.from_ical(existing.data)
    master = next(event for event in parsed.walk("VEVENT") if "RECURRENCE-ID" not in event)
    override = next(event for event in parsed.walk("VEVENT") if "RECURRENCE-ID" in event)
    assert str(master["SUMMARY"]) == "Pandamonium title"
    assert str(master["ORGANIZER"]) == "mailto:host@example.com"
    assert master["ORGANIZER"].params["CN"] == "Host"
    assert str(master["ATTENDEE"]) == "mailto:leo@example.com"
    assert master["ATTENDEE"].params["PARTSTAT"] == "ACCEPTED"
    assert str(master["CONFERENCE"]) == "https://meet.example.com/room"
    assert str(master["X-GOOGLE-CONFERENCE"]) == "https://meet.google.com/abc-defg-hij"
    assert str(master["STATUS"]) == "CONFIRMED"
    assert str(master["TRANSP"]) == "OPAQUE"
    alarms = [component for component in master.subcomponents if component.name == "VALARM"]
    assert len(alarms) == 1 and str(alarms[0]["DESCRIPTION"]) == "Meeting reminder"
    assert str(override["SUMMARY"]) == "Remote moved occurrence"
    assert len(parsed.walk("VTODO")) == 1
    assert "EXDATE:20260701T140000Z" in existing.data
    assert "EXDATE:20260617T140000Z" not in existing.data


def test_push_update_refuses_unparseable_remote_data_without_saving():
    existing = FakeEvent(data="not iCalendar")
    result = push_event([FakeCalendar(REMOTE_URL, existing=existing)], CAL_ID, _ev(summary="Moved"))

    assert result == {
        "ok": False,
        "error": "remote event could not be parsed safely; update was not applied",
        "calendar_url": REMOTE_URL,
    }
    assert existing.data == "not iCalendar"
    assert existing.saved is False


def test_push_delete_removes_existing():
    existing = FakeEvent()
    cal = FakeCalendar(REMOTE_URL, existing=existing)
    res = push_event([cal], CAL_ID, _ev(), delete=True)
    assert res["ok"] and existing.deleted


def test_push_delete_absent_is_ok():
    cal = FakeCalendar(REMOTE_URL, existing=None)
    res = push_event([cal], CAL_ID, _ev(), delete=True)
    assert res["ok"] and "absent" in res.get("note", "")


def test_push_unknown_calendar_reports_not_found():
    cal = FakeCalendar("https://different/")
    res = push_event([cal], CAL_ID, _ev())
    assert res["ok"] is False and "not found" in res["error"]


def test_push_missing_uid_reports_input_error_before_remote_lookup():
    cal = FakeCalendar(REMOTE_URL, existing=FakeEvent())
    res = push_event([cal], CAL_ID, _ev(uid=""))
    assert res["ok"] is False and "uid" in res["error"]
    assert cal._existing.saved is False


def test_writeback_validates_saved_url_before_remote_call(monkeypatch):
    _patch_credentials(monkeypatch)
    import src.caldav_sync as sync
    import src.caldav_writeback as wb

    prefs_mod = types.ModuleType("routes.prefs_routes")
    prefs_mod._load_for_user = lambda owner: {
        "caldav": {
            "url": " https://dav.example.com/calendars/home/ ",
            "username": owner,
            "password": "enc:pw",
        }
    }
    secret_mod = types.ModuleType("src.secret_storage")
    secret_mod.decrypt = lambda value: "plain-password"
    monkeypatch.setitem(sys.modules, "routes.prefs_routes", prefs_mod)
    monkeypatch.setitem(sys.modules, "src.secret_storage", secret_mod)

    captured = {}

    def fake_validate(url):
        captured["validated_url"] = url
        return "https://dav.example.com/calendars/home"

    def fake_writeback_blocking(local_cal_id, ev, delete, url, username, password,
                                owner="", account_id=""):
        captured.update(
            {
                "local_cal_id": local_cal_id,
                "delete": delete,
                "url": url,
                "username": username,
                "password": password,
            }
        )
        return {"ok": True}

    async def inline_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(sync, "validate_caldav_url", fake_validate)
    monkeypatch.setattr(wb, "_writeback_blocking", fake_writeback_blocking)
    monkeypatch.setattr(wb.asyncio, "to_thread", inline_to_thread)

    result = asyncio.run(
        wb.writeback_event("alice", "caldav", "caldav-123", {"uid": "evt-1"})
    )

    assert result == {"ok": True}
    assert captured == {
        "validated_url": "https://dav.example.com/calendars/home/",
        "local_cal_id": "caldav-123",
        "delete": False,
        "url": "https://dav.example.com/calendars/home",
        "username": "alice",
        "password": "plain-password",
    }


def test_writeback_rejects_unsafe_saved_url_before_remote_call(monkeypatch):
    _patch_credentials(monkeypatch)
    import src.caldav_sync as sync
    import src.caldav_writeback as wb

    prefs_mod = types.ModuleType("routes.prefs_routes")
    prefs_mod._load_for_user = lambda owner: {
        "caldav": {
            "url": "http://evil.example/latest/meta-data",
            "username": owner,
            "password": "enc:pw",
        }
    }
    secret_mod = types.ModuleType("src.secret_storage")
    secret_mod.decrypt = lambda value: "plain-password"
    monkeypatch.setitem(sys.modules, "routes.prefs_routes", prefs_mod)
    monkeypatch.setitem(sys.modules, "src.secret_storage", secret_mod)

    called = False

    def fake_validate(_url):
        raise ValueError("CalDAV URL host is not allowed")

    def fake_writeback_blocking(local_cal_id, ev, delete, url, username, password,
                                owner="", account_id=""):
        nonlocal called
        called = True
        return {"ok": True}

    async def inline_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(sync, "validate_caldav_url", fake_validate)
    monkeypatch.setattr(wb, "_writeback_blocking", fake_writeback_blocking)
    monkeypatch.setattr(wb.asyncio, "to_thread", inline_to_thread)

    result = asyncio.run(
        wb.writeback_event("alice", "caldav", "caldav-123", {"uid": "evt-1"})
    )

    assert result == {"ok": False, "error": "CalDAV URL host is not allowed"}
    assert called is False
