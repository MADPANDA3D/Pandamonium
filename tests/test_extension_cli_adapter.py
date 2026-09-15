"""Real generated-process execution and native lifecycle, using disposable state."""

import asyncio
import copy
import json
import threading
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import src.extension_agent_mount as mounts
import src.extension_cli_adapter as cli
from src.authority_protocol import AuthorityStore
from src.extension_installer import ExtensionLifecycleManager
from src.extension_package import build_prepared_package, package_tree_digest
from src.extension_registry import ExtensionRegistry
from tests.test_extension_installer import _approve_and_execute
from tests.test_extension_semantic_intake import proposal, source_tree


def package(tmp_path):
    path = source_tree(tmp_path)
    data = proposal()
    name = "demo_tools__double_count"
    data["interfaces"][0]["name"] = name
    data["interfaces"][0]["tool_schema"]["function"]["name"] = name
    data["files"][0]["content"] = data["files"][0]["content"].replace(
        '"double_count"', f'"{name}"'
    )
    data["execution"] = {
        "install": [],
        "checks": [
            {"name": name, "arguments": {"count": 4}, "expected": {"result": 8}}
        ],
    }
    (path / ".pandamonium").mkdir()
    (path / ".pandamonium/adapter.py").write_text(data["files"][0]["content"])
    (path / ".pandamonium/integration.json").write_text(json.dumps(data))
    (path / "jarvis-extension.json").write_text(json.dumps(data["manifest"]))
    return path, data["manifest"]


def preview_package(manager, path, manifest, operation="install"):
    archive = manager.root.parent / "package.tar.gz"
    build_prepared_package(path, archive)
    content = archive.read_bytes()
    import hashlib

    return manager.preview_source(
        operation,
        manifest["source"]["url"],
        manifest["source"]["revision"],
        operator_id="operator",
        scan_id="test",
        scan_revision=manifest["source"]["revision"],
        draft_manifest=manifest,
        prepared_content=content,
        prepared_metadata={
            "id": "test",
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
            "tree_digest": package_tree_digest(path),
        },
    )


