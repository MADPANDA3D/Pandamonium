import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from routes import agent_task_routes, voice_routes
from src import agent_worker_adapters as adapters_mod
from src import agent_worker_broker as broker


class Manager:
    def get_session(self, session_id):
        if session_id != "session-1":
            raise KeyError(session_id)
        return SimpleNamespace(id=session_id, owner="alice")


def task(**updates):
    value = {
        "task_id": "task-1",
        "remote_task_id": "remote-1",
        "worker": "pc-codex",
        "session_id": "session-1",
        "workspace": "demo",
        "permission_mode": "read_only",
        "approved": False,
        "status": "running",
        "owner": "alice",
        "events": [],
        "created_at": 1,
        "updated_at": 1,
    }
    value.update(updates)
    return value


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(broker, "TASKS_FILE", tmp_path / "tasks.json")
    monkeypatch.setattr(broker, "_MIRRORS", {})
    broker.configure(Manager())
    return tmp_path


def test_public_permission_boundary_rejects_write_and_preapproval():
    adapters_mod.require_read_only("read_only", False)
    with pytest.raises(PermissionError, match="public_tasks_read_only"):
        adapters_mod.require_read_only("workspace_write", False)
    with pytest.raises(PermissionError, match="public_tasks_read_only"):
        adapters_mod.require_read_only("read_only", True)


def test_owner_enforcement_and_first_terminal_event(isolated):
    broker._save_task(task())
    assert broker.require_task_owner("task-1", "alice")["task_id"] == "task-1"
    with pytest.raises(PermissionError, match="task_owner_mismatch"):
        broker.require_task_owner("task-1", "bob")

    broker._append_event("task-1", {"event_id": 7, "type": "progress", "text": "Working"})
    broker._append_event("task-1", {"event_id": "7", "type": "progress", "text": "Replay"})
    broker._append_event("task-1", {"event_id": "done", "type": "result", "text": "Done"})
    broker._append_event("task-1", {"event_id": "late", "type": "error", "text": "Late"})
    saved = broker.get_task("task-1")
    assert [event["event_id"] for event in saved["events"]] == ["7", "done"]
    assert saved["status"] == "completed"


@pytest.mark.asyncio
async def test_start_is_owner_scoped_and_prompt_is_not_persisted(isolated, monkeypatch):
    captured = {}

    class Adapter:
        enabled = True
        label = "PC Codex"

        async def start(self, value):
            captured.update(value)
            return {"remote_task_id": "remote-1", "status": "queued"}

    monkeypatch.setattr(broker, "adapters", lambda: {"pc-codex": Adapter()})
    monkeypatch.setattr(broker, "ensure_mirror", lambda _task_id: None)
    started = await broker.start_task(
        "pc-codex", "session-1", "demo", "private prompt", owner="alice"
    )
    assert started["status"] == "running"
    assert captured["prompt"] == "private prompt"
    persisted = json.loads(broker.TASKS_FILE.read_text(encoding="utf-8"))
    assert "private prompt" not in json.dumps(persisted)
    assert persisted["tasks"][started["task_id"]]["owner"] == "alice"


@pytest.mark.asyncio
async def test_stream_retries_twice_and_deduplicates_replay(isolated, monkeypatch):
    class Adapter:
        def __init__(self):
            self.calls = 0

        async def events(self, _task):
            self.calls += 1
            yield {"event_id": "progress-1", "type": "progress", "text": "Working"}
            if self.calls < 3:
                raise RuntimeError("stream closed")
            yield {"event_id": "result-1", "type": "result", "text": "Done"}

    adapter = Adapter()
    monkeypatch.setattr(broker, "adapters", lambda: {"pc-codex": adapter})
    broker._save_task(task())
    await broker._mirror("task-1")
    saved = broker.get_task("task-1")
    assert adapter.calls == 3
    assert [event["event_id"] for event in saved["events"]] == ["progress-1", "result-1"]


@pytest.mark.asyncio
async def test_status_is_redacted(monkeypatch):
    class Adapter:
        enabled = True
        label = "PC Codex"
        adapter_name = "codex-bridge"
        configured_workspaces = ["demo"]

        async def health(self):
            return {
                "state": "connected",
                "capabilities": ["read_only"],
                "workspaces": ["demo"],
                "url": "http://private.invalid",
                "token_file": "/secret/token",
            }

    monkeypatch.setattr(broker, "adapters", lambda: {"pc-codex": Adapter()})
    status = (await broker.worker_statuses())["pc-codex"]
    assert status["ready"] is True
    assert status["connection"] == {"state": "connected"}
    assert "private.invalid" not in json.dumps(status)
    assert "/secret/token" not in json.dumps(status)


@pytest.mark.asyncio
async def test_hermes_fails_closed_without_enforced_read_only(tmp_path, monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"features": {"run_submission": True, "run_events_sse": True, "run_stop": True}, "workspaces": ["demo"]}

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, *_args, **_kwargs):
            return Response()

    token = tmp_path / "token"
    token.write_text("test", encoding="utf-8")
    adapter = adapters_mod.HermesRunsAdapter(
        "http://example.invalid", token, enabled=True, label="Hermes", workspaces=["demo"]
    )
    monkeypatch.setattr(adapters_mod.httpx, "AsyncClient", Client)
    assert await adapter.health() == {"state": "incompatible", "reason": "read_only_not_enforced"}


@pytest.mark.asyncio
async def test_public_route_rejects_caller_preapproval(monkeypatch):
    router = agent_task_routes.setup_agent_task_routes(Manager())
    endpoint = next(route.endpoint for route in router.routes if route.name == "create")
    payload = agent_task_routes.TaskCreate(
        worker="pc-codex",
        session_id="session-1",
        workspace="demo",
        prompt="inspect",
        approved=True,
    )
    with pytest.raises(HTTPException) as exc:
        await endpoint(payload, SimpleNamespace(headers={}), "alice")
    assert exc.value.status_code == 403
    assert exc.value.detail == "Public worker tasks are read-only"


def test_worker_voice_commands_are_fixed_and_exact():
    assert voice_routes._worker_command("Ask PC Codex in demo to inspect tests.") == (
        "start", "pc-codex", "PC Codex", "demo", "inspect tests"
    )
    assert voice_routes._worker_command("Cancel Hermes.") == (
        "cancel", "hermes", "Hermes", None, None
    )
    assert voice_routes._worker_command("Ask arbitrary-agent to inspect tests") is None
    assert voice_routes._worker_command("Run a script in the browser") is None
