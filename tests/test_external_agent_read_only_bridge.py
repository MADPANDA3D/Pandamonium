from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from jsonschema import Draft202012Validator, FormatChecker

import routes.agent_task_routes as agent_task_routes
import src.agent_worker_adapters as worker_adapters
from src.external_agent_bridge import (
    EXTERNAL_AGENT_PROTOCOL,
    EXTERNAL_READ_CAPABILITIES,
    ExternalAgentBridgeError,
    ExternalAgentReadOnlyAdapter,
    _validated_external_agent_ips,
    configured_external_agent_connections,
)


NOW = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
NOW_TEXT = NOW.isoformat().replace("+00:00", "Z")
ROOT = Path(__file__).resolve().parents[1]
WIRE_VALIDATOR = Draft202012Validator(
    json.loads(
        (ROOT / "specs/schemas/pandamonium-external-agent-sidecar-v1.schema.json").read_text()
    ),
    format_checker=FormatChecker(),
)
DISCOVERY_VALIDATOR = Draft202012Validator(
    json.loads((ROOT / "specs/schemas/pandamonium-discovery-v1.schema.json").read_text()),
    format_checker=FormatChecker(),
)


def _connection(tmp_path: Path, **updates):
    token = tmp_path / "external-agent.token"
    token.write_text("fixture-bearer", encoding="utf-8")
    token.chmod(0o600)
    value = {
        "enabled": True,
        "protocol_version": EXTERNAL_AGENT_PROTOCOL,
        "id": "external-agent-a",
        "label": "External coding agent",
        "endpoint": "https://sidecar.example/v1",
        "auth_ref": f"file:{token}",
        "network_policy": "public",
        "workspaces": ["sample-project"],
        "capabilities": list(EXTERNAL_READ_CAPABILITIES),
        "timeout_seconds": 5,
    }
    value.update(updates)
    return value


def _scoped(request):
    return {
        "owner_ref": request["owner_ref"],
        "connection_id": request["connection_id"],
        "worker_ref": request["worker_ref"],
        "workspace_alias": request["workspace_alias"],
    }


def _wire(payload, status=200, headers=None):
    return status, headers or {"content-type": "application/json"}, json.dumps(payload).encode()


class SidecarFixture:
    def __init__(self):
        self.calls = []
        self.counter = 0
        self.failures = []

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
        if self.failures:
            failure = self.failures.pop(0)
            if isinstance(failure, Exception):
                raise failure
            return failure
        if method == "GET":
            return _wire({
                "protocol_version": EXTERNAL_AGENT_PROTOCOL,
                "envelope": "health",
                "message_id": self._message_id(),
                "request_id": "health-request",
                "issued_at": NOW_TEXT,
                "sidecar_id": "sidecar:fixture",
                "sidecar_version": "1.2.3",
                "protocol_compatible": True,
                "status": "healthy",
                "checked_at": NOW_TEXT,
            })
        if body["capability"] == "capabilities.read":
            return _wire({
                "protocol_version": EXTERNAL_AGENT_PROTOCOL,
                "envelope": "capabilities",
                "message_id": self._message_id(),
                "request_id": body["request_id"],
                "issued_at": NOW_TEXT,
                **_scoped(body),
                "sidecar_id": "sidecar:fixture",
                "sidecar_version": "1.2.3",
                "capabilities": [
                    {
                        "name": name,
                        "effect": "read",
                        "authorization": "explicit_request",
                        "reversible": True,
                        "enabled": True,
                    }
                    for name in EXTERNAL_READ_CAPABILITIES
                ],
            })
        results = {
            "agent.catalog": {
                "items": [{
                    "agent_ref": "agent:fixture",
                    "display_name": "Fixture agent",
                    "status": "available",
                }],
                "next_cursor": None,
            },
            "task.catalog": {
                "items": [{
                    "task_ref": "task:fixture-1234",
                    "agent_ref": "agent:fixture",
                    "title": "Inspect the project",
                    "status": "completed",
                    "updated_at": NOW_TEXT,
                }],
                "next_cursor": "next-page",
            },
            "task.events": {
                "items": [{
                    "protocol_version": EXTERNAL_AGENT_PROTOCOL,
                    "envelope": "event",
                    "message_id": self._message_id(),
                    "request_id": body["request_id"],
                    "issued_at": NOW_TEXT,
                    **_scoped(body),
                    "task_ref": body["arguments"].get("task_ref", "task:fixture-1234"),
                    "event_id": "event:fixture-0001",
                    "sequence": 1,
                    "event_type": "result",
                    "text": "token=hidden read /home/operator/private.txt",
                    "metadata": {},
                }],
                "next_cursor": None,
            },
            "task.transcript": {
                "items": [{
                    "message_id": "transcript:fixture-0001",
                    "role": "assistant",
                    "text": "Read completed.",
                    "created_at": NOW_TEXT,
                }],
                "next_cursor": None,
            },
        }
        return _wire({
            "protocol_version": EXTERNAL_AGENT_PROTOCOL,
            "envelope": "response",
            "message_id": self._message_id(),
            "request_id": body["request_id"],
            "issued_at": NOW_TEXT,
            **_scoped(body),
            "status": "succeeded",
            "result": results[body["capability"]],
        })


