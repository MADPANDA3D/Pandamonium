from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import routes.agent_task_routes as agent_task_routes
import src.agent_worker_adapters as agent_worker_adapters
import src.jarvis_agent as jarvis_agent
import src.tool_execution as tool_execution
from src.agent_worker_adapters import (
    CodexBridgeAdapter,
    HermesRunsAdapter,
    _last_remote_event_id,
)
from src.agent_tools import ToolBlock


def _route_endpoint(path: str, method: str):
    router = agent_task_routes.setup_agent_task_routes(SimpleNamespace())
    return next(
        route.endpoint
        for route in router.routes
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set())
    )


def _task(**updates):
    task = {
        "task_id": "task-1",
        "remote_task_id": "remote-1",
        "worker": "pc-codex",
        "session_id": "session-1",
        "workspace": "home-lab",
        "permission_mode": "read_only",
        "approved": False,
        "status": "running",
        "owner": "alice",
        "events": [],
        "artifacts": [],
    }
    task.update(updates)
    return task


def test_remote_resume_cursor_uses_last_stable_worker_event_id():
    task = _task(events=[
        {"type": "accepted", "event_id": "broker-accepted"},
        {"type": "progress", "event_id": "remote-1"},
        {"type": "result", "event_id": "reconciled", "metadata": {"reconciled": True}},
    ])

    assert _last_remote_event_id(task) == "remote-1"


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_kind", ["codex", "hermes"])
async def test_worker_adapters_reject_write_or_preapproved_tasks_before_network(tmp_path, adapter_kind):
    adapter = (
        CodexBridgeAdapter("pc-codex", "http://worker.test", tmp_path / "token", enabled=True, machine="test")
        if adapter_kind == "codex"
        else HermesRunsAdapter("http://worker.test", tmp_path / "token", enabled=True)
    )
    for updates in (
        {"permission_mode": "workspace_write", "approved": False},
        {"permission_mode": "read_only", "approved": True},
    ):
        with pytest.raises(PermissionError, match="public_tasks_read_only"):
            await adapter.start(_task(prompt="inspect", **updates))


@pytest.mark.asyncio
async def test_codex_stream_reconnect_sends_last_stable_event_id(tmp_path, monkeypatch):
    captured = {}

    class Response:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def raise_for_status(self):
            return None

        async def aiter_lines(self):
            yield 'data: {"event_id":"remote-2","type":"result","text":"Done."}'

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, method, url, params, headers):
            captured.update(method=method, url=url, params=params, headers=headers)
            return Response()

    token = tmp_path / "token"
    token.write_text("secret", encoding="utf-8")
    adapter = CodexBridgeAdapter("pc-codex", "http://worker.test", token, enabled=True, machine="test")
    monkeypatch.setattr(agent_worker_adapters.httpx, "AsyncClient", Client)
    task = _task(
        remote_task_id="remote-task",
        events=[
            {"type": "accepted", "event_id": "broker-accepted"},
            {"type": "progress", "event_id": "remote-1"},
        ],
    )

    events = [event async for event in adapter.events(task)]

    assert events[0]["event_id"] == "remote-2"
    assert captured["params"] == {"after": -1}
    assert captured["headers"]["Last-Event-ID"] == "remote-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "permission_mode,approved",
    [("workspace_write", False), ("read_only", True)],
)
async def test_public_task_api_rejects_write_or_preapproval(permission_mode, approved):
    endpoint = _route_endpoint("/api/agent-tasks", "POST")
    payload = agent_task_routes.TaskCreate(
        worker="pc-codex",
        session_id="session-1",
        workspace="home-lab",
        prompt="inspect only",
        permission_mode=permission_mode,
        approved=approved,
    )

    with pytest.raises(HTTPException) as exc:
        await endpoint(payload, SimpleNamespace(), owner="alice")

    assert exc.value.status_code == 403
    assert exc.value.detail == "Public worker tasks are read-only"


def test_task_owner_helper_fails_closed_for_missing_or_wrong_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(jarvis_agent, "TASKS_FILE", tmp_path / "agent_tasks.json")
    jarvis_agent._save_task(_task())

    assert jarvis_agent.require_task_owner("task-1", "alice")["task_id"] == "task-1"
    with pytest.raises(PermissionError, match="task_owner_mismatch"):
        jarvis_agent.require_task_owner("task-1", "bob")
    with pytest.raises(PermissionError, match="owner_required"):
        jarvis_agent.require_task_owner("task-1", None)
    with pytest.raises(KeyError):
        jarvis_agent.require_task_owner("missing", "alice")


