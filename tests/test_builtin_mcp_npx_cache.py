import asyncio
import importlib.util
from pathlib import Path
import subprocess
import sys
import types


ROOT = Path(__file__).resolve().parent.parent


def _load_builtin_mcp(monkeypatch):
    core = types.ModuleType("core")
    core.__path__ = []
    platform_compat = types.ModuleType("core.platform_compat")
    platform_compat.IS_WINDOWS = False
    platform_compat.which_tool = lambda name: None
    monkeypatch.setitem(sys.modules, "core", core)
    monkeypatch.setitem(sys.modules, "core.platform_compat", platform_compat)

    spec = importlib.util.spec_from_file_location(
        "builtin_mcp_under_test",
        ROOT / "src" / "builtin_mcp.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_npx_package_from_args_prefers_package_after_y_flag(monkeypatch):
    builtin_mcp = _load_builtin_mcp(monkeypatch)

    assert builtin_mcp._npx_package_from_args(
        ["-y", "@playwright/mcp@0.0.80", "--headless"]
    ) == "@playwright/mcp@0.0.80"


def test_npx_cache_check_detects_scoped_package_in_npx_cache(monkeypatch, tmp_path):
    builtin_mcp = _load_builtin_mcp(monkeypatch)
    package_json = (
        tmp_path
        / ".npm"
        / "_npx"
        / "9833c18b2d85bc59"
        / "node_modules"
        / "@playwright"
        / "mcp"
        / "package.json"
    )
    package_json.parent.mkdir(parents=True)
    package_json.write_text('{"name":"@playwright/mcp","version":"0.0.76"}', encoding="utf-8")

    async def unexpected_exec(*args, **kwargs):
        raise AssertionError("cache hit should not shell out to npx")

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("npm_config_cache", raising=False)
    monkeypatch.setattr(builtin_mcp.asyncio, "create_subprocess_exec", unexpected_exec)

    assert asyncio.run(
        builtin_mcp._is_npx_package_cached(
            "npx",
            "@playwright/mcp@0.0.80",
            timeout_s=2,
        )
    ) is True


def test_npx_cache_check_falls_back_when_async_subprocess_is_unsupported(monkeypatch, tmp_path):
    builtin_mcp = _load_builtin_mcp(monkeypatch)

    async def unsupported_exec(*args, **kwargs):
        raise NotImplementedError("subprocess transport unavailable")

    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(args, 0, stdout=b"1.2.3\n", stderr=b"")

    monkeypatch.setattr(builtin_mcp.asyncio, "create_subprocess_exec", unsupported_exec)
    monkeypatch.setattr(builtin_mcp.subprocess, "run", fake_run)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("npm_config_cache", raising=False)

    assert asyncio.run(
        builtin_mcp._is_npx_package_cached(
            "npx.cmd",
            "@playwright/mcp@0.0.80",
            timeout_s=2,
        )
    ) is True
    assert captured["args"] == [
        "npx.cmd",
        "--no-install",
        "@playwright/mcp@0.0.80",
        "--version",
    ]
    assert captured["kwargs"]["capture_output"] is True
    assert captured["kwargs"]["timeout"] == 2


def test_npx_cache_check_fallback_treats_timeout_as_cache_miss(monkeypatch, tmp_path):
    builtin_mcp = _load_builtin_mcp(monkeypatch)

    async def unsupported_exec(*args, **kwargs):
        raise NotImplementedError("subprocess transport unavailable")

    def fake_run(args, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setattr(builtin_mcp.asyncio, "create_subprocess_exec", unsupported_exec)
    monkeypatch.setattr(builtin_mcp.subprocess, "run", fake_run)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("npm_config_cache", raising=False)

    assert asyncio.run(
        builtin_mcp._is_npx_package_cached(
            "npx.cmd",
            "@playwright/mcp@0.0.80",
            timeout_s=2,
        )
    ) is False


def test_baked_browser_launch_passes_only_the_browser_store_environment(monkeypatch):
    builtin_mcp = _load_builtin_mcp(monkeypatch)
    captured = {}
    scheduled = []

    class Manager:
        async def connect_server(self, **kwargs):
            captured.update(kwargs)
            return True

    graphify_runtime = types.ModuleType("src.graphify_runtime")
    graphify_runtime.configured_roots = lambda: {}

    async def no_wait(_seconds):
        return None

    monkeypatch.setitem(sys.modules, "src.graphify_runtime", graphify_runtime)
    monkeypatch.setattr(builtin_mcp, "_BUILTIN_SERVERS", {})
    monkeypatch.setattr(builtin_mcp, "_spawn_bg", scheduled.append)
    monkeypatch.setattr(builtin_mcp.asyncio, "sleep", no_wait)
    monkeypatch.setattr(
        builtin_mcp.os.path,
        "isfile",
        lambda path: path == builtin_mcp._BROWSER_MCP_CLI,
    )
    monkeypatch.setattr(
        builtin_mcp,
        "which_tool",
        lambda name: "/usr/bin/node" if name == "node" else None,
    )

    async def exercise():
        await builtin_mcp.register_builtin_servers(Manager())
        assert len(scheduled) == 1
        await scheduled[0]

    asyncio.run(exercise())

    assert captured["command"] == "/usr/bin/node"
    assert captured["args"] == [
        builtin_mcp._BROWSER_MCP_CLI,
        *builtin_mcp._BROWSER_MCP_DOCKER_ARGS,
    ]
    assert captured["env"] == {
        "PLAYWRIGHT_BROWSERS_PATH": "/ms-playwright",
        "XDG_CACHE_HOME": "/app/.cache/browser-mcp",
    }
