from __future__ import annotations

import json
import uuid
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
    _hermes_instructions,
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
        {
            "type": "progress",
            "event_id": "remote-1",
            "metadata": {"remote_event_id": "remote-1"},
        },
        {"type": "result", "event_id": "reconciled", "metadata": {"reconciled": True}},
    ])

    assert _last_remote_event_id(task) == "remote-1"


@pytest.mark.asyncio
async def test_worker_health_redacts_transport_details(tmp_path, monkeypatch):
    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url):
            raise RuntimeError("dial failed at http://private-host.test:8040/?token=secret")

    token = tmp_path / "token"
    token.write_text("secret", encoding="utf-8")
    adapter = CodexBridgeAdapter(
        "pc-codex", "http://private-host.test:8040", token, enabled=True, machine="test"
    )
    monkeypatch.setattr(agent_worker_adapters.httpx, "AsyncClient", Client)

    health = await adapter.health()

    assert health == {
        "machine": "test",
        "state": "unreachable",
        "reason": "connection_failed",
    }
    assert "private-host" not in json.dumps(health)
    assert "secret" not in json.dumps(health)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "expected_state"),
    [
        ({"app_server": True}, "incompatible"),
        ({
            "app_server": True,
            "protocol_version": "pandamonium.codex-bridge.v2",
            "features": {"project_catalog": True, "task_control": True},
        }, "connected"),
    ],
)
async def test_codex_bridge_requires_the_catalog_and_task_protocol(
    tmp_path, monkeypatch, payload, expected_state
):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return payload

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url):
            return Response()

    token = tmp_path / "token"
    token.write_text("secret", encoding="utf-8")
    adapter = CodexBridgeAdapter(
        "pc-codex", "http://worker.test", token, enabled=True, machine="test"
    )
    monkeypatch.setattr(agent_worker_adapters.httpx, "AsyncClient", Client)

    health = await adapter.health()

    assert health["state"] == expected_state
    assert health["protocol_ready"] is (expected_state == "connected")
    if expected_state == "incompatible":
        assert health["reason"] == "bridge_update_required"


@pytest.mark.asyncio
async def test_worker_status_reports_configuration_and_readiness(monkeypatch):
    class Adapter:
        enabled = True

        async def health(self):
            return {
                "state": "connected",
                "machine": "test",
                "protocol": "codex-bridge",
                "protocol_ready": True,
                "display_name": "Friday",
                "installation_capabilities": ["codex"],
            }

    monkeypatch.setattr(jarvis_agent, "adapters", lambda: {"pc-codex": Adapter()})
    monkeypatch.setattr(
        jarvis_agent,
        "worker_catalog",
        lambda _registry: {
            "pc-codex": {
                "enabled": True,
                "configured": True,
                "ready": False,
                "adapter": "codex-bridge",
                "capabilities": ["code"],
                "workspaces": ["workspace"],
            }
        },
    )

    status = (await jarvis_agent.worker_statuses())["pc-codex"]

    assert status["configured"] is True
    assert status["ready"] is True
    assert status["enabled"] is True
    assert status["adapter"] == "codex-bridge"
    assert status["label"] == "Friday"
    assert status["installation_capabilities"] == ["codex"]
    assert status["connection"]["state"] == "connected"
    assert "url" not in status["connection"]
    assert "error" not in status["connection"]


@pytest.mark.asyncio
async def test_worker_status_omits_unconfigured_compatibility_slots():
    class Adapter:
        def __init__(self, enabled):
            self.enabled = enabled
            self.calls = 0

        async def health(self):
            self.calls += 1
            return {
                "state": "connected",
                "protocol": "codex-bridge",
                "installation_capabilities": ["codex"],
            }

    friday = Adapter(True)
    absent_vps = Adapter(False)
    registry = {"pc-codex": friday, "vps-codex": absent_vps}
    catalog = {
        worker: {
            "id": worker,
            "label": worker,
            "configured": adapter.enabled,
            "ready": False,
            "capabilities": [],
            "workspaces": [],
        }
        for worker, adapter in registry.items()
    }

    statuses = await agent_worker_adapters.probe_worker_statuses(registry, catalog)

    assert list(statuses) == ["pc-codex"]
    assert friday.calls == 1
    assert absent_vps.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_kind", ["codex", "hermes"])
