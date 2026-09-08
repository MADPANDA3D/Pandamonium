from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from jsonschema import Draft202012Validator, FormatChecker

from routes import agent_task_routes
from src import jarvis_agent
from src.action_protocol import normalize_action_call
from src.authority_protocol import AuthorityStore
from src.external_agent_bridge import (
    EXTERNAL_AGENT_PROTOCOL,
    ExternalAgentBridgeError,
    ExternalAgentReadOnlyAdapter,
    configured_external_agent_connections,
    external_agent_adapters,
)


NOW = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
NOW_TEXT = NOW.isoformat().replace("+00:00", "Z")
MATERIAL_EFFECTS = (
    "destructive_or_difficult_to_recover",
    "external_publication_or_communication",
    "purchase",
    "credential_or_auth_change",
    "privilege_expansion",
    "outside_workspace_boundary",
)
WIRE_VALIDATOR = Draft202012Validator(
    json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "specs/schemas/pandamonium-external-agent-sidecar-v1.schema.json"
        ).read_text(encoding="utf-8")
    ),
    format_checker=FormatChecker(),
)


def _configuration(tmp_path, *, effect="reversible_write"):
    token = tmp_path / "external-actions.token"
    token.write_text("fixture-bearer", encoding="utf-8")
    token.chmod(0o600)
    raw = [{
        "enabled": True,
        "protocol_version": EXTERNAL_AGENT_PROTOCOL,
        "id": "external-agent-a",
        "label": "External coding agent",
        "endpoint": "https://sidecar.example/v1",
        "auth_ref": f"file:{token}",
        "network_policy": "public",
        "workspaces": ["sample-project"],
        "capabilities": [
            "task.events",
            {"name": "task.start", "effect": effect},
            {"name": "task.steer", "effect": effect},
            {"name": "task.reply", "effect": effect},
            {"name": "task.cancel", "effect": effect},
            {"name": "task.status.read", "effect": "read"},
        ],
        "timeout_seconds": 5,
    }]
    return configured_external_agent_connections(json.dumps(raw))[0]


def _wire(payload, status=200):
    return status, {"content-type": "application/json"}, json.dumps(payload).encode()


def _scope(request):
    return {
        "owner_ref": request["owner_ref"],
        "connection_id": request["connection_id"],
        "worker_ref": request["worker_ref"],
        "workspace_alias": request["workspace_alias"],
    }


class ActionSidecar:
    def __init__(
        self,
        *,
        effect="reversible_write",
        version="2.0.0",
        empty_event_polls=0,
        event_text="Completed the bounded task.",
    ):
        self.effect = effect
        self.version = version
        self.empty_event_polls = empty_event_polls
        self.event_text = event_text
        self.event_polls = 0
        self.calls = []
        self.counter = 0
        self.fail_actions = False

    def _message_id(self):
        self.counter += 1
        return f"message-{self.counter:04d}"

    async def __call__(self, method, url, headers, body, timeout, pinned_ip, max_bytes):
        self.calls.append({
            "method": method,
            "url": url,
            "headers": headers,
            "body": body,
            "timeout": timeout,
            "pinned_ip": str(pinned_ip),
            "max_bytes": max_bytes,
        })
        WIRE_VALIDATOR.validate(body)
        if body.get("capability") == "capabilities.read":
            declarations = []
            for name in (
                "task.events", "task.start", "task.steer", "task.reply",
                "task.cancel", "task.status.read",
            ):
                effect = "read" if name in {"task.events", "task.status.read"} else self.effect
                declarations.append({
                    "name": name,
                    "effect": effect,
                    "authorization": (
                        "separate_gate" if effect in MATERIAL_EFFECTS else "explicit_request"
                    ),
                    "reversible": True,
                    "enabled": True,
                })
            return _wire({
                "protocol_version": EXTERNAL_AGENT_PROTOCOL,
                "envelope": "capabilities",
                "message_id": self._message_id(),
                "request_id": body["request_id"],
                "issued_at": NOW_TEXT,
                **_scope(body),
                "sidecar_id": "sidecar:fixture",
                "sidecar_version": self.version,
                "capabilities": declarations,
            })
        if self.fail_actions and url.endswith("/actions"):
            raise httpx.ConnectError("private upstream detail")
        if body["envelope"] == "cancel":
            task_ref = body["task_ref"]
            result = {"status": "cancelled"}
        elif body["capability"] == "task.start":
            task_ref = "task:fixture-1234"
            result = {"status": "running"}
        elif body["capability"] == "task.status.read":
            task_ref = body["task_ref"]
            result = {"status": "running"}
        elif body["capability"] == "task.events":
            task_ref = body["arguments"]["task_ref"]
            self.event_polls += 1
            items = []
            if self.event_polls > self.empty_event_polls:
                items.append({
                    "protocol_version": EXTERNAL_AGENT_PROTOCOL,
                    "envelope": "event",
                    "message_id": self._message_id(),
                    "request_id": body["request_id"],
                    "issued_at": NOW_TEXT,
                    **_scope(body),
                    "task_ref": task_ref,
                    "event_id": "event:fixture-0001",
                    "sequence": 1,
                    "event_type": "result",
                    "text": self.event_text,
                    "metadata": {},
                })
            result = {"items": items, "next_cursor": None}
        else:
            task_ref = body["task_ref"]
            result = {"status": "accepted"}
        return _wire({
            "protocol_version": EXTERNAL_AGENT_PROTOCOL,
            "envelope": "response",
            "message_id": self._message_id(),
            "request_id": body["request_id"],
            "issued_at": NOW_TEXT,
            **_scope(body),
            "status": "succeeded",
            "task_ref": task_ref,
            "result": result,
        })