def _adapter(tmp_path, fixture=None, **updates):
    return ExternalAgentReadOnlyAdapter(
        _connection(tmp_path, **updates),
        requester=fixture or SidecarFixture(),
        resolver=lambda _host: ["93.184.216.34"],
        clock=lambda: NOW,
    )


def test_default_installation_registers_nothing_and_touches_no_secret_or_network(monkeypatch):
    monkeypatch.delenv("PANDAMONIUM_EXTERNAL_AGENT_CONNECTIONS_JSON", raising=False)
    monkeypatch.delenv("ODYSSEUS_EXTERNAL_AGENT_CONNECTIONS_JSON", raising=False)
    monkeypatch.setattr("src.external_agent_bridge._read_credential", lambda _ref: pytest.fail("credential read"))
    monkeypatch.setattr("src.external_agent_bridge._default_requester", lambda *_a, **_k: pytest.fail("network"))

    assert configured_external_agent_connections() == []
    assert set(worker_adapters.adapters(include_external=True)) == set(worker_adapters.WORKER_IDS)


def test_only_complete_explicit_versioned_configuration_is_registered(tmp_path, monkeypatch):
    config = _connection(tmp_path)
    monkeypatch.setenv("PANDAMONIUM_EXTERNAL_AGENT_CONNECTIONS_JSON", json.dumps([config]))

    registry = worker_adapters.adapters(include_external=True)
    catalog = worker_adapters.worker_catalog(registry)

    assert registry[config["id"]].adapter_name == "external-agent-sidecar"
    assert set(worker_adapters.adapters()) == set(worker_adapters.WORKER_IDS)
    assert catalog[config["id"]]["capabilities"] == ["read_only_inspection"]
    assert catalog[config["id"]]["workspaces"] == ["sample-project"]
    assert config["endpoint"] not in json.dumps(catalog)
    assert config["auth_ref"] not in json.dumps(catalog)

    for missing in ("protocol_version", "endpoint", "auth_ref"):
        invalid = dict(config)
        invalid.pop(missing)
        with pytest.raises(ExternalAgentBridgeError, match="connection_configuration_invalid"):
            configured_external_agent_connections(json.dumps([invalid]))

    reserved = dict(config, id="pc-codex")
    with pytest.raises(ExternalAgentBridgeError, match="connection_configuration_invalid"):
        configured_external_agent_connections(json.dumps([reserved]))