async def test_worker_adapters_reject_write_or_preapproved_tasks_before_network(
    tmp_path, adapter_kind, monkeypatch
):
    monkeypatch.delenv("ODYSSEUS_PRIVATE_WORKER_MUTATIONS", raising=False)
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
@pytest.mark.parametrize("adapter_kind", ["codex", "hermes"])
async def test_worker_adapters_allow_private_preapproved_workspace_write(
    tmp_path, adapter_kind, monkeypatch
):
    monkeypatch.setenv("ODYSSEUS_PRIVATE_WORKER_MUTATIONS", "true")
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"task_id": "remote-1"} if adapter_kind == "codex" else {"run_id": "remote-1"}

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, json, headers):
            captured.update(url=url, payload=json, headers=headers)
            return Response()

    token = tmp_path / "token"
    token.write_text("secret", encoding="utf-8")
    adapter = (
        CodexBridgeAdapter("pc-codex", "http://worker.test", token, enabled=True, machine="test")
        if adapter_kind == "codex"
        else HermesRunsAdapter("http://worker.test", token, enabled=True)
    )
    monkeypatch.setattr(agent_worker_adapters.httpx, "AsyncClient", Client)
    task = _task(
        prompt="Apply the approved fix.",
        permission_mode="workspace_write",
        approved=True,
    )

    remote = await adapter.start(task)

    assert remote["remote_task_id"] == "remote-1"
    if adapter_kind == "codex":
        assert captured["payload"]["permission_mode"] == "workspace_write"
        assert captured["payload"]["approved"] is True
    else:
        assert "Pandamonium approved this task at the broker level" in captured["payload"]["instructions"]
        assert "native tool approval gate" in captured["payload"]["instructions"]
        task["remote_task_id"] = "remote-1"
        await adapter.approve(task, {"choice": "once"})
        assert captured["url"].endswith("/v1/runs/remote-1/approval")
        assert captured["payload"]["choice"] == "once"


@pytest.mark.asyncio
async def test_private_worker_adapter_still_requires_write_approval(tmp_path, monkeypatch):
    monkeypatch.setenv("ODYSSEUS_PRIVATE_WORKER_MUTATIONS", "true")
    adapter = CodexBridgeAdapter(
        "pc-codex", "http://worker.test", tmp_path / "token", enabled=True, machine="test"
    )

    with pytest.raises(PermissionError, match="approval_required"):
        await adapter.start(_task(permission_mode="workspace_write", approved=False))
    with pytest.raises(PermissionError, match="public_tasks_read_only"):
        await adapter.start(_task(permission_mode="read_only", approved=True))


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
            {
                "type": "progress",
                "event_id": "remote-1",
                "metadata": {"remote_event_id": "remote-1"},
            },
        ],
    )

    events = [event async for event in adapter.events(task)]

    assert events[0]["event_id"] == "remote-2"
    assert events[0]["metadata"]["remote_event_id"] == "remote-2"
    assert captured["params"] == {"after": -1}
    assert captured["headers"]["Last-Event-ID"] == "remote-1"


@pytest.mark.asyncio
async def test_hermes_stream_uses_sse_cursor_and_keeps_idless_repeats(tmp_path, monkeypatch):
    captured = {}

    class Response:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def raise_for_status(self):
            return None

        async def aiter_lines(self):
            yield "id: hermes-2"
            yield 'data: {"event":"reasoning.available","text":"same"}'
            yield 'data: {"event":"reasoning.available","text":"same"}'
            yield 'data: {"event":"reasoning.available","text":"same"}'

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, method, url, headers):
            captured.update(method=method, url=url, headers=headers)
            return Response()

    token = tmp_path / "token"
    token.write_text("secret", encoding="utf-8")
    adapter = HermesRunsAdapter("http://hermes.test", token, enabled=True)
    monkeypatch.setattr(agent_worker_adapters.httpx, "AsyncClient", Client)
    task = _task(
        worker="hermes",
        events=[{
            "type": "progress",
            "event_id": "hermes-1",
            "metadata": {"remote_event_id": "hermes-1"},
        }],
    )

    events = [event async for event in adapter.events(task)]

    assert captured["headers"]["Last-Event-ID"] == "hermes-1"
    assert events[0]["event_id"] == "hermes-2"
    assert events[0]["metadata"]["remote_event_id"] == "hermes-2"
    assert events[1]["event_id"] != events[2]["event_id"]
    assert "remote_event_id" not in events[1]["metadata"]
    assert "remote_event_id" not in events[2]["metadata"]