def _adapter(configuration, sidecar, **kwargs):
    return ExternalAgentReadOnlyAdapter(
        configuration,
        requester=sidecar,
        resolver=lambda _host: ["93.184.216.34"],
        clock=lambda: NOW,
        **kwargs,
    )


async def _bound_task(adapter, action="start", **updates):
    policy = await adapter.action_policy(
        action, owner="alice", workspace="sample-project"
    )
    task = {
        "task_id": "canonical-task-1",
        "remote_task_id": None,
        "worker": "external-agent-a",
        "session_id": "session-1",
        "workspace": "sample-project",
        "prompt": "Inspect the configured project and report the result.",
        "permission_mode": "read_only",
        "owner": "alice",
        "request_id": "canonical-request-1",
        "call_id": f"canonical-call-{action}",
        "authority_ref": f"authority-{action}",
        "action_effect": policy["effect"],
        "action_capability": policy["name"],
        "external_sidecar_version": policy["sidecar_version"],
        "external_connection_version": policy["connection_version"],
        "created_at": 1,
        "updated_at": 1,
        "events": [],
        "artifacts": [],
    }
    task.update(updates)
    return task


def _action_task(task, policy, action):
    return {
        **task,
        "_action_request_id": f"canonical-request-{action}",
        "_action_call_id": f"canonical-call-{action}",
        "_action_authority_ref": f"authority-{action}",
        "_action_effect": policy["effect"],
        "_action_capability": policy["name"],
        "_action_sidecar_version": policy["sidecar_version"],
        "_action_connection_version": policy["connection_version"],
    }


@pytest.mark.asyncio
async def test_governed_lifecycle_uses_stable_ids_and_canonical_envelopes(tmp_path):
    sidecar = ActionSidecar()
    adapter = _adapter(_configuration(tmp_path), sidecar)
    task = await _bound_task(adapter)

    started = await adapter.start(task)
    repeated = await adapter.start(task)
    assert started == repeated
    assert started["remote_task_id"] == "task:fixture-1234"
    assert len([call for call in sidecar.calls if (call["body"] or {}).get("capability") == "task.start"]) == 1
    task.update(started)

    for action, payload in (
        ("steer", {"prompt": "Use the corrected bounded instruction."}),
        ("reply", {"answers": {"question-1": "Proceed read-only."}}),
    ):
        policy = await adapter.action_policy(action, owner="alice", workspace="sample-project")
        action_task = _action_task(task, policy, action)
        await getattr(adapter, action)(action_task, payload)

    status = await adapter.status(task)
    events = [event async for event in adapter.events(task)]
    cancel_policy = await adapter.action_policy("cancel", owner="alice", workspace="sample-project")
    cancelled = await adapter.cancel(_action_task(task, cancel_policy, "cancel"))

    assert status == {"status": "running"}
    assert events == [{
        "event_id": "event:fixture-0001",
        "type": "result",
        "text": "Completed the bounded task.",
        "metadata": {
            "remote_event_id": "event:fixture-0001",
            "remote_sequence": 1,
        },
    }]
    assert cancelled["task_ref"] == task["remote_task_id"]
    action_bodies = [call["body"] for call in sidecar.calls if call["url"].endswith("/actions")]
    assert all(body["owner_ref"].startswith("owner:o") for body in action_bodies)
    assert all(body["workspace_alias"] == "sample-project" for body in action_bodies)
    assert all(body["connection_id"] == adapter.connection_ref for body in action_bodies)
    assert action_bodies[-1]["envelope"] == "cancel"
    assert action_bodies[-1]["target_request_id"] == started["external_start_request_id"]