def test_linked_session_owner_helper_fails_closed(monkeypatch):
    manager = SimpleNamespace(
        get_session=lambda session_id: SimpleNamespace(id=session_id, owner="alice")
    )
    monkeypatch.setattr(jarvis_agent, "_SESSION_MANAGER", manager)

    assert jarvis_agent.require_session_owner("session-1", "alice").owner == "alice"
    with pytest.raises(PermissionError, match="session_owner_mismatch"):
        jarvis_agent.require_session_owner("session-1", "bob")
    monkeypatch.setattr(jarvis_agent, "_SESSION_MANAGER", None)
    with pytest.raises(RuntimeError, match="session_manager_unavailable"):
        jarvis_agent.require_session_owner("session-1", "alice")


@pytest.mark.asyncio
async def test_start_task_rejects_unknown_workspace_before_worker_call(monkeypatch):
    class Adapter:
        enabled = True

        async def start(self, _task):
            raise AssertionError("worker must not be called")

    manager = SimpleNamespace(
        get_session=lambda session_id: SimpleNamespace(id=session_id, owner="alice")
    )
    monkeypatch.setattr(jarvis_agent, "_SESSION_MANAGER", manager)
    monkeypatch.setattr(jarvis_agent, "adapters", lambda: {"pc-codex": Adapter()})
    monkeypatch.setattr(
        jarvis_agent,
        "worker_catalog",
        lambda: {"pc-codex": {"workspaces": ["home-lab"]}},
    )

    with pytest.raises(ValueError, match="unknown_workspace"):
        await jarvis_agent.start_task(
            "pc-codex", "session-1", "other", "inspect", owner="alice"
        )


@pytest.mark.asyncio
async def test_refresh_and_actions_enforce_owner_before_worker_call(tmp_path, monkeypatch):
    class Adapter:
        calls = 0

        async def status(self, _task):
            self.calls += 1
            return {"status": "running"}

        async def cancel(self, _task):
            self.calls += 1
            return {}

    adapter = Adapter()
    monkeypatch.setattr(jarvis_agent, "TASKS_FILE", tmp_path / "agent_tasks.json")
    monkeypatch.setattr(jarvis_agent, "adapters", lambda: {"pc-codex": adapter})
    jarvis_agent._save_task(_task())

    with pytest.raises(PermissionError, match="task_owner_mismatch"):
        await jarvis_agent.refresh_task("task-1", owner="bob")
    with pytest.raises(PermissionError, match="task_owner_mismatch"):
        await jarvis_agent.task_action("task-1", "cancel", owner="bob")

    assert adapter.calls == 0


@pytest.mark.asyncio
async def test_read_only_task_approval_can_only_be_denied(tmp_path, monkeypatch):
    class Adapter:
        def __init__(self):
            self.choices = []

        async def approve(self, _task, payload):
            self.choices.append(payload["choice"])
            return {}

        async def status(self, _task):
            return {"status": "running"}

    adapter = Adapter()
    monkeypatch.setattr(jarvis_agent, "TASKS_FILE", tmp_path / "agent_tasks.json")
    monkeypatch.setattr(jarvis_agent, "adapters", lambda: {"pc-codex": adapter})
    monkeypatch.setattr(jarvis_agent, "ensure_mirror", lambda _task_id: None)
    jarvis_agent._save_task(_task(status="waiting_approval"))

    with pytest.raises(PermissionError, match="read_only_task_approval_must_deny"):
        await jarvis_agent.task_action(
            "task-1",
            "approval",
            {"choice": "once"},
            owner="alice",
        )

    await jarvis_agent.task_action(
        "task-1",
        "approval",
        {"choice": "deny"},
        owner="alice",
    )
    assert adapter.choices == ["deny"]