@pytest.mark.parametrize(
    "endpoint",
    [
        "file:///tmp/sidecar",
        "https://user:pass@sidecar.example/v1",
        "https://sidecar.example/v1?target=internal",
        "https://sidecar.example/v1#internal",
        "https://sidecar.example:bad/v1",
    ],
)
def test_endpoint_syntax_fails_closed(tmp_path, endpoint):
    with pytest.raises(ExternalAgentBridgeError, match="endpoint_invalid"):
        configured_external_agent_connections(json.dumps([_connection(tmp_path, endpoint=endpoint)]))


def test_network_policy_is_explicit_and_dns_result_is_pinned():
    with pytest.raises(ExternalAgentBridgeError, match="endpoint_policy_rejected"):
        _validated_external_agent_ips(
            "https://sidecar.example/v1",
            "public",
            resolver=lambda _host: ["10.0.0.7"],
        )
    private = _validated_external_agent_ips(
        "https://sidecar.example/v1",
        "private",
        resolver=lambda _host: ["10.0.0.7"],
    )
    loopback = _validated_external_agent_ips(
        "http://localhost:8042/v1",
        "loopback",
        resolver=lambda _host: ["127.0.0.1"],
    )
    assert [str(value) for value in private] == ["10.0.0.7"]
    assert [str(value) for value in loopback] == ["127.0.0.1"]


@pytest.mark.asyncio
async def test_discovery_and_all_reads_are_owner_workspace_bounded_and_redacted(tmp_path):
    fixture = SidecarFixture()
    adapter = _adapter(tmp_path, fixture)

    discovery = await adapter.discovery(owner="alice@example.com", workspace="sample-project")
    tasks = await adapter.catalog_tasks(
        owner="alice@example.com",
        workspace="sample-project",
        query="inspect",
        cursor="page-1",
        limit=5,
    )
    events = await adapter.task_events(
        "task:fixture-1234",
        owner="alice@example.com",
        workspace="sample-project",
        cursor=None,
        limit=5,
    )
    transcript = await adapter.task_transcript(
        "task:fixture-1234",
        owner="alice@example.com",
        workspace="sample-project",
        cursor=None,
        limit=5,
    )

    assert discovery["schema_version"] == "pandamonium.discovery.v1"
    assert {entity["kind"] for entity in discovery["entities"]} >= {"agent", "worker", "connection"}
    assert tasks["next_cursor"] == "next-page"
    assert transcript["items"][0]["text"] == "Read completed."
    assert "hidden" not in events["items"][0]["text"]
    assert "/home/" not in events["items"][0]["text"]
    serialized = json.dumps({"discovery": discovery, "tasks": tasks, "events": events})
    assert "alice@example.com" not in serialized
    assert "fixture-bearer" not in serialized
    assert "sidecar.example" not in serialized
    task_call = next(call for call in fixture.calls if (call["body"] or {}).get("capability") == "task.catalog")
    assert task_call["body"]["workspace_alias"] == "sample-project"
    assert task_call["body"]["arguments"] == {"query": "inspect", "cursor": "page-1", "limit": 5}
    assert task_call["pinned_ip"] == "93.184.216.34"
    assert task_call["headers"]["Authorization"] == "Bearer fixture-bearer"
    DISCOVERY_VALIDATOR.validate(discovery)
    for call in fixture.calls:
        if call["body"] is not None:
            WIRE_VALIDATOR.validate(call["body"])


@pytest.mark.asyncio
async def test_capability_intersection_is_scoped_per_owner(tmp_path):
    fixture = SidecarFixture()
    adapter = _adapter(tmp_path, fixture)

    await adapter.catalog_agents(owner="alice", workspace="sample-project", limit=10)
    await adapter.catalog_agents(owner="bob", workspace="sample-project", limit=10)

    capability_calls = [
        call for call in fixture.calls
        if (call["body"] or {}).get("capability") == "capabilities.read"
    ]
    assert len(capability_calls) == 2
    assert capability_calls[0]["body"]["owner_ref"] != capability_calls[1]["body"]["owner_ref"]