@pytest.mark.asyncio
async def test_changed_replayed_and_unsafe_arguments_fail_before_duplicate_dispatch(tmp_path):
    sidecar = ActionSidecar()
    adapter = _adapter(_configuration(tmp_path), sidecar)
    task = await _bound_task(adapter)
    await adapter.start(task)

    with pytest.raises(ExternalAgentBridgeError, match="replay_detected"):
        await adapter.start({**task, "prompt": "A changed request with the same call ID."})
    action_count = len([call for call in sidecar.calls if call["url"].endswith("/actions")])

    for prompt, error in (
        ("Read /home/operator/private.txt", "path_escape"),
        ("Use token=fixture-secret", "unauthorized"),
        ("Use Bearer abcdefgh12345", "unauthorized"),
        ("Use sk-abcdefghijklmnop", "unauthorized"),
        ("Use ghp_abcdefghijklmnop", "unauthorized"),
        ("Use xoxb-abcdefghijklmnop", "unauthorized"),
    ):
        with pytest.raises(ExternalAgentBridgeError, match=error):
            await adapter.start({**task, "call_id": f"call-{error}", "prompt": prompt})
    assert len([call for call in sidecar.calls if call["url"].endswith("/actions")]) == action_count


@pytest.mark.asyncio
async def test_event_stream_polls_after_an_empty_nonterminal_snapshot(tmp_path):
    sidecar = ActionSidecar(empty_event_polls=1)
    adapter = _adapter(
        _configuration(tmp_path), sidecar, event_poll_seconds=0
    )
    task = await _bound_task(adapter)
    task.update(await adapter.start(task))

    events = [event async for event in adapter.events(task)]

    assert sidecar.event_polls == 2
    assert [event["type"] for event in events] == ["result"]


@pytest.mark.asyncio
async def test_repeated_event_snapshot_is_idempotent(tmp_path):
    sidecar = ActionSidecar()
    adapter = _adapter(_configuration(tmp_path), sidecar)
    task = await _bound_task(adapter)
    task.update(await adapter.start(task))

    first = await adapter.task_events(
        task["remote_task_id"], owner="alice", workspace="sample-project"
    )
    repeated = await adapter.task_events(
        task["remote_task_id"], owner="alice", workspace="sample-project"
    )

    assert len(first["items"]) == 1
    assert repeated["items"] == []

    sidecar.event_text = "Conflicting replay content."
    with pytest.raises(ExternalAgentBridgeError, match="replay_detected"):
        await adapter.task_events(
            task["remote_task_id"], owner="alice", workspace="sample-project"
        )


def test_adapter_registry_reuses_state_until_configuration_changes(tmp_path, monkeypatch):
    configuration = _configuration(tmp_path)
    wire_configuration = {
        **configuration,
        "capabilities": [
            name if name == "task.events" else {"name": name, "effect": effect}
            for name, effect in configuration["capabilities"].items()
        ],
    }
    monkeypatch.setenv(
        "PANDAMONIUM_EXTERNAL_AGENT_CONNECTIONS_JSON",
        json.dumps([wire_configuration]),
    )
    monkeypatch.delenv("ODYSSEUS_EXTERNAL_AGENT_CONNECTIONS_JSON", raising=False)

    first = external_agent_adapters()[configuration["id"]]
    repeated = external_agent_adapters()[configuration["id"]]
    assert first is repeated

    changed = dict(wire_configuration, endpoint="https://replacement.example/v1")
    monkeypatch.setenv(
        "PANDAMONIUM_EXTERNAL_AGENT_CONNECTIONS_JSON",
        json.dumps([changed]),
    )
    replacement = external_agent_adapters()[configuration["id"]]
    assert replacement is not first


@pytest.mark.asyncio
async def test_current_capability_and_connection_versions_are_rechecked(tmp_path):
    configuration = _configuration(tmp_path)
    authorized_adapter = _adapter(configuration, ActionSidecar(version="2.0.0"))
    task = await _bound_task(authorized_adapter)

    changed_sidecar = _adapter(configuration, ActionSidecar(version="2.1.0"))
    with pytest.raises(ExternalAgentBridgeError, match="stale_request"):
        await changed_sidecar.start(task)

    changed_configuration = dict(configuration, endpoint="https://replacement.example/v1")
    changed_connection = _adapter(changed_configuration, ActionSidecar(version="2.0.0"))
    with pytest.raises(ExternalAgentBridgeError, match="stale_request"):
        await changed_connection.start(task)


