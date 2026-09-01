import asyncio
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.authority_protocol import AuthorityStore
from src.extension_installer import ExtensionLifecycleError, ExtensionLifecycleManager, GitSourceClient
from src.extension_mcp_adapter import (
    EXTENSION_MCP_MAX_RESULT_BYTES,
    McpExtensionAdapter,
    execute_mcp_extension_tool,
    mcp_extension_tool_specs,
)
from src.extension_registry import ExtensionContractError, ExtensionRegistry, validate_extension_manifest
from src.mcp_manager import McpManager


SOURCE_URL = "https://github.com/example/quartz-extension.git"


def _run(argv, *, cwd=None):
    return subprocess.run(
        argv,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    ).stdout.strip()


def _manifest(version: str, tool: str) -> dict:
    return {
        "protocol_version": "jos-extension.v1",
        "extension_id": "quartz",
        "name": "Quartz Fixture",
        "version": version,
        "source": {"url": SOURCE_URL, "revision": "self"},
        "runtime": {"type": "mcp", "entrypoint": "server.py"},
        "capabilities": {"descriptor": {"type": "mcp", "reference": "quartz-runtime"}},
        "permissions": {"default": "read_only", "capabilities": {tool: "read_only"}},
        "health": {"type": "catalog", "timeout_seconds": 2},
        "lifecycle": {"install": [], "start": [], "stop": [], "remove": []},
        "data_boundaries": {"read": [], "write": [], "network": []},
        "removal": {"remove_paths": [], "preserve_paths": []},
        "rollback": {"strategy": "pinned_revision", "retain_revisions": 3},
    }


def _commit(repo: Path, manifest: dict, tag: str) -> str:
    (repo / "jarvis-extension.json").write_text(json.dumps(manifest), encoding="utf-8")
    (repo / "server.py").write_text("# reference-neutral MCP fixture\n", encoding="utf-8")
    tool = next(iter(manifest["permissions"]["capabilities"]))
    (repo / "catalog.json").write_text(json.dumps({"tool": tool}), encoding="utf-8")
    _run(["git", "add", "jarvis-extension.json", "server.py", "catalog.json"], cwd=repo)
    _run(["git", "commit", "-m", f"fixture {manifest['version']}"], cwd=repo)
    revision = _run(["git", "rev-parse", "HEAD"], cwd=repo)
    _run(["git", "tag", tag], cwd=repo)
    return revision


@pytest.fixture
def git_fixture(tmp_path):
    repo = tmp_path / "source"
    repo.mkdir()
    _run(["git", "init", "-b", "main"], cwd=repo)
    _run(["git", "config", "user.email", "fixture@example.test"], cwd=repo)
    _run(["git", "config", "user.name", "Fixture"], cwd=repo)
    v1 = _commit(repo, _manifest("1.0.0", "inspect_crystal"), "v1")
    v2 = _commit(repo, _manifest("2.0.0", "shape_crystal"), "v2")
    return repo, v1, v2


def _mapped_git(repo: Path) -> GitSourceClient:
    def runner(argv, cwd, timeout, environment):
        return subprocess.run(
            [str(repo) if item == SOURCE_URL else item for item in argv],
            cwd=str(cwd) if cwd else None,
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )

    return GitSourceClient(runner=runner, check_public_urls=False)