@pytest.mark.asyncio
async def test_hermes_direct_chat_uses_scoped_session_and_native_gordon_agent(
    tmp_path, monkeypatch
):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "  Good evening, Leo.  "}}]}

    class Client:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, json, headers):
            captured.update(url=url, payload=json, headers=headers)
            return Response()

    token = tmp_path / "token"
    token.write_text("secret", encoding="utf-8")
    adapter = HermesRunsAdapter("http://hermes.test/", token, enabled=True)
    monkeypatch.setattr(agent_worker_adapters.httpx, "AsyncClient", Client)

    reply = await adapter.direct_chat(
        session_id="odysseus-gordon-foreground-session",
        session_key="odysseus:gordon:memory-scope",
        message="Good evening. Is this Gordon?",
    )

    assert reply == "Good evening, Leo."
    assert captured["url"] == "http://hermes.test/v1/chat/completions"
    assert captured["client_kwargs"] == {"timeout": 300}
    assert captured["headers"] == {
        "Authorization": "Bearer secret",
        "X-Hermes-Session-Id": "odysseus-gordon-foreground-session",
        "X-Hermes-Session-Key": "odysseus:gordon:memory-scope",
    }
    assert captured["payload"]["model"] == "hermes-agent"
    assert captured["payload"]["stream"] is False
    assert "tools" not in captured["payload"]
    assert "tool_choice" not in captured["payload"]
    assert captured["payload"]["messages"] == [{
        "role": "user",
        "content": "Good evening. Is this Gordon?",
    }]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "permission_mode,approved",
    [("workspace_write", False), ("read_only", True)],
)
async def test_public_task_api_rejects_write_or_preapproval(permission_mode, approved, monkeypatch):
    monkeypatch.delenv("ODYSSEUS_PRIVATE_WORKER_MUTATIONS", raising=False)
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