def test_event_ids_are_normalized_and_first_terminal_event_wins(tmp_path, monkeypatch):
    monkeypatch.setattr(jarvis_agent, "TASKS_FILE", tmp_path / "agent_tasks.json")
    monkeypatch.setattr(jarvis_agent, "_SESSION_MANAGER", None)
    jarvis_agent._save_task(_task())

    jarvis_agent._append_event("task-1", {"event_id": 7, "type": "progress", "text": "Working."})
    jarvis_agent._append_event("task-1", {"event_id": "7", "type": "progress", "text": "Replay."})
    jarvis_agent._append_event("task-1", {"event_id": "result-1", "type": "result", "text": "Live result."})
    jarvis_agent._append_event("task-1", {"event_id": "error-1", "type": "error", "text": "Late error."})

    saved = jarvis_agent.get_task("task-1")
    assert [event["event_id"] for event in saved["events"]] == ["7", "result-1"]
    assert saved["status"] == "completed"
    assert saved["result"] == "Live result."
    assert saved.get("error") is None


@pytest.mark.asyncio
async def test_refresh_loses_race_to_live_terminal_without_duplicate(tmp_path, monkeypatch):
    class Adapter:
        async def status(self, _task):
            return {"status": "completed", "result": "Reconciled result."}

    async def live_result_wins(_task, event):
        jarvis_agent._append_event(
            "task-1",
            {"event_id": "live-result", "type": "result", "text": "Live result."},
        )
        return event

    monkeypatch.setattr(jarvis_agent, "TASKS_FILE", tmp_path / "agent_tasks.json")
    monkeypatch.setattr(jarvis_agent, "_SESSION_MANAGER", None)
    monkeypatch.setattr(jarvis_agent, "adapters", lambda: {"pc-codex": Adapter()})
    monkeypatch.setattr(jarvis_agent, "_enrich_worker_event", live_result_wins)
    jarvis_agent._save_task(_task())

    saved = await jarvis_agent.refresh_task("task-1", owner="alice")

    assert saved["status"] == "completed"
    assert saved["result"] == "Live result."
    assert [(event["event_id"], event["type"]) for event in saved["events"]] == [
        ("live-result", "result"),
    ]


@pytest.mark.asyncio
async def test_worker_stream_retries_twice_and_dedupes_replay(tmp_path, monkeypatch):
    class Adapter:
        def __init__(self):
            self.calls = 0

        async def events(self, _task):
            self.calls += 1
            yield {"event_id": "progress-1", "type": "progress", "text": "Working."}
            if self.calls < 3:
                raise RuntimeError("stream interrupted")
            yield {"event_id": "result-1", "type": "result", "text": "Done."}

    async def passthrough(_task, event):
        return event

    adapter = Adapter()
    monkeypatch.setattr(jarvis_agent, "TASKS_FILE", tmp_path / "agent_tasks.json")
    monkeypatch.setattr(jarvis_agent, "_SESSION_MANAGER", None)
    monkeypatch.setattr(jarvis_agent, "_MIRRORS", {})
    monkeypatch.setattr(jarvis_agent, "adapters", lambda: {"pc-codex": adapter})
    monkeypatch.setattr(jarvis_agent, "_enrich_worker_event", passthrough)
    jarvis_agent._save_task(_task())

    await jarvis_agent._mirror("task-1")

    saved = jarvis_agent.get_task("task-1")
    assert adapter.calls == 3
    assert [event["event_id"] for event in saved["events"]] == ["progress-1", "result-1"]
    assert saved["status"] == "completed"


@pytest.mark.asyncio
async def test_model_task_tools_forward_owner_and_reject_missing_identity(monkeypatch):
    captured = {}

    async def start_task(**kwargs):
        captured["start_owner"] = kwargs.get("owner")
        return {"task_id": "task-1", "status": "queued"}

    async def refresh_task(task_id, *, owner=None):
        captured["read"] = (task_id, owner)
        return {"task_id": task_id, "status": "running"}

    monkeypatch.setattr(jarvis_agent, "start_task", start_task)
    monkeypatch.setattr(jarvis_agent, "refresh_task", refresh_task)

    _, started = await tool_execution.execute_tool_block(
        ToolBlock("start_agent_task", json.dumps({"prompt": "inspect"})),
        session_id="session-1",
        owner="alice",
    )
    _, read = await tool_execution.execute_tool_block(
        ToolBlock("read_agent_task", json.dumps({"task_id": "task-1"})),
        owner="alice",
    )
    _, missing = await tool_execution.execute_tool_block(
        ToolBlock("read_agent_task", json.dumps({"task_id": "task-1"})),
        owner=None,
    )

    assert started["exit_code"] == 0
    assert read["exit_code"] == 0
    assert missing == {"error": "owner_required", "exit_code": 1}
    assert captured == {"start_owner": "alice", "read": ("task-1", "alice")}