@pytest.mark.asyncio
async def test_wrong_workspace_is_rejected_before_credentials_or_network(tmp_path):
    fixture = SidecarFixture()
    adapter = _adapter(tmp_path, fixture)

    with pytest.raises(ExternalAgentBridgeError, match="wrong_workspace"):
        await adapter.catalog_agents(owner="alice", workspace="other-project", limit=10)

    assert fixture.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        ({"protocol_version": "other.v1"}, "incompatible_protocol"),
        ({"issued_at": (NOW - timedelta(minutes=10)).isoformat()}, "stale_request"),
        ({"owner_ref": "owner:mallory"}, "wrong_owner"),
        ({"workspace_alias": "other-project"}, "wrong_workspace"),
    ],
)
async def test_sidecar_envelope_mismatches_fail_closed(tmp_path, mutation, error):
    fixture = SidecarFixture()

    async def malformed(*args):
        method, _url, _headers, body, *_rest = args
        if method == "GET" or body["capability"] == "capabilities.read":
            return await fixture(*args)
        payload = {
            "protocol_version": EXTERNAL_AGENT_PROTOCOL,
            "envelope": "response",
            "message_id": "message-malformed",
            "request_id": body["request_id"],
            "issued_at": NOW_TEXT,
            **_scoped(body),
            "status": "succeeded",
            "result": {"items": [], "next_cursor": None},
        }
        payload.update(mutation)
        return _wire(payload)

    adapter = ExternalAgentReadOnlyAdapter(
        _connection(tmp_path),
        requester=malformed,
        resolver=lambda _host: ["93.184.216.34"],
        clock=lambda: NOW,
    )
    with pytest.raises(ExternalAgentBridgeError, match=error):
        await adapter.catalog_tasks(owner="alice", workspace="sample-project", limit=10)


@pytest.mark.asyncio
async def test_unauthorized_oversize_timeout_and_malformed_fail_stably(tmp_path):
    cases = [
        _wire({}, status=401),
        _wire({"too": "large"})[:-1] + (b"x" * 65_537,),
        asyncio.TimeoutError(),
        _wire({"not": "an envelope"}),
    ]
    expected = ["unauthorized", "oversized_payload", "timeout", "malformed_envelope"]
    for response, error in zip(cases, expected):
        fixture = SidecarFixture()
        fixture.failures = [response]
        adapter = _adapter(tmp_path, fixture)
        with pytest.raises(ExternalAgentBridgeError, match=error):
            await adapter.health(owner=None)


@pytest.mark.asyncio
async def test_redirect_is_not_followed_and_fails_with_a_stable_code(tmp_path):
    fixture = SidecarFixture()
    fixture.failures = [
        _wire({}, status=302, headers={"location": "http://127.0.0.1/admin"})
    ]
    adapter = _adapter(tmp_path, fixture)

    with pytest.raises(ExternalAgentBridgeError, match="sidecar_unavailable"):
        await adapter.catalog_tasks(owner="alice", workspace="sample-project", limit=10)

    assert len(fixture.calls) == 1


