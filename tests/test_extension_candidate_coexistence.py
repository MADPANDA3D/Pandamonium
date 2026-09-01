"""MAD-755: prove the four reviewed candidate classes coexist and fail apart."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping

import pytest

from services.memory.skills import SkillsManager
from src.authority_protocol import AuthorityStore
from src.extension_host import ExtensionRuntimeHost, LiveCatalogWebAdapter
from src.extension_installer import (
    ExtensionLifecycleError,
    ExtensionLifecycleManager,
    GitSourceClient,
)
from src.extension_mcp_adapter import (
    McpExtensionAdapter,
    execute_mcp_extension_tool,
    mcp_extension_tool_specs,
)
from src.extension_registry import ExtensionRegistry
from src.extension_skill_adapter import SkillBundleAdapter
from src.mcp_manager import McpManager


CANDIDATES = {
    "barehands": (
        "https://github.com/MADPANDA3D/barehands.git",
        "0ef7c9a2f302f1fefe5b3fd9a56f987f4d8f1cff",
    ),
    "img2threejs": (
        "https://github.com/MADPANDA3D/img2threejs.git",
        "54734b5d307876753d0433f489497be5c8c32428",
    ),
    "text-to-cad": (
        "https://github.com/MADPANDA3D/text-to-cad.git",
        "fd444ccf5805f2c5ac451cc5794cf419a3676ed9",
    ),
    "robin": (
        "https://github.com/MADPANDA3D/robin.git",
        "8d4b4109f6928016f7976472309d2b7336b005b0",
    ),
}


def _candidate_roots() -> dict[str, Path]:
    value = os.environ.get("JOS_MAD_755_CANDIDATE_ROOT", "").strip()
    if not value:
        pytest.skip("set JOS_MAD_755_CANDIDATE_ROOT for the local MAD-755 proof")
    root = Path(value).expanduser().resolve()
    roots = {name: root / name for name in CANDIDATES}
    for name, path in roots.items():
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=path,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == CANDIDATES[name][1]
    return roots


def _git_client(roots: dict[str, Path]) -> GitSourceClient:
    local_sources = {
        CANDIDATES[name][0]: str(path) for name, path in roots.items()
    }

    def runner(
        argv: list[str],
        cwd: Path | None,
        timeout: int,
        environment: Mapping[str, str],
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [local_sources.get(item, item) for item in argv],
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


def _barehands_server(root: Path) -> tuple[ThreadingHTTPServer, threading.Thread]:
    spec = importlib.util.spec_from_file_location(
        "mad755_barehands_server", root / "server.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.CONFIG = {
        "name": "MAD-755 fixture",
        "jos_parent_origin": "http://127.0.0.1:7000",
        "orbs": [],
    }
    server = ThreadingHTTPServer(("127.0.0.1", 0), module.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


async def _approve(
    manager: ExtensionLifecycleManager,
    authority: AuthorityStore,
    plan: dict[str, Any],
) -> dict[str, Any]:
    decision = plan["authority_decision"]
    assert decision["decision"] == "approval_required"
    authority.resolve(
        decision["decision_id"],
        operator_id="operator",
        choice="approve",
        scope="once",
    )
    return await asyncio.to_thread(
        manager.execute_plan, plan["plan_id"], operator_id="operator"
    )


@pytest.mark.asyncio
async def test_four_repository_classes_coexist_and_one_failed_uninstall_isolated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roots = _candidate_roots()
    server, thread = _barehands_server(roots["barehands"])
    native = McpManager()
    skills = SkillsManager(str(tmp_path / "skills"))
    authority = AuthorityStore(tmp_path / "authority.json")
    registry = ExtensionRegistry(tmp_path / "registry.json")
    host = ExtensionRuntimeHost(
        {"barehands": f"http://127.0.0.1:{server.server_port}/"}
    )
    web_adapter = LiveCatalogWebAdapter(host)

    def mcp_config(reference: str) -> dict[str, object]:
        assert reference == "robin-runtime"
        return {
            "id": reference,
            "name": "Robin fixture runtime",
            "transport": "stdio",
            "command": sys.executable,
            "args": ["{entrypoint}"],
            "env": {
                "ROBIN_JOS_MODE": "fixture",
                "ROBIN_JOS_INVESTIGATIONS_DIR": str(tmp_path / "investigations"),
            },
            "url": None,
            "is_enabled": False,
        }

    mcp_adapter = McpExtensionAdapter(
        manager_provider=lambda: native,
        config_provider=mcp_config,
    )
    mcp_adapter.bind_loop(asyncio.get_running_loop())
    manager = ExtensionLifecycleManager(
        root=tmp_path / "managed",
        registry=registry,
        authority=authority,
        git_client=_git_client(roots),
        adapters=[web_adapter, SkillBundleAdapter(skills), mcp_adapter],
    )
    monkeypatch.setattr(
        "src.extension_installer.record_operational_event", lambda **_values: {}
    )

    try:
        for name, (source_url, revision) in CANDIDATES.items():
            plan = await asyncio.to_thread(
                manager.preview_source,
                "install",
                source_url,
                revision,
                operator_id="operator",
            )
            await _approve(manager, authority, plan)

        snapshot = registry.snapshot()["extensions"]
        assert set(snapshot) == set(CANDIDATES)
        assert {
            name: record["manifest"]["source"]["revision"]
            for name, record in snapshot.items()
        } == {name: values[1] for name, values in CANDIDATES.items()}
        assert registry.context_extensions(CANDIDATES) == {
            "barehands": {
                "engaged": True,
                "state_mounted": True,
                "tool_count": 2,
                "skill_count": 0,
            },
            "img2threejs": {
                "engaged": True,
                "state_mounted": True,
                "tool_count": 0,
                "skill_count": 1,
            },
            "robin": {
                "engaged": True,
                "state_mounted": True,
                "tool_count": 1,
                "skill_count": 0,
            },
            "text-to-cad": {
                "engaged": True,
                "state_mounted": True,
                "tool_count": 0,
                "skill_count": 2,
            },
        }
        assert {item["name"] for item in skills.load("operator")} == {
            "img2threejs",
            "cad",
            "cad-viewer",
        }

        robin = snapshot["robin"]
        assert [
            item["name"] for item in mcp_extension_tool_specs(robin, manager=native)
        ] == ["investigate_fixture"]
        result = await execute_mcp_extension_tool(
            robin,
            "investigate_fixture",
            {
                "query": "example organization credential exposure",
                "retain_evidence": False,
            },
            manager=native,
        )
        assert result["exit_code"] == 0, result
        assert json.loads(result["stdout"])["untrusted"] is True

        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        barehands = snapshot["barehands"]
        installed = (
            manager.root
            / "installed"
            / "barehands"
            / "revisions"
            / CANDIDATES["barehands"][1]
        )
        with pytest.raises(
            ExtensionLifecycleError, match="extension_catalog_unavailable"
        ):
            await asyncio.to_thread(
                web_adapter.validate,
                installed,
                barehands["manifest"],
                CANDIDATES["barehands"][1],
            )

        plan = await asyncio.to_thread(
            manager.preview_lifecycle,
            "uninstall",
            "barehands",
            operator_id="operator",
        )
        await _approve(manager, authority, plan)

        remaining = {"img2threejs", "text-to-cad", "robin"}
        assert set(manager.snapshot()["extensions"]) == remaining
        assert set(registry.snapshot()["extensions"]) == remaining
        assert {item["name"] for item in skills.load("operator")} == {
            "img2threejs",
            "cad",
            "cad-viewer",
        }
        robin = registry.snapshot()["extensions"]["robin"]
        result = await execute_mcp_extension_tool(
            robin,
            "investigate_fixture",
            {
                "query": "example organization credential exposure",
                "retain_evidence": False,
            },
            manager=native,
        )
        assert result["exit_code"] == 0, result
    finally:
        if thread.is_alive():
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        await native.disconnect_all()