class FakeMcpManager:
    def __init__(self):
        self.reserved = set()
        self.statuses = {}
        self.tools = {}
        self.connects = []
        self.calls = []
        self.available = True
        self.identity = "quartz"
        self.catalog_override = None
        self.duplicate_tools = False

    def reserve_extension_server(self, server_id):
        self.reserved.add(server_id)

    def release_extension_server(self, server_id):
        self.reserved.discard(server_id)

    def is_extension_server(self, server_id):
        return server_id in self.reserved

    async def connect_server(self, server_id, name, transport, command=None, args=None, env=None, url=None, **kwargs):
        self.connects.append({
            "server_id": server_id,
            "transport": transport,
            "command": command,
            "args": list(args or []),
            "env": dict(env or {}),
            "url": url,
            **kwargs,
        })
        if not self.available:
            self.statuses[server_id] = {"status": "error"}
            return False
        entrypoint = Path(next(item for item in [command, *(args or [])] if item and item.endswith("server.py")))
        revision = _run(["git", "rev-parse", "HEAD"], cwd=entrypoint.parent)
        tool = self.catalog_override or json.loads((entrypoint.parent / "catalog.json").read_text())["tool"]
        self.tools[server_id] = [{
            "name": tool,
            "description": f"Run {tool}",
            "input_schema": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
        }]
        if self.duplicate_tools:
            self.tools[server_id].append(dict(self.tools[server_id][0]))
        self.statuses[server_id] = {
            "status": "connected",
            "transport": transport,
            "server_info": {"name": self.identity, "version": revision},
        }
        return True

    async def disconnect_server(self, server_id):
        self.statuses.pop(server_id, None)
        self.tools.pop(server_id, None)

    def get_server_status(self, server_id):
        return self.statuses.get(server_id, {"status": "disconnected"})

    def get_server_tools(self, server_id):
        return list(self.tools.get(server_id, []))

    async def call_tool(self, qualified_name, arguments, **limits):
        self.calls.append((qualified_name, arguments, limits))
        return {"stdout": json.dumps({"value": arguments["value"]}), "stderr": "", "exit_code": 0}


def _config(_reference):
    return {
        "id": "quartz-runtime",
        "name": "Quartz runtime",
        "transport": "stdio",
        "command": sys.executable,
        "args": ["{entrypoint}"],
        "env": {"FIXTURE_TOKEN": "secret-value"},
        "url": None,
        "is_enabled": False,
    }


def _manager(tmp_path: Path, repo: Path, fake: FakeMcpManager):
    authority = AuthorityStore(tmp_path / "authority.json")
    registry = ExtensionRegistry(tmp_path / "registry.json")
    adapter = McpExtensionAdapter(manager_provider=lambda: fake, config_provider=_config)
    manager = ExtensionLifecycleManager(
        root=tmp_path / "managed",
        registry=registry,
        authority=authority,
        git_client=_mapped_git(repo),
        adapters=[adapter],
    )
    return manager, authority, registry, adapter


async def _approve_and_execute(manager, authority, plan):
    decision = plan["authority_decision"]
    authority.resolve(decision["decision_id"], operator_id="operator", choice="approve", scope="once")
    return await asyncio.to_thread(manager.execute_plan, plan["plan_id"], operator_id="operator")


async def _source(manager, authority, operation, ref):
    plan = await asyncio.to_thread(
        manager.preview_source, operation, SOURCE_URL, ref, operator_id="operator"
    )
    return await _approve_and_execute(manager, authority, plan)


async def _lifecycle(manager, authority, operation, *, target=None):
    plan = await asyncio.to_thread(
        manager.preview_lifecycle,
        operation,
        "quartz",
        operator_id="operator",
        target_revision=target,
    )
    return await _approve_and_execute(manager, authority, plan)


def test_mcp_runtime_and_descriptor_must_pair():
    manifest = _manifest("1.0.0", "inspect_crystal")
    manifest["capabilities"] = {
        "descriptor": {"type": "openapi", "endpoint": "/openapi.json"}
    }
    with pytest.raises(ExtensionContractError, match="extension_runtime_descriptor_mismatch"):
        validate_extension_manifest(manifest)


async def test_reference_neutral_stdio_lifecycle_action_and_secret_boundary(tmp_path, git_fixture):
    repo, v1, v2 = git_fixture
    fake = FakeMcpManager()
    manager, authority, registry, adapter = _manager(tmp_path, repo, fake)
    adapter.bind_loop(asyncio.get_running_loop())

    await _source(manager, authority, "install", "v1")
    record = registry.snapshot()["extensions"]["quartz"]
    specs = mcp_extension_tool_specs(record, manager=fake)
    assert [item["name"] for item in specs] == ["inspect_crystal"]
    result = await execute_mcp_extension_tool(record, "inspect_crystal", {"value": "one"}, manager=fake)
    assert result["exit_code"] == 0
    assert fake.calls[-1] == (
        "mcp__quartz-runtime__inspect_crystal",
        {"value": "one"},
        {"timeout_seconds": 2, "max_output_bytes": EXTENSION_MCP_MAX_RESULT_BYTES},
    )

    await _lifecycle(manager, authority, "disable")
    assert mcp_extension_tool_specs(registry.snapshot()["extensions"]["quartz"], manager=fake) == []
    await _lifecycle(manager, authority, "enable")
    await _source(manager, authority, "upgrade", "v2")
    assert set(registry.effective_capabilities()) == {"shape_crystal"}
    await _lifecycle(manager, authority, "rollback", target=v1)
    assert set(registry.effective_capabilities()) == {"inspect_crystal"}
    await _lifecycle(manager, authority, "uninstall")
    assert registry.snapshot()["extensions"] == {}
    await _source(manager, authority, "install", "v2")
    assert set(registry.effective_capabilities()) == {"shape_crystal"}

    assert any(call["transport"] == "stdio" and call["args"][0].endswith("server.py") for call in fake.connects)
    assert all(call.get("identity_from_env") is False for call in fake.connects)
    persisted = (tmp_path / "registry.json").read_text() + (tmp_path / "managed" / "lifecycle.json").read_text()
    assert "secret-value" not in persisted