def test_real_cli_install_mount_owner_restart_disable_and_remove(tmp_path, monkeypatch):
    path, manifest = package(tmp_path)
    root = tmp_path / "manager"
    registry = ExtensionRegistry(tmp_path / "registry.json")
    authority = AuthorityStore(tmp_path / "authority.json")
    adapter = cli.GeneratedCliAdapter(root)
    manager = ExtensionLifecycleManager(
        root=root, registry=registry, authority=authority, adapters=[adapter]
    )
    plan = preview_package(manager, path, manifest)
    assert not (root / "runtimes").exists(), "preview must not execute generated code"
    _approve_and_execute(manager, authority, plan)
    monkeypatch.setattr(cli, "default_extensions_root", lambda: root)
    name = "demo_tools__double_count"
    spec = mounts.mount_extension_capabilities(registry, [name])["mounted"][0]
    assert spec["permission_mode"] == "external_side_effect"
    record = registry.snapshot()["extensions"]["demo-tools"]
    fresh = cli.GeneratedCliAdapter(root)
    assert fresh.execute(record, name, {"count": 7}, "operator", threading.Event()) == {
        "result": 14
    }
    with pytest.raises(Exception, match="owner_scope"):
        fresh.execute(record, name, {"count": 7}, "another-owner", threading.Event())
    with pytest.raises(Exception, match="Additional properties"):
        fresh.execute(
            record, name, {"count": 7, "shell": "bad"}, "operator", threading.Event()
        )
    monkeypatch.setattr(mounts, "ExtensionRegistry", lambda: registry)
    monkeypatch.setattr(cli, "default_extensions_root", lambda: root)
    result = asyncio.run(
        mounts.execute_mounted_extension_tool(spec, {"count": 9}, owner="operator")
    )
    assert result["result"] == {"result": 18}
    stale = {**spec, "permission_mode": "read_only"}
    assert (
        "remount"
        in asyncio.run(
            mounts.execute_mounted_extension_tool(stale, {"count": 9}, owner="operator")
        )["error"]
    )
    import routes.voice_routes as voice

    monkeypatch.setattr(voice, "extension_registry", registry)
    session = {"engaged_extensions": ["demo-tools"]}
    monkeypatch.setattr(voice, "_engaged_extension_ids", lambda _: ["demo-tools"])
    voice_specs = voice._extension_tool_specs(session)
    assert voice_specs[0]["cli_runtime"] is True
    assert (
        voice._extension_capability_policies(voice_specs)[name]["mounted_spec"] == spec
    )
    _, voice_result = asyncio.run(
        voice._extension_tool_executor(session, "operator", voice_specs)(
            SimpleNamespace(tool_type=name, content='{"count": 11}'), None
        )
    )
    assert voice_result["result"] == {"result": 22}
    # Real agent loop and dispatcher; only the model transport is deterministic.
    import src.agent_loop as agent
    import src.extension_registry as registry_module

    monkeypatch.setattr(registry_module, "ExtensionRegistry", lambda: registry)
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setattr(agent, "authority_store", authority)
    monkeypatch.setattr(agent, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(agent, "blocked_tools_for_owner", lambda _: set())
    monkeypatch.setattr(agent, "get_setting", lambda key, default=None: default)
    rounds = []

    async def model(*args, **kwargs):
        rounds.append(kwargs.get("tools", []))
        if len(rounds) <= 2:
            call = {
                "id": "cli-agent-" + str(len(rounds)),
                "name": "manage_extensions" if len(rounds) == 1 else name,
                "arguments": json.dumps(
                    {"action": "mount", "names": [name]}
                    if len(rounds) == 1
                    else {"count": 13}
                ),
            }
            yield (
                "data: " + json.dumps({"type": "tool_calls", "calls": [call]}) + "\n\n"
            )
        else:
            yield 'data: {"delta":"The plugin returned 26."}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(agent, "stream_llm_with_fallback", model)

    async def run_agent(approved_action=None):
        events = []
        async for chunk in agent.stream_agent_loop(
            "https://api.openai.com/v1",
            "gpt-4o",
            [{"role": "user", "content": "Use the demo-tools plugin to double 13."}],
            owner="operator",
            session_id="cli-session",
            relevant_tools={"manage_extensions"},
            max_rounds=4,
            workspace=str(tmp_path),
            approved_action=approved_action,
        ):
            if chunk.startswith("data: ") and chunk.strip() != "data: [DONE]":
                events.append(json.loads(chunk[6:]))
        return events

    first = asyncio.run(run_agent())
    pending = next(
        e["data"] for e in first if e.get("type") == "authority_approval_required"
    )
    resolved = authority.resolve_explicit_reply(
        pending["decision_id"],
        operator_id="operator",
        session_id="cli-session",
        choice="approve",
        scope="once",
    )
    resumed = asyncio.run(run_agent(resolved["pending_action"]))
    output = next(
        e for e in resumed if e.get("type") == "tool_output" and e.get("tool") == name
    )
    assert output["status"] == "succeeded", output
    assert "26" in json.dumps(output)
    # Installed manifest normalization changes its package digest; use its receipt.
    runtime = adapter._runtime(record["manifest"], package_tree_digest(next((root / "installed").rglob("jarvis-extension.json")).parent), "operator")
    saved = runtime.parent / "data/home/keep.txt"
    saved.write_text("owner data")
    installed_path = next((root / "installed").rglob("jarvis-extension.json")).parent
    adapter.validate_for_owner(
        installed_path, record["manifest"], manifest["source"]["revision"], "operator"
    )
    assert saved.read_text() == "owner data"
    original = (installed_path / ".pandamonium/adapter.py").read_text()
    (installed_path / ".pandamonium/adapter.py").write_text("print('{}')")
    assert (
        asyncio.run(
            mounts.execute_mounted_extension_tool(spec, {"count": 9}, owner="operator")
        )["exit_code"]
        == 1
    )
    (installed_path / ".pandamonium/adapter.py").write_text(original)
    old_revision = manager.snapshot()["extensions"]["demo-tools"]["active_revision"]
    manifest["version"] = "0.2.0"
    (path / "jarvis-extension.json").write_text(json.dumps(manifest))
    upgraded = json.loads((path / ".pandamonium/integration.json").read_text())
    upgraded["execution"]["checks"][0]["expected"] = {"result": 9}
    (path / ".pandamonium/integration.json").write_text(json.dumps(upgraded))
    (path / ".pandamonium/adapter.py").write_text(
        original.replace("int(result.stdout)", "int(result.stdout) + 1")
    )
    _approve_and_execute(
        manager, authority, preview_package(manager, path, manifest, "upgrade")
    )
    assert (
        "remount"
        in asyncio.run(
            mounts.execute_mounted_extension_tool(spec, {"count": 7}, owner="operator")
        )["error"]
    )
    new_spec = mounts.mount_extension_capabilities(registry, [name])["mounted"][0]
    assert asyncio.run(
        mounts.execute_mounted_extension_tool(new_spec, {"count": 7}, owner="operator")
    )["result"] == {"result": 15}
    upgraded["execution"]["checks"][0]["expected"] = {"result": 999}
    (path / ".pandamonium/integration.json").write_text(json.dumps(upgraded))
    with pytest.raises(Exception, match="operation_check_failed"):
        _approve_and_execute(
            manager, authority, preview_package(manager, path, manifest, "upgrade")
        )
    assert asyncio.run(
        mounts.execute_mounted_extension_tool(new_spec, {"count": 7}, owner="operator")
    )["result"] == {"result": 15}
    manager._rollback("demo-tools", old_revision, "operator")
    restored = mounts.mount_extension_capabilities(registry, [name])["mounted"][0]
    assert asyncio.run(
        mounts.execute_mounted_extension_tool(restored, {"count": 7}, owner="operator")
    )["result"] == {"result": 14}
    assert saved.read_text() == "owner data"
    manager._disable("demo-tools", "operator")
    assert (
        mounts.mount_extension_capabilities(registry, [name])["unavailable"][0]["error"]
        == "extension_disabled"
    )
    manager._enable("demo-tools", "operator")
    manager._uninstall("demo-tools", "operator")
    assert registry.snapshot()["extensions"] == {}
    assert list(cli.resources.storage_root().rglob("validated.json")), (
        "removal must preserve user runtime data"
    )
    assert saved.read_text() == "owner data"


def test_real_cli_confinement_timeout_output_and_schema_failure(tmp_path, monkeypatch):
    path, manifest = package(tmp_path)
    runtime = cli.resources.storage_root() / tmp_path.name / "runtime"
    runtime.mkdir(parents=True)
    monkeypatch.setenv("MAD959_HOST_SECRET", "must-not-enter-child")
    script = path / ".pandamonium/probe.py"
    script.write_text(
        "import json,os,pathlib\nassert 'MAD959_HOST_SECRET' not in os.environ\nassert not pathlib.Path('/home/leo/.ssh').exists()\nassert not pathlib.Path('/package/jarvis-extension.json').is_symlink()\ntry:\n pathlib.Path('/package/escape').write_text('no')\n raise AssertionError('writable source')\nexcept PermissionError: pass\nexcept OSError: pass\nprint('{}')\n"
    )
    assert (
        cli._run(path, runtime, manifest, ["python", "/package/.pandamonium/probe.py"])
        == b"{}\n"
    )
    for code, error in [
        ("import time; time.sleep(10)", "timeout"),
        ("print('x'*70000)", "output_limit"),
    ]:
        script.write_text(code)
        with pytest.raises(Exception, match=error):
            cli._run(
                path,
                runtime,
                manifest,
                ["python", "/package/.pandamonium/probe.py"],
                timeout=1,
            )
    script.write_text("import time; time.sleep(10)")
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Exception, match="cancelled"):
        cli._run(
            path,
            runtime,
            manifest,
            ["python", "/package/.pandamonium/probe.py"],
            cancel=cancel,
        )
    script.write_text(
        "from pathlib import Path\nassert not Path('/runtime/home/keep.txt').exists()\nPath('/runtime/home/health.txt').write_text('health')\nprint('{}')"
    )
    data = runtime.parent / "data/home"
    data.mkdir(parents=True, exist_ok=True)
    (data / "keep.txt").write_text("owner data")
    cli._run(
        path,
        runtime,
        manifest,
        ["python", "/package/.pandamonium/probe.py"],
        validation=True,
    )
    assert (data / "keep.txt").read_text() == "owner data"
    assert not (data / "health.txt").exists()
    data = json.loads((path / ".pandamonium/integration.json").read_text())
    invalid = copy.deepcopy(data)
    invalid["execution"]["checks"] = []
    with pytest.raises(ValidationError):
        cli.validate_cli_execution(manifest, invalid)
    (path / ".pandamonium/adapter.py").write_text('print(\'{"result":"wrong"}\')')
    with pytest.raises(Exception, match="not of type"):
        cli.GeneratedCliAdapter(tmp_path / "manager").validate_for_owner(
            path, manifest, manifest["source"]["revision"], "operator"
        )
