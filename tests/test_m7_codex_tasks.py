from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from routes import agent_task_routes
from src import jarvis_agent


BRIDGE_PATH = (
    Path(__file__).parents[1]
    / "services"
    / "pc-codex-bridge"
    / "jarvis_codex_bridge.py"
)
SPEC = importlib.util.spec_from_file_location("m7_jarvis_codex_bridge", BRIDGE_PATH)
bridge = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(bridge)

THREAD_ID = "019f5022-a520-7de0-9208-018cd2d4d222"


def _bridge_task(root: Path) -> object:
    return bridge.Task({
        "task_id": "fixture-task",
        "worker": "pc-codex",
        "session_id": "session-1",
        "workspace": "disposable",
        "cwd": str(root.resolve()),
        "source_root": str(root.resolve()),
        "status": "queued",
        "events": [],
    })


def test_resume_fixture_reads_supported_thread_api_and_matches_exact_root(tmp_path, monkeypatch):
    root = tmp_path / "disposable"
    root.mkdir()
    task = _bridge_task(root)
    sent = []
    task.send = sent.append
    monkeypatch.setattr(
        bridge,
        "_read_until",
        lambda _task, request_id: {
            "thread": {"id": THREAD_ID, "cwd": str(root), "status": {"type": "idle"}}
        },
    )

    bridge._validate_resume_thread(task, THREAD_ID)

    assert sent == [{
        "id": 19,
        "method": "thread/read",
        "params": {"threadId": THREAD_ID, "includeTurns": False},
    }]


@pytest.mark.parametrize(
    ("thread", "error"),
    [
        ({"id": "different", "cwd": "/tmp"}, "codex_thread_identity_mismatch"),
        ({"id": THREAD_ID, "cwd": "/tmp"}, "codex_thread_project_mismatch"),
    ],
)
def test_resume_fixture_fails_closed_for_wrong_task_or_root(tmp_path, monkeypatch, thread, error):
    root = tmp_path / "disposable"
    root.mkdir()
    task = _bridge_task(root)
    task.send = lambda _message: None
    monkeypatch.setattr(bridge, "_read_until", lambda _task, _request_id: {"thread": thread})

    with pytest.raises(RuntimeError, match=error):
        bridge._validate_resume_thread(task, THREAD_ID)