@pytest.mark.asyncio
async def test_replay_and_path_or_symlink_escape_fail_closed(tmp_path):
    fixture = SidecarFixture()
    adapter = _adapter(tmp_path, fixture)
    await adapter.catalog_tasks(owner="alice", workspace="sample-project", limit=10)
    replay = dict(fixture.calls[-1]["body"])

    async def repeated(method, url, headers, body, timeout, pinned_ip, max_bytes):
        if body["capability"] == "capabilities.read":
            return await fixture(method, url, headers, body, timeout, pinned_ip, max_bytes)
        return _wire({
            "protocol_version": EXTERNAL_AGENT_PROTOCOL,
            "envelope": "response",
            "message_id": "message-replayed",
            "request_id": body["request_id"],
            "issued_at": NOW_TEXT,
            **_scoped(body),
            "status": "succeeded",
            "result": {"items": [], "next_cursor": None},
        })

    replay_adapter = ExternalAgentReadOnlyAdapter(
        _connection(tmp_path), requester=repeated,
        resolver=lambda _host: ["93.184.216.34"], clock=lambda: NOW,
    )
    await replay_adapter.catalog_tasks(owner="alice", workspace="sample-project", limit=10)
    with pytest.raises(ExternalAgentBridgeError, match="replay_detected"):
        await replay_adapter.catalog_tasks(owner="alice", workspace="sample-project", limit=10)

    for code in ("path_escape", "symlink_escape"):
        async def escaped(method, url, headers, body, timeout, pinned_ip, max_bytes, code=code):
            if body["capability"] == "capabilities.read":
                return await fixture(method, url, headers, body, timeout, pinned_ip, max_bytes)
            return _wire({
                "protocol_version": EXTERNAL_AGENT_PROTOCOL,
                "envelope": "error",
                "message_id": f"message-{code}",
                "request_id": body["request_id"],
                "issued_at": NOW_TEXT,
                "code": code,
                "retryable": False,
                "detail": "The requested logical reference escaped its Workspace.",
            })
        escaped_adapter = ExternalAgentReadOnlyAdapter(
            _connection(tmp_path), requester=escaped,
            resolver=lambda _host: ["93.184.216.34"], clock=lambda: NOW,
        )
        with pytest.raises(ExternalAgentBridgeError, match=code):
            await escaped_adapter.catalog_tasks(owner="alice", workspace="sample-project", limit=10)


@pytest.mark.asyncio
async def test_disconnect_then_reconnect_is_explicit(tmp_path):
    fixture = SidecarFixture()
    fixture.failures = [httpx.ConnectError("offline")]
    adapter = _adapter(tmp_path, fixture)

    first = await adapter.health(owner=None)
    second = await adapter.health(owner=None)

    assert first["state"] == "unreachable"
    assert first["reason"] == "sidecar_unavailable"
    assert second["state"] == "connected"
    assert "offline" not in json.dumps(first)


@pytest.mark.asyncio
async def test_health_exposes_exact_configured_start_and_steer_actions(tmp_path):
    fixture = SidecarFixture()
    capabilities = [
        "task.events",
        {"name": "task.start", "effect": "reversible_write"},
        {"name": "task.status.read", "effect": "read"},
    ]
    adapter = _adapter(tmp_path, fixture, capabilities=capabilities)

    health = await adapter.health(owner=None)

    assert health["installation_capabilities"] == [
        "external_agent", "governed_task_actions", "task.start",
    ]
    assert "task.steer" not in health["installation_capabilities"]


@pytest.mark.asyncio
async def test_unavailable_discovery_is_canonical_and_does_not_continue(tmp_path):
    fixture = SidecarFixture()
    fixture.failures = [httpx.ConnectError("offline")]
    adapter = _adapter(tmp_path, fixture)

    discovery = await adapter.discovery(owner="alice", workspace="sample-project")

    DISCOVERY_VALIDATOR.validate(discovery)
    assert len(fixture.calls) == 1
    assert {entity["health"]["state"] for entity in discovery["entities"]} == {"unavailable"}
    assert all(entity["actions"] == [] for entity in discovery["entities"])


@pytest.mark.asyncio
async def test_unauthorized_capability_read_fails_closed(tmp_path):
    fixture = SidecarFixture()
    fixture.failures = [_wire({}, status=401)]
    adapter = _adapter(tmp_path, fixture)

    with pytest.raises(ExternalAgentBridgeError, match="unauthorized"):
        await adapter.catalog_tasks(owner="alice", workspace="sample-project", limit=10)