@pytest.mark.asyncio
async def test_private_task_api_allows_only_preapproved_workspace_write(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_PRIVATE_WORKER_MUTATIONS", "true")
    captured = {}
    manager = SimpleNamespace(
        get_session=lambda session_id: SimpleNamespace(id=session_id, owner="alice"),
    )

    async def start_task(**values):
        captured.update(values)
        return {"task_id": "private-task"}

    monkeypatch.setattr(agent_task_routes, "start_task", start_task)
    router = agent_task_routes.setup_agent_task_routes(manager)
    endpoint = next(
        route.endpoint
        for route in router.routes
        if getattr(route, "path", None) == "/api/agent-tasks"
        and "POST" in getattr(route, "methods", set())
    )
    payload = agent_task_routes.TaskCreate(
        worker="pc-codex",
        session_id="session-1",
        workspace="home-lab",
        prompt="Apply the approved fix.",
        permission_mode="workspace_write",
        approved=True,
    )

    assert await endpoint(payload, SimpleNamespace(), owner="alice") == {"task_id": "private-task"}
    assert captured["permission_mode"] == "workspace_write"
    assert captured["approved"] is True

    for permission_mode, approved, detail in (
        ("workspace_write", False, "approval_required"),
        ("read_only", True, "Public worker tasks are read-only"),
    ):
        rejected = payload.model_copy(update={"permission_mode": permission_mode, "approved": approved})
        with pytest.raises(HTTPException) as exc:
            await endpoint(rejected, SimpleNamespace(), owner="alice")
        assert exc.value.status_code == 403
        assert exc.value.detail == detail


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
async def test_direct_hermes_turn_enforces_owner_and_scopes_session_to_owner_chat_workspace(
    monkeypatch,
):
    calls = []
    requested_sessions = []

    class Adapter:
        enabled = True

        async def direct_chat(self, **values):
            calls.append(values)
            return "This is Gordon."

    def get_session(session_id):
        requested_sessions.append(session_id)
        return SimpleNamespace(id=session_id, owner="alice")

    monkeypatch.setattr(
        jarvis_agent,
        "_SESSION_MANAGER",
        SimpleNamespace(get_session=get_session),
    )
    monkeypatch.setattr(jarvis_agent, "adapters", lambda: {"hermes": Adapter()})
    monkeypatch.setattr(
        jarvis_agent,
        "worker_catalog",
        lambda: {"hermes": {"workspaces": ["home-lab"]}},
    )

    reply = await jarvis_agent.direct_hermes_turn(
        "chat-session-1",
        "Good evening. Is this Gordon?",
        owner=" alice ",
        workspace="home-lab",
    )

    scope = uuid.uuid5(
        uuid.NAMESPACE_URL,
        "odysseus:gordon:alice:chat-session-1:home-lab",
    )
    assert reply == "This is Gordon."
    assert requested_sessions == ["chat-session-1"]
    assert calls == [{
        "session_id": f"odysseus-gordon-{scope}",
        "session_key": f"odysseus:gordon:{scope}",
        "message": "Good evening. Is this Gordon?",
    }]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "owner,workspace,error,message",
    [
        (None, "home-lab", PermissionError, "owner_required"),
        ("bob", "home-lab", PermissionError, "session_owner_mismatch"),
        ("alice", "other", ValueError, "unknown_workspace"),
    ],
)
async def test_direct_hermes_turn_rejects_invalid_scope_before_worker_call(
    owner, workspace, error, message, monkeypatch
):
    class Adapter:
        enabled = True
        calls = 0

        async def direct_chat(self, **_values):
            self.calls += 1
            return "must not be reached"

    adapter = Adapter()
    manager = SimpleNamespace(
        get_session=lambda session_id: SimpleNamespace(id=session_id, owner="alice")
    )
    monkeypatch.setattr(jarvis_agent, "_SESSION_MANAGER", manager)
    monkeypatch.setattr(jarvis_agent, "adapters", lambda: {"hermes": adapter})
    monkeypatch.setattr(
        jarvis_agent,
        "worker_catalog",
        lambda: {"hermes": {"workspaces": ["home-lab"]}},
    )

    with pytest.raises(error, match=message):
        await jarvis_agent.direct_hermes_turn(
            "chat-session-1",
            "Hello",
            owner=owner,
            workspace=workspace,
        )

    assert adapter.calls == 0


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
async def test_broker_allows_only_private_preapproved_workspace_write(tmp_path, monkeypatch):
    class Adapter:
        enabled = True

        def __init__(self):
            self.started = []

        async def start(self, task):
            self.started.append(task)
            return {"remote_task_id": "remote-private", "status": "queued"}

    adapter = Adapter()
    manager = SimpleNamespace(
        get_session=lambda session_id: SimpleNamespace(id=session_id, owner="alice")
    )
    monkeypatch.setenv("ODYSSEUS_PRIVATE_WORKER_MUTATIONS", "true")
    monkeypatch.setattr(jarvis_agent, "TASKS_FILE", tmp_path / "agent_tasks.json")
    monkeypatch.setattr(jarvis_agent, "_SESSION_MANAGER", manager)
    monkeypatch.setattr(jarvis_agent, "adapters", lambda: {"pc-codex": adapter})
    monkeypatch.setattr(
        jarvis_agent,
        "worker_catalog",
        lambda: {"pc-codex": {"machine": "test", "workspaces": ["home-lab"]}},
    )
    monkeypatch.setattr(jarvis_agent, "ensure_mirror", lambda _task_id: None)

    task = await jarvis_agent.start_task(
        "pc-codex",
        "session-1",
        "home-lab",
        "Apply the approved fix.",
        permission_mode="workspace_write",
        approved=True,
        owner="alice",
    )

    assert task["permission_mode"] == "workspace_write"
    assert task["approved"] is True
    assert adapter.started[0]["approved"] is True

    with pytest.raises(PermissionError, match="approval_required"):
        await jarvis_agent.start_task(
            "pc-codex",
            "session-1",
            "home-lab",
            "Do not run this.",
            permission_mode="workspace_write",
            approved=False,
            owner="alice",
        )
    with pytest.raises(PermissionError, match="public_tasks_read_only"):
        await jarvis_agent.start_task(
            "pc-codex",
            "session-1",
            "home-lab",
            "Do not run this.",
            permission_mode="read_only",
            approved=True,
            owner="alice",
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
    monkeypatch.delenv("ODYSSEUS_PRIVATE_WORKER_MUTATIONS", raising=False)
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


@pytest.mark.asyncio
async def test_private_write_task_preserves_native_approval_choices(tmp_path, monkeypatch):
    class Adapter:
        def __init__(self):
            self.choices = []

        async def approve(self, _task, payload):
            self.choices.append(payload["choice"])
            return {}

        async def status(self, _task):
            return {"status": "running"}

    adapter = Adapter()
    monkeypatch.setenv("ODYSSEUS_PRIVATE_WORKER_MUTATIONS", "true")
    monkeypatch.setattr(jarvis_agent, "TASKS_FILE", tmp_path / "agent_tasks.json")
    monkeypatch.setattr(jarvis_agent, "adapters", lambda: {"hermes": adapter})
    monkeypatch.setattr(jarvis_agent, "ensure_mirror", lambda _task_id: None)
    jarvis_agent._save_task(_task(
        worker="hermes",
        status="waiting_approval",
        permission_mode="workspace_write",
        approved=True,
    ))

    await jarvis_agent.task_action(
        "task-1",
        "approval",
        {"choice": "once"},
        owner="alice",
    )

    assert adapter.choices == ["once"]
    assert "mutation using normal Hermes tools" in _hermes_instructions(
        {"permission_mode": "workspace_write", "approved": True}
    )


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
async def test_worker_stream_retries_clean_eof_until_terminal(tmp_path, monkeypatch):
    class Adapter:
        def __init__(self):
            self.calls = 0

        async def events(self, _task):
            self.calls += 1
            if self.calls == 3:
                yield {"event_id": "result-1", "type": "result", "text": "Done."}
            return

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
    assert [(event["event_id"], event["type"]) for event in saved["events"]] == [
        ("result-1", "result"),
    ]
    assert saved["status"] == "completed"


@pytest.mark.asyncio
async def test_broken_stream_reconciles_running_worker_until_terminal(tmp_path, monkeypatch):
    class Adapter:
        def __init__(self):
            self.stream_calls = 0
            self.status_calls = 0

        async def events(self, _task):
            self.stream_calls += 1
            raise RuntimeError("one-consumer stream closed")
            yield

        async def status(self, _task):
            self.status_calls += 1
            if self.status_calls < 2:
                return {"status": "running"}
            return {"status": "completed", "result": "Recovered result."}

    async def passthrough(_task, event):
        return event

    adapter = Adapter()
    monkeypatch.setattr(jarvis_agent, "TASKS_FILE", tmp_path / "agent_tasks.json")
    monkeypatch.setattr(jarvis_agent, "_SESSION_MANAGER", None)
    monkeypatch.setattr(jarvis_agent, "_MIRRORS", {})
    monkeypatch.setattr(jarvis_agent, "STREAM_RECONCILE_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(jarvis_agent, "STREAM_RECONCILE_POLL_SECONDS", 0.1)
    monkeypatch.setattr(jarvis_agent, "adapters", lambda: {"hermes": adapter})
    monkeypatch.setattr(jarvis_agent, "_enrich_worker_event", passthrough)
    jarvis_agent._save_task(_task(worker="hermes"))

    await jarvis_agent._mirror("task-1")

    saved = jarvis_agent.get_task("task-1")
    assert adapter.stream_calls == 3
    assert adapter.status_calls == 2
    assert saved["status"] == "completed"
    assert saved["result"] == "Recovered result."
    assert saved["events"][-1]["type"] == "result"


@pytest.mark.asyncio
async def test_model_task_tools_forward_owner_and_reject_missing_identity(monkeypatch):
    captured = {}

    async def start_task(**kwargs):
        captured["start_owner"] = kwargs.get("owner")
        captured["start_presenter"] = kwargs.get("presenter")
        captured["start_persist_result"] = kwargs.get("persist_result")
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
        presenter="Jarvis",
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
    assert captured == {
        "start_owner": "alice",
        "start_presenter": "Jarvis",
        "start_persist_result": True,
        "read": ("task-1", "alice"),
    }