@pytest.mark.asyncio
@pytest.mark.parametrize("effect", MATERIAL_EFFECTS)
async def test_every_material_sidecar_effect_requires_a_separate_exact_gate(tmp_path, effect):
    adapter = _adapter(_configuration(tmp_path, effect=effect), ActionSidecar(effect=effect))
    policy = await adapter.action_policy("start", owner="alice", workspace="sample-project")
    call = normalize_action_call(
        request_id="request-material-effect",
        call_id="call-material-effect",
        agent_id="jarvis",
        actor="odysseus:codex-workspace",
        capability_version="fixture",
        name="start_agent_task",
        arguments={
            "action": "create",
            "worker": adapter.worker,
            "worker_ref": policy["worker_ref"],
            "connection_id": policy["connection_id"],
            "workspace": "sample-project",
            "capability": policy["name"],
            "sidecar_version": policy["sidecar_version"],
            "connection_version": policy["connection_version"],
            "arguments": {"prompt": "Inspect the project.", "permission_mode": "read_only"},
        },
        target=policy["worker_ref"],
        authority_ref=None,
    )
    call["capability_policy"] = {
        "action_effect": policy["effect"],
        "configured_scopes": ["sample-project"],
    }

    decision = AuthorityStore(tmp_path / f"authority-{effect}.json").decide(
        call, operator_id="alice", session_id="session-1"
    )

    assert decision["action_effect"] == effect
    assert decision["decision"] == "approval_required"
    assert decision["policy_basis"] == "exact_operator_approval"


@pytest.mark.asyncio
async def test_route_reuses_only_an_exact_material_effect_receipt(tmp_path, monkeypatch):
    sidecar = ActionSidecar(effect="purchase")
    adapter = _adapter(_configuration(tmp_path, effect="purchase"), sidecar)
    store = AuthorityStore(tmp_path / "authority.json")
    started = []

    def registries(*, include_external=False):
        return {adapter.worker: adapter} if include_external else {}

    async def start_task(**values):
        started.append(values)
        return {
            "task_id": "canonical-task-1",
            "session_id": values["session_id"],
            "worker": values["worker"],
            "workspace": values["workspace"],
            "permission_mode": values["permission_mode"],
            "status": "queued",
            "owner": values["owner"],
            "artifacts": [],
        }

    class Manager:
        def get_session(self, _session_id):
            return SimpleNamespace(owner="alice", agent_target=adapter.worker)

    monkeypatch.setattr(agent_task_routes, "adapters", registries)
    monkeypatch.setattr(agent_task_routes, "authority_store", store)
    monkeypatch.setattr(agent_task_routes, "start_task", start_task)
    monkeypatch.setattr(agent_task_routes, "session_presenter", lambda _session, _worker: "External coding agent")
    monkeypatch.setattr(agent_task_routes, "record_operational_event", lambda **_values: {})
    router = agent_task_routes.setup_agent_task_routes(Manager())
    create = next(route.endpoint for route in router.routes if route.name == "create")
    payload = agent_task_routes.TaskCreate(
        worker=adapter.worker,
        session_id="session-1",
        workspace="sample-project",
        prompt="Perform the exact separately gated action.",
        request_id="request-exact-gate",
    )

    unsafe = payload.model_copy(update={
        "prompt": "Read /home/operator/private.txt",
        "request_id": "request-unsafe",
    })
    with pytest.raises(HTTPException) as rejected:
        await create(unsafe, SimpleNamespace(), owner="alice")
    assert rejected.value.status_code == 400
    assert rejected.value.detail == "path_escape"
    assert store.list_state(operator_id="alice")["decisions"] == []

    with pytest.raises(HTTPException) as pending:
        await create(payload, SimpleNamespace(), owner="alice")
    assert pending.value.status_code == 403
    decision = store.list_state(operator_id="alice")["decisions"][-1]
    store.resolve(
        decision["decision_id"], operator_id="alice", choice="approve", scope="session"
    )

    result = await create(payload, SimpleNamespace(), owner="alice")
    assert result["task_id"] == "canonical-task-1"
    assert started[0]["action_effect"] == "purchase"
    assert started[0]["action_capability"] == "task.start"

    changed = payload.model_copy(update={"prompt": "Perform a different action."})
    with pytest.raises(HTTPException) as changed_pending:
        await create(changed, SimpleNamespace(), owner="alice")
    assert changed_pending.value.status_code == 403
    assert len(started) == 1