def test_credential_reference_rejects_symlinks_and_loose_permissions(tmp_path):
    target = tmp_path / "target"
    target.write_text("secret", encoding="utf-8")
    target.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(target)
    adapter = _adapter(tmp_path, auth_ref=f"file:{link}")
    with pytest.raises(ExternalAgentBridgeError, match="credential_unavailable"):
        adapter._headers()

    target.chmod(0o644)
    adapter = _adapter(tmp_path, auth_ref=f"file:{target}")
    with pytest.raises(ExternalAgentBridgeError, match="credential_unavailable"):
        adapter._headers()


def _route(path, method="GET"):
    router = agent_task_routes.setup_agent_task_routes(SimpleNamespace())
    return next(
        route.endpoint for route in router.routes
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set())
    )


@pytest.mark.asyncio
async def test_authenticated_routes_forward_owner_and_never_expose_actions(monkeypatch):
    calls = []

    class Adapter:
        adapter_name = "external-agent-sidecar"

        async def discovery(self, **values):
            calls.append(("discovery", values))
            return {"schema_version": "pandamonium.discovery.v1", "entities": []}

        async def catalog_tasks(self, **values):
            calls.append(("tasks", values))
            return {"items": [], "next_cursor": None}

        async def task_events(self, task_ref, **values):
            calls.append(("events", {"task_ref": task_ref, **values}))
            return {"items": [], "next_cursor": None}

        async def task_transcript(self, task_ref, **values):
            calls.append(("transcript", {"task_ref": task_ref, **values}))
            return {"items": [], "next_cursor": None}

    monkeypatch.setattr(
        agent_task_routes,
        "adapters",
        lambda **_kwargs: {"external-agent-a": Adapter()},
    )

    status_calls = []

    async def statuses(**values):
        status_calls.append(values)
        result = {"pc-codex": {"ready": True}}
        if values.get("include_external"):
            result["external-agent-a"] = {"ready": True}
        return result

    monkeypatch.setattr(agent_task_routes, "worker_statuses", statuses)

    established = await _route("/api/agent-workers")(_owner="alice")
    external = await _route("/api/external-agent-workers")(owner="alice")
    assert established == {"pc-codex": {"ready": True}}
    assert external == {"external-agent-a": {"ready": True}}
    assert status_calls == [
        {},
        {"owner": "alice", "include_external": True},
    ]

    await _route("/api/agent-workers/{worker}/discovery")(
        "external-agent-a", workspace="sample-project", owner="alice"
    )
    await _route("/api/agent-workers/{worker}/tasks")(
        "external-agent-a", workspace="sample-project", query="", cursor=None, limit=10, owner="alice"
    )
    await _route("/api/agent-workers/{worker}/tasks/{task_ref}/events")(
        "external-agent-a", "task:fixture-1234", workspace="sample-project", cursor=None, limit=10, owner="alice"
    )
    await _route("/api/agent-workers/{worker}/tasks/{task_ref}/transcript")(
        "external-agent-a", "task:fixture-1234", workspace="sample-project", cursor=None, limit=10, owner="alice"
    )

    assert all(values["owner"] == "alice" for _name, values in calls)
    assert not any("start" in getattr(route, "path", "") for route in agent_task_routes.setup_agent_task_routes(SimpleNamespace()).routes if "agent-workers/{worker}" in getattr(route, "path", ""))

    with pytest.raises(HTTPException) as exc:
        await _route("/api/agent-workers/{worker}/tasks")(
            "pc-codex", workspace="sample-project", query="", cursor=None, limit=10, owner="alice"
        )
    assert exc.value.status_code == 404


def test_bridge_adds_no_sdk_dependency_and_preserves_contributor_credit():
    root = Path(__file__).resolve().parents[1]
    requirements = (root / "requirements.txt").read_text(encoding="utf-8").lower()
    acknowledgments = (root / "ACKNOWLEDGMENTS.md").read_text(encoding="utf-8")
    assert "cursor-sdk" not in requirements
    assert "Twenty4SevenLabs/Pandamonium" in acknowledgments
    assert "7220f7cc9cbee26cd94697bd7a6a3d0ef001b66d" in acknowledgments
