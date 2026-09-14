"""Deterministic in-process mock services for offline benchmark runs.

The mock world is a small, fixed service surface that mirrors the real tool
categories the benchmark exercises (capability inventory, files, books,
calendar, integrations, ORACLE, notes, email, documents).  It never touches the
network, the database, or the filesystem.

A mock outcome is one of:
  * ok=True             - the tool answered with usable evidence
  * ok=False            - the tool failed (missing file, unavailable install)
  * ok=False, gated=True - the tool requires operator approval before it runs
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

MOCK_TOOL_NAMES = {
    "manage_mcp",
    "manage_settings",
    "read_file",
    "ls",
    "grep",
    "manage_books",
    "read_calendar",
    "manage_calendar",
    "api_call",
    "ui_control",
    "oracle_read",
    "oracle_control",
    "manage_notes",
    "list_emails",
    "bulk_email",
    "create_document",
    "bash",
    "python",
    "app_api",
}

_GATED_ACTIONS = {
    "manage_calendar": {"create", "update", "delete"},
    "oracle_control": {"*"},
    "bulk_email": {"*"},
}

_DEFAULT_FILES = {
    "notes.txt": "Release checklists\nMigration plan for the new storage node\nCall the vet on Friday\n",
    "q3-report.csv": "quarter,revenue\nQ1,1200\nQ2,1500\nQ3,1842\n",
}

_DEFAULT_BOOKS = [
    {
        "title": "Meditations",
        "page": 42,
        "snippet": "The happiness of your life depends upon the quality of your thoughts.",
    }
]

_DEFAULT_EVENTS = [
    {"summary": "Design review", "start": "2026-09-14T15:00:00", "end": "2026-09-14T16:00:00"},
    {"summary": "Dentist", "start": "2026-09-15T09:30:00", "end": "2026-09-15T10:00:00"},
]


@dataclass
class MockOutcome:
    result: Dict[str, Any]
    ok: bool
    gated: bool = False


@dataclass
class MockWorld:
    """A per-scenario mock service world."""

    unavailable: set = field(default_factory=set)
    failing: Dict[str, str] = field(default_factory=dict)
    flaky: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    files: Dict[str, str] = field(default_factory=lambda: copy.deepcopy(_DEFAULT_FILES))
    books: List[Dict[str, Any]] = field(default_factory=lambda: copy.deepcopy(_DEFAULT_BOOKS))
    events: List[Dict[str, Any]] = field(default_factory=lambda: copy.deepcopy(_DEFAULT_EVENTS))
    notes: List[Dict[str, Any]] = field(default_factory=list)
    calls: List[Dict[str, Any]] = field(default_factory=list)
    _flaky_counts: Dict[str, int] = field(default_factory=dict, repr=False)

    def dispatch(self, name: str, args: Dict[str, Any]) -> MockOutcome:
        call = {"name": name, "args": args}
        self.calls.append(call)
        if name in self.unavailable:
            return MockOutcome(
                result={
                    "success": False,
                    "available": False,
                    "error": f"{name} is not available in this installation",
                },
                ok=False,
            )
        flaky = self.flaky.get(name)
        if flaky:
            used = self._flaky_counts.get(name, 0)
            if used < int(flaky.get("fail_times", 0)):
                self._flaky_counts[name] = used + 1
                return MockOutcome(
                    result={"success": False, "error": str(flaky.get("error") or f"{name} failed")},
                    ok=False,
                )
        if name in self.failing:
            return MockOutcome(
                result={"success": False, "error": self.failing[name]},
                ok=False,
            )
        if self._is_gated(name, args):
            return MockOutcome(
                result={
                    "success": False,
                    "action": "approval_required",
                    "pending_action": f"pending-{name}",
                    "message": "Operator approval is required before this mutation runs.",
                },
                ok=False,
                gated=True,
            )
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            return MockOutcome(
                result={"success": False, "available": False, "error": f"unknown tool: {name}"},
                ok=False,
            )
        try:
            return handler(args)
        except Exception as exc:  # deterministic failure, never a real crash
            return MockOutcome(result={"success": False, "error": str(exc)}, ok=False)

    @staticmethod
    def _is_gated(name: str, args: Dict[str, Any]) -> bool:
        if str(args.get("approval") or "").strip():
            return False
        actions = _GATED_ACTIONS.get(name)
        if actions is None:
            return False
        if "*" in actions:
            return True
        action = str(args.get("action") or "").lower()
        return action in {str(a).lower() for a in actions}

    # -- handlers ---------------------------------------------------------

    def _tool_manage_mcp(self, args: Dict[str, Any]) -> MockOutcome:
        if str(args.get("action") or "") != "inventory":
            return MockOutcome(result={"success": False, "error": "unsupported action"}, ok=False)
        return MockOutcome(
            result={
                "success": True,
                "integrations": [
                    {"id": "home_assistant", "label": "Home Assistant", "status": "configured"},
                    {"id": "oracle", "label": "ORACLE", "status": "installed"},
                ],
                "total": 2,
            },
            ok=True,
        )

    def _tool_manage_settings(self, args: Dict[str, Any]) -> MockOutcome:
        if str(args.get("action") or "") != "list_tools":
            return MockOutcome(result={"success": False, "error": "unsupported action"}, ok=False)
        return MockOutcome(
            result={
                "success": True,
                "total": 82,
                "enabled_count": 81,
                "tools": [{"name": "read_file"}, {"name": "manage_books"}, {"name": "api_call"}],
            },
            ok=True,
        )

    def _tool_read_file(self, args: Dict[str, Any]) -> MockOutcome:
        path = str(args.get("path") or args.get("file") or "")
        key = path.rsplit("/", 1)[-1]
        if key not in self.files:
            return MockOutcome(result={"success": False, "error": f"file not found: {path}"}, ok=False)
        content = self.files[key]
        return MockOutcome(result={"content": content, "size": len(content), "path": key}, ok=True)

    def _tool_ls(self, args: Dict[str, Any]) -> MockOutcome:
        return MockOutcome(result={"output": "\n".join(sorted(self.files)), "exit_code": 0}, ok=True)

    def _tool_grep(self, args: Dict[str, Any]) -> MockOutcome:
        pattern = str(args.get("pattern") or "")
        matches = []
        for key, content in self.files.items():
            for line in content.splitlines():
                if re.search(pattern, line, re.I):
                    matches.append(f"{key}:{line}")
        return MockOutcome(result={"results": matches, "count": len(matches)}, ok=True)

    def _tool_manage_books(self, args: Dict[str, Any]) -> MockOutcome:
        action = str(args.get("action") or "")
        if action == "search":
            return MockOutcome(
                result={"results": copy.deepcopy(self.books), "count": len(self.books)}, ok=True
            )
        if action == "list":
            return MockOutcome(
                result={
                    "books": [{"title": book["title"]} for book in self.books],
                    "count": len(self.books),
                },
                ok=True,
            )
        return MockOutcome(result={"success": False, "error": "unsupported action"}, ok=False)

    def _tool_read_calendar(self, args: Dict[str, Any]) -> MockOutcome:
        return MockOutcome(
            result={"events": copy.deepcopy(self.events), "count": len(self.events)}, ok=True
        )

    def _tool_manage_calendar(self, args: Dict[str, Any]) -> MockOutcome:
        action = str(args.get("action") or "")
        if action == "list_calendars":
            return MockOutcome(result={"calendars": ["Personal"], "count": 1}, ok=True)
        if action in {"create", "update", "delete"}:
            return MockOutcome(
                result={"success": True, "action": action, "event_uid": f"event-{action}"}, ok=True
            )
        return MockOutcome(result={"success": False, "error": "unsupported action"}, ok=False)

    def _tool_api_call(self, args: Dict[str, Any]) -> MockOutcome:
        integration = str(args.get("integration") or args.get("service") or "")
        if integration != "home_assistant":
            return MockOutcome(result={"success": False, "error": "unknown integration"}, ok=False)
        return MockOutcome(
            result={"connected": True, "state": "running", "version": "2026.9.1"}, ok=True
        )

    def _tool_ui_control(self, args: Dict[str, Any]) -> MockOutcome:
        action = str(args.get("action") or "")
        if action != "oracle_protocol":
            return MockOutcome(result={"success": False, "error": "unsupported action"}, ok=False)
        name = str(args.get("name") or args.get("value") or "").lower()
        if name not in {"engage", "shutdown"}:
            return MockOutcome(result={"success": False, "error": "unsupported oracle action"}, ok=False)
        return MockOutcome(result={"success": True, "oracle": name}, ok=True)

    def _tool_oracle_read(self, args: Dict[str, Any]) -> MockOutcome:
        return MockOutcome(
            result={"layer": "traffic", "visible_countries": 176, "source": "oracle-native"},
            ok=True,
        )

    def _tool_oracle_control(self, args: Dict[str, Any]) -> MockOutcome:
        return MockOutcome(
            result={"success": True, "layer": str(args.get("layer") or "cctv"), "visible": True},
            ok=True,
        )

    def _tool_manage_notes(self, args: Dict[str, Any]) -> MockOutcome:
        action = str(args.get("action") or "")
        if action != "create":
            return MockOutcome(result={"success": False, "error": "unsupported action"}, ok=False)
        note = {
            "note_id": f"note-{len(self.notes) + 1}",
            "title": str(args.get("title") or args.get("text") or "Untitled"),
        }
        self.notes.append(note)
        return MockOutcome(result={"success": True, **note, "count": len(self.notes)}, ok=True)

    def _tool_list_emails(self, args: Dict[str, Any]) -> MockOutcome:
        return MockOutcome(
            result={
                "emails": [
                    {"UID": "90186", "subject": "Weekly digest", "from": "news@example.com"},
                    {"UID": "90187", "subject": "Sale ends tonight", "from": "news@example.com"},
                ],
                "count": 2,
            },
            ok=True,
        )

    def _tool_bulk_email(self, args: Dict[str, Any]) -> MockOutcome:
        uids = args.get("uids") or []
        return MockOutcome(
            result={"success": True, "action": "delete", "affected": len(uids or [1])}, ok=True
        )

    def _tool_create_document(self, args: Dict[str, Any]) -> MockOutcome:
        content = str(args.get("content") or args.get("markdown") or "")
        if len(content) < 200:
            return MockOutcome(
                result={"success": False, "error": "document content is too short to be long-form"},
                ok=False,
            )
        return MockOutcome(
            result={"doc_id": "doc-1", "version": 1, "title": str(args.get("title") or "Runbook"), "content": content},
            ok=True,
        )

    def _tool_bash(self, args: Dict[str, Any]) -> MockOutcome:
        return MockOutcome(result={"output": "mock shell has no real command execution", "exit_code": 0}, ok=True)

    def _tool_python(self, args: Dict[str, Any]) -> MockOutcome:
        return MockOutcome(result={"output": "mock python has no real execution", "exit_code": 0}, ok=True)

    def _tool_app_api(self, args: Dict[str, Any]) -> MockOutcome:
        return MockOutcome(result={"success": True, "result": "mock api response"}, ok=True)


def world_for_scenario(scenario) -> MockWorld:
    """Build the deterministic world described by one scenario."""
    state = dict(scenario.mock_state or {})
    world = MockWorld(
        unavailable=set(scenario.unavailable),
        failing=dict(scenario.failing),
    )
    if isinstance(state.get("files"), dict):
        world.files.update({str(k): str(v) for k, v in state["files"].items()})
    if isinstance(state.get("books"), list):
        world.books = [dict(book) for book in state["books"]]
    if isinstance(state.get("events"), list):
        world.events = [dict(event) for event in state["events"]]
    if isinstance(state.get("flaky"), dict):
        world.flaky = {
            str(name): dict(config)
            for name, config in state["flaky"].items()
            if isinstance(config, dict)
        }
    return world