@pytest.mark.asyncio
async def test_canonical_broker_persists_action_identity_and_cancelled_history(tmp_path, monkeypatch):
    sidecar = ActionSidecar()
    adapter = _adapter(_configuration(tmp_path), sidecar)
    start_policy = await adapter.action_policy("start", owner="alice", workspace="sample-project")

    def registries(*, include_external=False):
        return {adapter.worker: adapter} if include_external else {}

    class Manager:
        def get_session(self, _session_id):
            return SimpleNamespace(owner="alice")

    tasks_file = tmp_path / "agent_tasks.json"
    monkeypatch.setattr(jarvis_agent, "TASKS_FILE", tasks_file)
    monkeypatch.setattr(jarvis_agent, "_SESSION_MANAGER", Manager())
    monkeypatch.setattr(jarvis_agent, "_START_LOCKS", {})
    monkeypatch.setattr(jarvis_agent, "adapters", registries)
    monkeypatch.setattr(jarvis_agent, "ensure_mirror", lambda _task_id: None)

    task = await jarvis_agent.start_task(
        adapter.worker,
        "session-1",
        "sample-project",
        "Inspect the configured project and report the result.",
        owner="alice",
        request_id="request-start",
        call_id="call-start",
        authority_ref="authority-start",
        action_effect=start_policy["effect"],
        action_capability=start_policy["name"],
        external_sidecar_version=start_policy["sidecar_version"],
        external_connection_version=start_policy["connection_version"],
    )
    steer_policy = await adapter.action_policy("steer", owner="alice", workspace="sample-project")
    task = await jarvis_agent.task_action(
        task["task_id"],
        "steer",
        {"prompt": "Use the corrected bounded instruction."},
        owner="alice",
        request_id="request-steer",
        call_id="call-steer",
        authority_ref="authority-steer",
        action_effect=steer_policy["effect"],
        action_capability=steer_policy["name"],
        external_sidecar_version=steer_policy["sidecar_version"],
        external_connection_version=steer_policy["connection_version"],
    )
    cancel_policy = await adapter.action_policy("cancel", owner="alice", workspace="sample-project")
    task = await jarvis_agent.task_action(
        task["task_id"],
        "cancel",
        owner="alice",
        request_id="request-cancel",
        call_id="call-cancel",
        authority_ref="authority-cancel",
        action_effect=cancel_policy["effect"],
        action_capability=cancel_policy["name"],
        external_sidecar_version=cancel_policy["sidecar_version"],
        external_connection_version=cancel_policy["connection_version"],
    )

    assert task["status"] == "cancelled"
    assert [event["type"] for event in task["events"]] == [
        "accepted", "progress", "cancelled",
    ]
    assert all(
        event["metadata"].get("remote_request_id")
        for event in task["events"]
    )
    assert task["artifacts"] == []
    persisted = json.loads(tasks_file.read_text(encoding="utf-8"))["tasks"][task["task_id"]]
    assert persisted["events"] == task["events"]
    assert "prompt" not in persisted


@pytest.mark.asyncio
async def test_broker_persists_safe_failure_without_prompt_or_upstream_detail(tmp_path, monkeypatch):
    sidecar = ActionSidecar()
    adapter = _adapter(_configuration(tmp_path), sidecar)
    policy = await adapter.action_policy("start", owner="alice", workspace="sample-project")
    sidecar.fail_actions = True

    def registries(*, include_external=False):
        return {adapter.worker: adapter} if include_external else {}

    class Manager:
        def get_session(self, _session_id):
            return SimpleNamespace(owner="alice")

    tasks_file = tmp_path / "agent_tasks.json"
    monkeypatch.setattr(jarvis_agent, "TASKS_FILE", tasks_file)
    monkeypatch.setattr(jarvis_agent, "_SESSION_MANAGER", Manager())
    monkeypatch.setattr(jarvis_agent, "_START_LOCKS", {})
    monkeypatch.setattr(jarvis_agent, "adapters", registries)
    monkeypatch.setattr(jarvis_agent, "ensure_mirror", lambda _task_id: None)

    with pytest.raises(ExternalAgentBridgeError, match="sidecar_unavailable"):
        await jarvis_agent.start_task(
            adapter.worker,
            "session-1",
            "sample-project",
            "This prompt must never persist.",
            owner="alice",
            request_id="request-start",
            call_id="call-start",
            authority_ref="authority-start",
            action_effect=policy["effect"],
            action_capability=policy["name"],
            external_sidecar_version=policy["sidecar_version"],
            external_connection_version=policy["connection_version"],
        )

    state = json.loads(tasks_file.read_text(encoding="utf-8"))
    task = next(iter(state["tasks"].values()))
    assert task["status"] == "failed"
    assert task["error"] == "sidecar_unavailable"
    assert "prompt" not in task
    assert "private upstream detail" not in json.dumps(state)