def test_bounded_bridge_create_keeps_exact_task_and_approved_root(tmp_path, monkeypatch):
    class IdleThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

    root = tmp_path / "disposable"
    root.mkdir()
    monkeypatch.setattr(bridge, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(bridge, "WORKSPACES", {"disposable": str(root)})
    monkeypatch.setattr(bridge.threading, "Thread", IdleThread)

    task = bridge.create_task({
        "session_id": "session-1",
        "workspace": "disposable",
        "prompt": "Read the fixture and report its title.",
        "permission_mode": "read_only",
        "approved": False,
        "codex_thread_id": THREAD_ID,
        "request_id": "request-fixture",
    })
    try:
        assert task.data["codex_thread_id"] == THREAD_ID
        assert task.data["workspace"] == "disposable"
        assert task.data["source_root"] == str(root.resolve())
        assert task.data["cwd"] == str(root.resolve())
        assert task.data["request_id"] == "request-fixture"
    finally:
        bridge.TASKS.pop(task.task_id, None)


class _Manager:
    def get_session(self, session_id):
        if session_id not in {"session-1", "session-2"}:
            raise KeyError(session_id)
        return SimpleNamespace(id=session_id, owner="leo", agent_target="pc-codex")


class _Adapter:
    enabled = True

    def __init__(self):
        self.started = []
        self.steered = []
        self.cancelled = []
        self.remote_status = {}

    async def start(self, task):
        self.started.append(dict(task))
        remote_id = f"remote-{len(self.started)}"
        self.remote_status[remote_id] = "running"
        return {
            "remote_task_id": remote_id,
            "status": "queued",
            "codex_thread_id": task.get("codex_thread_id") or THREAD_ID,
        }

    async def status(self, task):
        status = self.remote_status.get(task["remote_task_id"], "running")
        return {
            "status": status,
            "codex_thread_id": task.get("codex_thread_id"),
            **({"result": "Fixture complete."} if status == "completed" else {}),
        }

    async def steer(self, task, payload):
        self.steered.append((task["task_id"], payload["prompt"]))
        return {"ok": True}

    async def cancel(self, task):
        self.cancelled.append(task["task_id"])
        self.remote_status[task["remote_task_id"]] = "cancelled"
        return {"ok": True}


def test_session_presenter_uses_server_owned_live_worker_identity(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_PC_CODEX_ENABLED", "true")
    monkeypatch.setenv("ODYSSEUS_PC_CODEX_LABEL", "Friday")

    assert jarvis_agent.session_presenter(
        SimpleNamespace(agent_target="pc-codex"),
        "pc-codex",
    ) == "Friday"
    with pytest.raises(ValueError, match="conversation_target_worker_mismatch"):
        jarvis_agent.session_presenter(
            SimpleNamespace(agent_target="hermes"),
            "pc-codex",
        )


@pytest.fixture
def broker_fixture(tmp_path, monkeypatch):
    adapter = _Adapter()
    monkeypatch.setattr(jarvis_agent, "TASKS_FILE", tmp_path / "agent_tasks.json")
    monkeypatch.setattr(jarvis_agent, "_SESSION_MANAGER", _Manager())
    monkeypatch.setattr(jarvis_agent, "_START_LOCKS", {})
    monkeypatch.setattr(jarvis_agent, "adapters", lambda: {"pc-codex": adapter})
    monkeypatch.setattr(jarvis_agent, "worker_catalog", lambda: {
        "pc-codex": {
            "machine": "configured workstation",
            "workspaces": ["disposable", "other-project"],
        }
    })
    monkeypatch.setattr(jarvis_agent, "ensure_mirror", lambda _task_id: None)
    return adapter, tmp_path / "agent_tasks.json"


@pytest.mark.asyncio
async def test_broker_resume_reconnect_steer_and_cancel_one_running_fixture(broker_fixture):
    adapter, tasks_file = broker_fixture
    task = await jarvis_agent.start_task(
        "pc-codex",
        "session-1",
        "disposable",
        "Inspect the fixture and read it all.",
        owner="leo",
        codex_thread_id=THREAD_ID,
        request_id="request-1",
        presenter="Friday",
    )

    # Reconnect callers receive the same active mapping and cannot duplicate it.
    jarvis_agent._START_LOCKS.clear()
    reconnected = await jarvis_agent.start_task(
        "pc-codex",
        "session-1",
        "disposable",
        "Reconnect only.",
        owner="leo",
        codex_thread_id=THREAD_ID,
    )
    assert reconnected["task_id"] == task["task_id"]
    assert reconnected["reused"] is True
    assert len(adapter.started) == 1
    assert reconnected["presenter"] == "Friday"
    assert adapter.started[0]["workspace"] == "disposable"
    assert adapter.started[0]["codex_thread_id"] == THREAD_ID

    steered = await jarvis_agent.task_action(
        task["task_id"], "steer", {"prompt": "Use the corrected fixture."}, owner="leo"
    )
    assert steered["status"] == "running"
    assert adapter.steered == [(task["task_id"], "Use the corrected fixture.")]

    cancelled = await jarvis_agent.task_action(task["task_id"], "cancel", owner="leo")
    assert cancelled["status"] == "cancelled"
    assert adapter.cancelled == [task["task_id"]]
    assert cancelled["events"][-1]["type"] == "cancelled"

    state = json.loads(tasks_file.read_text(encoding="utf-8"))
    assert "prompt" not in state["tasks"][task["task_id"]]
    assert state["tasks"][task["task_id"]]["read_all_requested"] is True
    assert len(state["bindings"]) == 1
    binding = next(iter(state["bindings"].values()))
    assert binding["owner"] == "leo"
    assert binding["session_id"] == "session-1"
    assert binding["workspace"] == "disposable"
    assert binding["codex_thread_id"] == THREAD_ID
    assert "active_task_id" not in binding


@pytest.mark.asyncio
async def test_direct_codex_turn_reuses_mapping_and_rebinds_every_event_to_friday(broker_fixture):
    adapter, _tasks_file = broker_fixture
    task = await jarvis_agent.start_task(
        "pc-codex",
        "session-1",
        "disposable",
        "Start the fixture.",
        owner="leo",
        presenter="Jarvis",
    )

    steered, action = await jarvis_agent.direct_codex_turn(
        "session-1",
        "Use the corrected fixture.",
        owner="leo",
        workspace="other-project",
        presenter="Friday",
    )

    assert action == "steered"
    assert steered["task_id"] == task["task_id"]
    assert steered["workspace"] == "disposable"
    assert steered["presenter"] == "Friday"
    assert {event["presenter"] for event in steered["events"]} == {"Friday"}
    assert adapter.steered == [(task["task_id"], "Use the corrected fixture.")]


@pytest.mark.asyncio
async def test_direct_codex_turn_resumes_the_thread_selected_in_the_sidebar(broker_fixture):
    adapter, _tasks_file = broker_fixture
    selected_thread = "019f5022-a520-7de0-9208-018cd2d4d999"

    task, action = await jarvis_agent.direct_codex_turn(
        "session-2",
        "Continue this exact task.",
        owner="leo",
        workspace="other-project",
        presenter="Friday",
        codex_thread_id=selected_thread,
    )

    assert action == "started"
    assert task["workspace"] == "other-project"
    assert task["codex_thread_id"] == selected_thread
    assert adapter.started[0]["codex_thread_id"] == selected_thread


@pytest.mark.asyncio
async def test_completed_friday_turn_stays_friday_in_the_next_round(broker_fixture):
    adapter, _tasks_file = broker_fixture
    first, first_action = await jarvis_agent.direct_codex_turn(
        "session-1",
        "Inspect the fixture.",
        owner="leo",
        workspace="disposable",
        presenter="Friday",
    )
    adapter.remote_status[first["remote_task_id"]] = "completed"
    first = await jarvis_agent.refresh_task(first["task_id"], owner="leo")

    second, second_action = await jarvis_agent.direct_codex_turn(
        "session-1",
        "Now summarize the same fixture.",
        owner="leo",
        workspace="other-project",
        presenter="Friday",
    )
    adapter.remote_status[second["remote_task_id"]] = "completed"
    second = await jarvis_agent.refresh_task(second["task_id"], owner="leo")

    assert (first_action, second_action) == ("started", "started")
    assert first["task_id"] != second["task_id"]
    assert adapter.started[1]["codex_thread_id"] == THREAD_ID
    assert adapter.started[1]["workspace"] == "disposable"
    assert {first["presenter"], second["presenter"]} == {"Friday"}
    assert {
        event["presenter"]
        for task in (first, second)
        for event in task["events"]
    } == {"Friday"}


@pytest.mark.asyncio
async def test_broker_refuses_silent_project_reroute_for_active_conversation(broker_fixture):
    await jarvis_agent.start_task(
        "pc-codex", "session-1", "disposable", "Inspect.", owner="leo"
    )

    with pytest.raises(RuntimeError, match="conversation_task_conflict"):
        await jarvis_agent.start_task(
            "pc-codex", "session-1", "other-project", "Inspect elsewhere.", owner="leo"
        )


def test_reconnect_catalog_is_owner_scoped_bounded_and_omits_prompt(broker_fixture):
    _adapter, _tasks_file = broker_fixture
    jarvis_agent._save_task({
        "task_id": "safe-task",
        "worker": "pc-codex",
        "session_id": "session-1",
        "workspace": "disposable",
        "permission_mode": "read_only",
        "prompt": "private prompt",
        "status": "running",
        "owner": "leo",
        "created_at": 1,
        "updated_at": 2,
    })
    jarvis_agent._save_task({
        "task_id": "other-session",
        "worker": "pc-codex",
        "session_id": "session-2",
        "workspace": "disposable",
        "status": "running",
        "owner": "leo",
        "created_at": 1,
        "updated_at": 3,
    })

    tasks = jarvis_agent.list_session_tasks("session-1", "leo")

    assert [task["task_id"] for task in tasks] == ["safe-task"]
    assert "prompt" not in tasks[0]


@pytest.mark.asyncio
async def test_task_routes_expose_owner_safe_list_and_supported_steer(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        agent_task_routes,
        "list_session_tasks",
        lambda session_id, owner, limit: [{"task_id": "task-1", "session_id": session_id, "owner": owner}],
    )

    async def action(task_id, action, payload=None, **kwargs):
        captured.update(task_id=task_id, action=action, payload=payload, owner=kwargs["owner"])
        return {"task_id": task_id, "status": "running"}

    monkeypatch.setattr(agent_task_routes, "task_action", action)
    monkeypatch.setattr(agent_task_routes, "require_task_owner", lambda task_id, owner: {
        "task_id": task_id,
        "session_id": "session-1",
        "owner": owner,
        "worker": "pc-codex",
        "workspace": "disposable",
        "permission_mode": "read_only",
        "codex_thread_id": THREAD_ID,
    })
    router = agent_task_routes.setup_agent_task_routes(_Manager())
    list_endpoint = next(route.endpoint for route in router.routes if route.name == "list_tasks")
    steer_endpoint = next(route.endpoint for route in router.routes if route.name == "steer")

    assert await list_endpoint(session_id="session-1", limit=20, owner="leo") == {
        "tasks": [{"task_id": "task-1", "session_id": "session-1", "owner": "leo"}]
    }
    assert await steer_endpoint(
        "task-1", agent_task_routes.TaskSteer(prompt="Use the corrected name."), owner="leo"
    ) == {"task_id": "task-1", "status": "running"}
    assert captured == {
        "task_id": "task-1",
        "action": "steer",
        "payload": {"prompt": "Use the corrected name."},
        "owner": "leo",
    }


def test_artifact_handoff_is_cited_reviewable_and_secret_free(tmp_path, monkeypatch):
    class DatabaseSession:
        def __init__(self):
            self.rows = []

        def add(self, row):
            self.rows.append(row)

        def commit(self):
            pass

        def close(self):
            pass

    import core.database as database

    db = DatabaseSession()
    audit = []
    monkeypatch.setattr(database, "SessionLocal", lambda: db)
    monkeypatch.setattr(jarvis_agent, "TASKS_FILE", tmp_path / "agent_tasks.json")
    monkeypatch.setattr(jarvis_agent, "_SESSION_MANAGER", None)
    monkeypatch.setattr(
        jarvis_agent,
        "record_operational_event",
        lambda **values: audit.append(values) or {"event_id": "audit-1"},
    )
    jarvis_agent._save_task({
        "task_id": "artifact-task",
        "worker": "pc-codex",
        "session_id": "session-1",
        "workspace": "disposable",
        "permission_mode": "workspace_write",
        "status": "running",
        "owner": "leo",
        "request_id": "request-artifact",
        "call_id": "call-artifact",
        "authority_ref": "decision-artifact",
        "codex_thread_id": THREAD_ID,
        "events": [],
        "artifacts": [],
        "created_at": 1,
        "updated_at": 1,
    })

    jarvis_agent._append_event("artifact-task", {
        "event_id": "artifact-1",
        "type": "artifact",
        "text": "Review the fixture edit.",
        "metadata": {
            "title": "Review Fixture",
            "source_path": "reports/review.md",
            "language": "markdown",
            "content": "Bearer fixture-secret-must-not-enter-audit",
        },
    })
    jarvis_agent._append_event("artifact-task", {
        "event_id": "result-1",
        "type": "result",
        "text": "Fixture edit ready for review.",
        "metadata": {},
    })

    task = jarvis_agent.get_task("artifact-task")
    artifact = task["artifacts"][0]
    assert artifact["citation"] == "workspace:disposable/reports/review.md"
    assert artifact["review_mode"] == "reversible_edit"
    assert task["events"][0]["metadata"]["citation"] == artifact["citation"]
    assert "content" not in task["events"][0]["metadata"]
    assert [event["status"] for event in audit] == ["executed", "succeeded"]
    assert audit[-1]["request_id"] == "request-artifact"
    assert audit[-1]["task_id"] == "artifact-task"
    assert audit[-1]["evidence_refs"][0]["approved_root"] == "workspace:disposable"
    assert "fixture-secret" not in json.dumps(audit)
    assert len(db.rows) == 2


def test_artifact_handoff_rejects_outside_logical_path(tmp_path, monkeypatch):
    monkeypatch.setattr(jarvis_agent, "TASKS_FILE", tmp_path / "agent_tasks.json")
    monkeypatch.setattr(jarvis_agent, "_SESSION_MANAGER", None)
    monkeypatch.setattr(jarvis_agent, "record_operational_event", lambda **_values: {})
    jarvis_agent._save_task({
        "task_id": "outside-artifact",
        "worker": "pc-codex",
        "session_id": "session-1",
        "workspace": "disposable",
        "permission_mode": "read_only",
        "status": "running",
        "owner": "leo",
        "events": [],
        "artifacts": [],
        "created_at": 1,
        "updated_at": 1,
    })

    jarvis_agent._append_event("outside-artifact", {
        "type": "artifact",
        "text": "Outside file.",
        "metadata": {"source_path": "../outside.md", "content": "no"},
    })

    task = jarvis_agent.get_task("outside-artifact")
    assert task["status"] == "failed"
    assert task["artifacts"] == []
    assert task["events"][0]["type"] == "error"
    assert task["events"][0]["metadata"] == {"artifact_rejected": True}


@pytest.mark.asyncio
async def test_project_browser_route_uses_m6_authority_and_audits_exact_safe_identity(monkeypatch):
    audit = []
    started = []

    class Authority:
        def decide(self, call, **_kwargs):
            return {
                "decision_id": "decision-1",
                "approval_decision_id": None,
                "decision": "allow",
                "permission_mode": "bounded_write",
                "action_effect": "reversible_write",
                "policy_basis": "authenticated_explicit_request",
            }

    async def start_task(**values):
        started.append(values)
        if values["workspace"] == "outside":
            raise ValueError("unknown_workspace")
        return {
            "task_id": "task-authorized",
            "session_id": values["session_id"],
            "worker": values["worker"],
            "workspace": values["workspace"],
            "permission_mode": values["permission_mode"],
            "status": "queued",
            "owner": values["owner"],
            "codex_thread_id": THREAD_ID,
            "artifacts": [],
        }

    monkeypatch.setenv("ODYSSEUS_PRIVATE_WORKER_MUTATIONS", "true")
    monkeypatch.setenv("ODYSSEUS_PC_CODEX_ENABLED", "true")
    monkeypatch.setenv("ODYSSEUS_PC_CODEX_LABEL", "Friday")
    monkeypatch.setattr(agent_task_routes, "authority_store", Authority())
    monkeypatch.setattr(agent_task_routes, "start_task", start_task)
    monkeypatch.setattr(
        agent_task_routes,
        "record_operational_event",
        lambda **values: audit.append(values) or {"event_id": f"audit-{len(audit)}"},
    )
    router = agent_task_routes.setup_agent_task_routes(_Manager())
    create = next(route.endpoint for route in router.routes if route.name == "create")
    payload = agent_task_routes.TaskCreate(
        worker="pc-codex",
        session_id="session-1",
        workspace="disposable",
        prompt="Apply the one reversible fixture edit. token=fixture-secret",
        permission_mode="workspace_write",
        approved=True,
        request_id="request-1",
    )

    task = await create(payload, SimpleNamespace(), owner="leo")

    assert task["task_id"] == "task-authorized"
    assert started[0]["authority_ref"] == "decision-1"
    assert started[0]["request_id"] == "request-1"
    assert started[0]["presenter"] == "Friday"
    assert [event["status"] for event in audit] == [
        "requested", "authorized", "executed", "succeeded",
    ]
    assert audit[-1]["evidence_refs"][0] == {
        "task_id": "task-authorized",
        "codex_thread_id": THREAD_ID,
        "requested_project": "disposable",
        "approved_root": "workspace:disposable",
        "artifact_ids": [],
    }
    assert "fixture-secret" not in json.dumps(audit)

    denied = payload.model_copy(update={
        "workspace": "outside",
        "permission_mode": "read_only",
        "approved": False,
        "request_id": "request-2",
    })
    with pytest.raises(HTTPException) as exc:
        await create(denied, SimpleNamespace(), owner="leo")
    assert exc.value.status_code == 400
    assert exc.value.detail == "unknown_workspace"
    assert audit[-1]["status"] == "failed"
    assert audit[-1]["evidence_refs"][0]["requested_project"] == "outside"
    assert audit[-1]["evidence_refs"][0]["approved_root"] is None


@pytest.mark.asyncio
async def test_execution_rollback_switches_fail_closed_without_worker_or_bridge_start(
    tmp_path, monkeypatch
):
    class Adapter:
        enabled = True
        calls = 0

        async def start(self, _task):
            self.calls += 1
            raise AssertionError("worker must remain disabled")

    adapter = Adapter()
    monkeypatch.setenv("ODYSSEUS_CODEX_TASK_EXECUTION_ENABLED", "false")
    monkeypatch.setattr(jarvis_agent, "_SESSION_MANAGER", _Manager())
    monkeypatch.setattr(jarvis_agent, "adapters", lambda: {"pc-codex": adapter})
    monkeypatch.setattr(jarvis_agent, "worker_catalog", lambda: {
        "pc-codex": {"machine": "test", "workspaces": ["disposable"]}
    })
    with pytest.raises(RuntimeError, match="codex_task_execution_disabled"):
        await jarvis_agent.start_task(
            "pc-codex", "session-1", "disposable", "Do not run.", owner="leo"
        )
    assert adapter.calls == 0

    root = tmp_path / "disposable"
    root.mkdir()
    monkeypatch.setenv("JARVIS_CODEX_EXECUTION_ENABLED", "false")
    monkeypatch.setattr(bridge, "WORKSPACES", {"disposable": str(root)})
    monkeypatch.setattr(bridge, "WORKSPACE_NAMES", {"disposable": "Disposable"})
    assert bridge.catalog_projects()["items"] == [{
        "project_id": "disposable",
        "display_name": "Disposable",
        "approved_root": "workspace:disposable",
        "availability": "available",
    }]
    with pytest.raises(RuntimeError, match="codex_task_execution_disabled"):
        bridge.create_task({"workspace": "disposable", "prompt": "Do not run."})