async def test_drift_identity_unavailable_and_restart_fail_closed(tmp_path, git_fixture):
    repo, _v1, _v2 = git_fixture
    fake = FakeMcpManager()
    manager, authority, registry, adapter = _manager(tmp_path, repo, fake)
    adapter.bind_loop(asyncio.get_running_loop())

    plan = await asyncio.to_thread(
        manager.preview_source, "install", SOURCE_URL, "v1", operator_id="operator"
    )
    fake.catalog_override = "drifted_tool"
    with pytest.raises(ExtensionLifecycleError, match="extension_mcp_catalog_malformed"):
        await _approve_and_execute(manager, authority, plan)
    assert registry.snapshot()["extensions"] == {}

    fake.catalog_override = None
    fake.identity = "wrong-identity"
    with pytest.raises(ExtensionLifecycleError, match="extension_mcp_identity_mismatch"):
        await asyncio.to_thread(
            manager.preview_source, "install", SOURCE_URL, "v1", operator_id="operator"
        )
    fake.identity = "quartz"
    fake.duplicate_tools = True
    with pytest.raises(ExtensionLifecycleError, match="extension_mcp_duplicate_tools"):
        await asyncio.to_thread(
            manager.preview_source, "install", SOURCE_URL, "v1", operator_id="operator"
        )
    fake.duplicate_tools = False
    fake.available = False
    with pytest.raises(ExtensionLifecycleError, match="extension_mcp_unavailable"):
        await asyncio.to_thread(
            manager.preview_source, "install", SOURCE_URL, "v1", operator_id="operator"
        )

    fake.available = True
    await _source(manager, authority, "install", "v1")
    record = registry.snapshot()["extensions"]["quartz"]

    drifted_restart = FakeMcpManager()
    drifted_restart.identity = "wrong-identity"
    failed_restore = McpExtensionAdapter(
        manager_provider=lambda: drifted_restart, config_provider=_config
    )
    assert await failed_restore.restore_enabled(registry=registry, root=manager.root) == {
        "quartz": False
    }
    assert mcp_extension_tool_specs(record, manager=drifted_restart) == []

    restarted = FakeMcpManager()
    restored = McpExtensionAdapter(manager_provider=lambda: restarted, config_provider=_config)
    assert await restored.restore_enabled(registry=registry, root=manager.root) == {
        "quartz": True
    }
    assert [item["name"] for item in mcp_extension_tool_specs(record, manager=restarted)] == ["inspect_crystal"]


async def test_engaged_mcp_extension_routes_through_existing_voice_executor(
    tmp_path, git_fixture, monkeypatch
):
    from routes import voice_routes
    from src.tool_utils import get_mcp_manager, set_mcp_manager

    repo, _v1, _v2 = git_fixture
    fake = FakeMcpManager()
    manager, authority, registry, adapter = _manager(tmp_path, repo, fake)
    adapter.bind_loop(asyncio.get_running_loop())
    await _source(manager, authority, "install", "v1")
    previous = get_mcp_manager()
    set_mcp_manager(fake)
    monkeypatch.setattr(voice_routes, "extension_registry", registry)
    try:
        session = {
            "id": "voice-quartz",
            "target": "jarvis",
            "engaged_extensions": ["quartz"],
        }
        specs = voice_routes._extension_tool_specs(session)
        assert [(item["name"], item["permission_mode"]) for item in specs] == [
            ("inspect_crystal", "read_only")
        ]
        executor = voice_routes._extension_tool_executor(session, "operator", specs)

        async def progress(_payload):
            raise AssertionError("native MCP actions must not use the browser result bridge")

        description, result = await executor(
            SimpleNamespace(tool_type="inspect_crystal", content='{"value":"routed"}'),
            progress,
        )
        assert description == "QUARTZ inspect_crystal"
        assert result["exit_code"] == 0
        assert fake.calls[-1][0] == "mcp__quartz-runtime__inspect_crystal"
    finally:
        set_mcp_manager(previous)


async def test_native_manager_reserves_extension_tools_and_bounds_untrusted_results():
    manager = McpManager()
    manager._tools["quartz-runtime"] = [{
        "name": "inspect_crystal",
        "description": "Inspect",
        "input_schema": {"type": "object", "properties": {}},
    }]
    manager._connections["quartz-runtime"] = {"status": "connected", "name": "Quartz"}
    assert manager.get_all_openai_schemas()
    manager.reserve_extension_server("quartz-runtime")
    assert manager.get_all_openai_schemas() == []
    assert "inspect_crystal" not in manager.get_tool_descriptions_for_prompt()

    class Session:
        def __init__(self, value):
            self.value = value

        async def call_tool(self, _name, _arguments):
            if self.value == "late":
                await asyncio.sleep(1)
            if self.value == "malformed":
                return SimpleNamespace(content=None, isError=False)
            return SimpleNamespace(
                content=[SimpleNamespace(text=self.value, type="text")], isError=False
            )

    manager._sessions["quartz-runtime"] = Session("x" * 20)
    oversized = await manager.call_tool(
        "mcp__quartz-runtime__inspect_crystal", {}, max_output_bytes=10
    )
    assert oversized["exit_code"] == 1 and "too large" in oversized["error"].lower()
    manager._sessions["quartz-runtime"] = Session("malformed")
    malformed = await manager.call_tool("mcp__quartz-runtime__inspect_crystal", {})
    assert malformed["exit_code"] == 1 and "malformed" in malformed["error"].lower()
    manager._sessions["quartz-runtime"] = Session("late")
    timeout = await manager.call_tool(
        "mcp__quartz-runtime__inspect_crystal", {}, timeout_seconds=0.01
    )
    assert timeout["exit_code"] == 1 and "timed out" in timeout["error"].lower()


async def test_native_stdio_connection_teardown_stays_with_owner_task(caplog):
    manager = McpManager()
    server = Path(__file__).parent / "fixtures" / "quartz_mcp_server.py"
    caplog.set_level("WARNING")
    connected = await asyncio.wait_for(
        manager.connect_server(
            server_id="quartz-runtime",
            name="Quartz runtime",
            transport="stdio",
            command=sys.executable,
            args=[str(server)],
            env={},
            identity_from_env=False,
        ),
        timeout=3,
    )
    assert connected
    result = await manager.call_tool("mcp__quartz-runtime__inspect_crystal", {})
    assert result == {"stdout": "quartz-ok", "stderr": "", "exit_code": 0}
    await manager.disconnect_server("quartz-runtime")
    assert not [
        record
        for record in caplog.records
        if "different task" in record.getMessage().lower()
    ]


@pytest.mark.parametrize(
    "config",
    [
        {**_config("x"), "is_enabled": True},
        {**_config("x"), "args": ["server.py"]},
        {**_config("x"), "command": "{entrypoint}", "args": ["value"] * 65},
        {**_config("x"), "transport": "http", "command": None, "args": [], "url": "https://example.test/mcp"},
    ],
)
async def test_dual_exposure_unpinned_stdio_and_remote_transport_are_rejected(tmp_path, git_fixture, config):
    repo, _v1, _v2 = git_fixture
    fake = FakeMcpManager()
    authority = AuthorityStore(tmp_path / "authority.json")
    adapter = McpExtensionAdapter(manager_provider=lambda: fake, config_provider=lambda _ref: config)
    adapter.bind_loop(asyncio.get_running_loop())
    manager = ExtensionLifecycleManager(
        root=tmp_path / "managed",
        registry=ExtensionRegistry(tmp_path / "registry.json"),
        authority=authority,
        git_client=_mapped_git(repo),
        adapters=[adapter],
    )
    with pytest.raises(ExtensionLifecycleError):
        await asyncio.to_thread(
            manager.preview_source, "install", SOURCE_URL, "v1", operator_id="operator"
        )
