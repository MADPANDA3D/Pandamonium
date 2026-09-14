"""MAD-838: governed Android emulator/ADB adapter contract tests.

Covers SDK resolution and fail-closed states, allowlisted command
construction, argument-injection and hostile-input rejection, explicit serial
targeting, timeouts and output bounds, cancellation, per-serial concurrency,
evidence capture, audit, the tool wrapper, and the non-admin/disabled gates.

Nothing here starts a real emulator or dials adb: command execution is either
a recorded fake or the real bounded runner pointed at a throwaway script.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from types import SimpleNamespace

import pytest

import src.android_emulator as android
from src.agent_tools.android_tools import AndroidDeviceTool

DEVICES_OUTPUT = """\
List of devices attached
emulator-5554          device product:sdk_gphone64_x86_64 model:Pixel_7_API_34 device:emu64x transport_id:1
R58M1234ABC            unauthorized usb:1-1 transport_id:2
192.168.1.5:5555       offline transport_id:3
"""

PROPS_OUTPUT = """\
[ro.product.model]: [Pixel_7_API_34]
[ro.build.version.release]: [14]
[ro.build.version.sdk]: [34]
[sys.boot_completed]: [1]
"""


def _proc(rc: int = 0, stdout="", stderr: str = "", truncated: bool = False, timed_out: bool = False):
    data = stdout.encode("utf-8") if isinstance(stdout, str) else bytes(stdout or b"")
    text = stdout if isinstance(stdout, str) else data.decode("utf-8", errors="replace")
    return android.AndroidProcessResult(
        returncode=rc,
        stdout=text,
        stderr=stderr,
        stdout_bytes=data,
        truncated=truncated,
        timed_out=timed_out,
    )


class FakeRunner:
    """Records commands and answers them from a per-test handler."""

    def __init__(self, handler):
        self.commands: list[android.AndroidCommand] = []
        self.handler = handler

    async def __call__(self, command, progress_cb=None):
        self.commands.append(command)
        return self.handler(command)

    @property
    def argv(self):
        return [command.argv for command in self.commands]


def _default_handler(command: android.AndroidCommand):
    argv = command.argv
    if argv[-2:] == ["devices", "-l"]:
        return _proc(stdout=DEVICES_OUTPUT)
    if argv[-1] == "sys.boot_completed":
        return _proc(stdout="1\n")
    if argv[-2:] == ["shell", "getprop"]:
        return _proc(stdout=PROPS_OUTPUT)
    if argv[-3:] == ["emu", "avd", "name"]:
        return _proc(stdout="Pixel_7_API_34\n")
    return _proc(stdout="ok\n")


@pytest.fixture(autouse=True)
def clean_state(tmp_path, monkeypatch):
    """No ambient SDK, isolated evidence/audit paths, clean serial registry."""
    android.reset_runtime_state()
    monkeypatch.setattr(android, "ANDROID_EVIDENCE_DIR", str(tmp_path / "evidence"))
    monkeypatch.setattr(android, "ANDROID_AUDIT_FILE", str(tmp_path / "android_audit.jsonl"))
    for name in android.SDK_ROOT_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv(android.AVD_ALLOWLIST_ENV, raising=False)
    monkeypatch.setattr(android, "_setting_value", lambda key: "")
    # Never resolve a developer machine's real adb/emulator through PATH.
    monkeypatch.setattr(android, "_which", lambda name: "")
    yield
    android.reset_runtime_state()


def _make_executable(path, body: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def sdk(tmp_path, monkeypatch):
    """A throwaway SDK tree with adb/emulator/sdkmanager executables."""
    root = tmp_path / "android-sdk"
    _make_executable(root / "platform-tools" / "adb", "#!/bin/sh\nexit 0\n")
    _make_executable(root / "emulator" / "emulator", "#!/bin/sh\necho Pixel_7_API_34\n")
    _make_executable(root / "cmdline-tools" / "latest" / "bin" / "sdkmanager", "#!/bin/sh\necho 1.0\n")
    monkeypatch.setenv("PANDAMONIUM_ANDROID_SDK_ROOT", str(root))
    return root


@pytest.fixture
def apk(tmp_path):
    path = tmp_path / "app-debug.apk"
    path.write_bytes(b"PK\x03\x04 fake apk")
    return str(path)


# ── SDK resolution and fail-closed states ────────────────────────────────

def test_status_without_sdk_is_honest():
    status = android.sdk_status()
    assert status["available"] is False
    assert status["adb"] == ""
    assert "PANDAMONIUM_ANDROID_SDK_ROOT" in status["message"]


def test_resolve_binary_prefers_sdk_root_over_path(sdk, monkeypatch):
    monkeypatch.setattr(android, "_which", lambda name: "/usr/bin/" + name)
    assert android.resolve_binary("adb") == str(sdk / "platform-tools" / "adb")
    assert android.resolve_binary("emulator") == str(sdk / "emulator" / "emulator")


def test_configured_but_missing_root_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv("PANDAMONIUM_ANDROID_SDK_ROOT", str(tmp_path / "nope"))
    assert android.resolve_sdk_root() == ""
    assert android.sdk_status()["available"] is False


async def test_status_action_reports_unavailable_without_error():
    result = await android.execute_android_operation("status", {}, audit=False)
    assert result["state"] == "unavailable"
    assert "No Android SDK is configured" in result["output"]


async def test_devices_without_sdk_fails_closed():
    with pytest.raises(android.AndroidAgentError) as exc:
        await android.execute_android_operation("devices", {})
    assert exc.value.code == "unavailable"


async def test_avds_without_emulator_fails_closed():
    with pytest.raises(android.AndroidAgentError) as exc:
        await android.execute_android_operation("avds", {})
    assert exc.value.code == "unavailable"


# ── discovery ────────────────────────────────────────────────────────────


async def test_devices_parses_state_model_versions_and_avd(sdk, monkeypatch):
    runner = FakeRunner(_default_handler)
    monkeypatch.setattr(android, "run_command", runner)
    result = await android.execute_android_operation("devices", {}, audit=False)
    devices = {entry["serial"]: entry for entry in result["devices"]}
    assert set(devices) == {"emulator-5554", "R58M1234ABC", "192.168.1.5:5555"}
    ready = devices["emulator-5554"]
    assert ready["state"] == "device"
    assert ready["model"] == "Pixel_7_API_34"
    assert ready["android"] == "14"
    assert ready["api"] == "34"
    assert ready["boot_completed"] is True
    assert ready["avd"] == "Pixel_7_API_34"
    assert devices["R58M1234ABC"]["authorized"] is False
    assert devices["192.168.1.5:5555"]["state"] == "offline"


async def test_list_avds_reads_emulator_output(sdk):
    assert android.list_avds() == ["Pixel_7_API_34"]


async def test_avds_reports_allowlist(sdk, monkeypatch):
    monkeypatch.setattr(android, "list_avds", lambda: ["Pixel_7_API_34", "Tablet_API_30"])
    monkeypatch.setenv(android.AVD_ALLOWLIST_ENV, "Pixel_7_API_34")
    result = await android.execute_android_operation("avds", {}, audit=False)
    assert result["avd_allowlist"] == ["Pixel_7_API_34"]
    flags = {entry["name"]: entry["allowed"] for entry in result["avds"]}
    assert flags == {"Pixel_7_API_34": True, "Tablet_API_30": False}


# ── explicit serial targeting ────────────────────────────────────────────


async def test_device_action_requires_serial(sdk):
    with pytest.raises(android.AndroidAgentError) as exc:
        await android.execute_android_operation("launch", {"package": "com.example.app"}, audit=False)
    assert exc.value.code == "serial_required"


@pytest.mark.parametrize(
    "serial",
    ["-s", "--serial", "emulator-5554; rm -rf /", "emu dev", "serial$(id)", "a" * 129, ""],
)
def test_hostile_serials_rejected(serial):
    with pytest.raises(android.AndroidAgentError):
        android.validate_serial(serial)


async def test_unknown_serial_fails_closed(sdk, monkeypatch):
    runner = FakeRunner(_default_handler)
    monkeypatch.setattr(android, "run_command", runner)
    with pytest.raises(android.AndroidAgentError) as exc:
        await android.execute_android_operation(
            "launch", {"serial": "emulator-9999", "package": "com.example.app"}, audit=False
        )
    assert exc.value.code == "device_not_found"


async def test_unauthorized_device_fails_closed(sdk, monkeypatch):
    runner = FakeRunner(_default_handler)
    monkeypatch.setattr(android, "run_command", runner)
    with pytest.raises(android.AndroidAgentError) as exc:
        await android.execute_android_operation(
            "screenshot", {"serial": "R58M1234ABC"}, audit=False
        )
    assert exc.value.code == "device_unauthorized"


async def test_offline_device_fails_closed(sdk, monkeypatch):
    runner = FakeRunner(_default_handler)
    monkeypatch.setattr(android, "run_command", runner)
    with pytest.raises(android.AndroidAgentError) as exc:
        await android.execute_android_operation(
            "screenshot", {"serial": "192.168.1.5:5555"}, audit=False
        )
    assert exc.value.code == "device_offline"


async def test_devices_empty_is_honest(sdk, monkeypatch):
    monkeypatch.setattr(
        android, "run_command", FakeRunner(lambda command: _proc(stdout="List of devices attached\n"))
    )
    result = await android.execute_android_operation("devices", {}, audit=False)
    assert result["devices"] == []
    assert "none" in result["output"].lower()


async def test_logcat_lines_are_clamped(sdk, monkeypatch):
    runner = FakeRunner(_default_handler)
    monkeypatch.setattr(android, "run_command", runner)
    await android.execute_android_operation(
        "logcat", {"serial": "emulator-5554", "lines": 99999}, audit=False
    )
    argv = runner.argv[-1]
    assert argv[argv.index("-t") + 1] == str(android.LOGCAT_MAX_LINES)


async def test_deep_link_package_is_optional(sdk, monkeypatch):
    runner = FakeRunner(_default_handler)
    monkeypatch.setattr(android, "run_command", runner)
    await android.execute_android_operation(
        "deep_link", {"serial": "emulator-5554", "url": "https://example.com/x"}, audit=False
    )
    assert "-p" not in runner.argv[-1]


# ── allowlisted command construction ─────────────────────────────────────


def test_plan_install_argv(sdk, apk):
    command = android.plan_install("emulator-5554", apk)
    assert command.argv == [
        android.resolve_binary("adb"),
        "-s",
        "emulator-5554",
        "install",
        "-r",
        apk,
    ]
    assert command.timeout == android.INSTALL_TIMEOUT_SECONDS


def test_plan_input_tap_and_swipe_and_key(sdk):
    tap = android.plan_input("emulator-5554", "tap", {"x": 10, "y": 20})
    assert tap.argv[-4:] == ["input", "tap", "10", "20"]
    swipe = android.plan_input(
        "emulator-5554", "swipe", {"x1": 1, "y1": 2, "x2": 3, "y2": 4, "duration_ms": 500}
    )
    assert swipe.argv[-7:] == ["input", "swipe", "1", "2", "3", "4", "500"]
    key = android.plan_input("emulator-5554", "key", {"key": "home"})
    assert key.argv[-2:] == ["keyevent", "KEYCODE_HOME"]


def test_plan_input_text_escapes_spaces(sdk):
    command = android.plan_input("emulator-5554", "text", {"text": "hello world"})
    assert command.argv[-2] == "text"
    assert command.argv[-1] == "'hello%sworld'"


def test_plan_deep_link_quotes_url(sdk):
    url = "https://example.com/path?a=1&b=2"
    command = android.plan_deep_link("emulator-5554", url, "com.example.app")
    assert command.argv[-2:] == ["-p", "'com.example.app'"]
    assert f"'{url}'" in command.argv
    assert command.argv[command.argv.index("-a") + 1] == "android.intent.action.VIEW"


def test_plan_logcat_is_dump_only_and_bounded(sdk):
    command = android.plan_logcat("emulator-5554", 99999, "ActivityManager")
    assert "-d" in command.argv
    assert command.argv[command.argv.index("-t") + 1] == "99999"  # clamped by the action
    assert command.argv[-2:] == ["-s", "ActivityManager"]


def test_plan_record_uses_fixed_values(sdk):
    command = android.plan_record("emulator-5554", "/sdcard/x.mp4", 30)
    assert command.argv[-4:] == ["screenrecord", "--time-limit", "30", "/sdcard/x.mp4"]


@pytest.mark.parametrize("avd", ["bad name", "avd;rm", "../avd", "", "a" * 65])
def test_hostile_avd_names_rejected(avd):
    with pytest.raises(android.AndroidAgentError):
        android.validate_avd(avd)


@pytest.mark.parametrize(
    "package",
    ["com.example;rm", "com.example app", "com", "$(id)", "com.example.app/Activity", ""],
)
def test_hostile_packages_rejected(package):
    with pytest.raises(android.AndroidAgentError):
        android.validate_package(package)


@pytest.mark.parametrize(
    "text",
    ["hello; rm -rf /", "a && b", "$(id)", "`id`", "a'b", "a\"b", "a|b", "a>b", "a%b"],
)
def test_hostile_input_text_rejected(text):
    with pytest.raises(android.AndroidAgentError):
        android.validate_input_text(text)


@pytest.mark.parametrize(
    "url",
    ["javascript:alert(1)", "https://x'; rm -rf /", "https://x\nInjected: 1", "notaurl",
     "https://x\\y", 'https://x"y'],
)
def test_hostile_urls_rejected(url):
    with pytest.raises(android.AndroidAgentError):
        android.validate_url(url)


@pytest.mark.parametrize("key", ["RM", "KEYCODE_BOOTLOADER", "1", "", "POWER;rm"])
def test_unknown_keys_rejected(key):
    with pytest.raises(android.AndroidAgentError):
        android.validate_key(key)


def test_coordinates_are_bounded(sdk):
    with pytest.raises(android.AndroidAgentError):
        android.plan_input("emulator-5554", "tap", {"x": 10001, "y": 0})
    with pytest.raises(android.AndroidAgentError):
        android.plan_input("emulator-5554", "tap", {"x": "1; rm", "y": 0})
    assert android.validate_key("KEYCODE_HOME") == "KEYCODE_HOME"


def test_apk_path_validation(sdk, tmp_path, apk):
    assert android.validate_apk_path(apk) == os.path.realpath(apk)
    for bad in ("relative.apk", "/nope/missing.apk", str(tmp_path / "file.txt"), ""):
        with pytest.raises(android.AndroidAgentError):
            android.validate_apk_path(bad)


# ── concurrency, timeout, cancellation ───────────────────────────────────


async def test_busy_serial_rejected(sdk, monkeypatch, apk):
    runner = FakeRunner(_default_handler)
    monkeypatch.setattr(android, "run_command", runner)
    lock = await android._acquire_serial("emulator-5554")
    try:
        with pytest.raises(android.AndroidAgentError) as exc:
            await android.execute_android_operation(
                "install", {"serial": "emulator-5554", "apk": apk}, audit=False
            )
        assert exc.value.code == "busy_serial"
        assert runner.commands == []
    finally:
        lock.release()


async def test_timeout_is_classified(sdk, monkeypatch):
    def handler(command):
        if command.argv[-2:] == ["devices", "-l"]:
            return _proc(stdout=DEVICES_OUTPUT)
        if command.argv[-2:] == ["shell", "getprop"]:
            return _proc(stdout=PROPS_OUTPUT)
        return _proc(rc=124, stderr="killed", timed_out=True)

    monkeypatch.setattr(android, "run_command", FakeRunner(handler))
    with pytest.raises(android.AndroidAgentError) as exc:
        await android.execute_android_operation(
            "logcat", {"serial": "emulator-5554"}, audit=False
        )
    assert exc.value.code == "timeout"


async def test_command_failure_redacts_stderr(sdk, monkeypatch):
    secret = "api_key=super-secret-value"

    def handler(command):
        argv = command.argv
        if argv[-2:] == ["devices", "-l"]:
            return _proc(stdout=DEVICES_OUTPUT)
        if argv[-2:] == ["shell", "getprop"]:
            return _proc(stdout=PROPS_OUTPUT)
        if argv[-3:] == ["emu", "avd", "name"]:
            return _proc(stdout="Pixel_7_API_34\n")
        return _proc(rc=1, stderr=secret)

    monkeypatch.setattr(android, "run_command", FakeRunner(handler))
    with pytest.raises(android.AndroidAgentError) as exc:
        await android.execute_android_operation(
            "force_stop", {"serial": "emulator-5554", "package": "com.example.app"}, audit=False
        )
    assert exc.value.code == "command_failed"
    assert "super-secret-value" not in exc.value.message


async def test_wait_ready(sdk, monkeypatch):
    monkeypatch.setattr(android, "run_command", FakeRunner(_default_handler))
    result = await android.execute_android_operation(
        "wait", {"serial": "emulator-5554", "timeout": 5}, audit=False
    )
    assert result["state"] == "ready"


async def test_wait_is_cancellable(sdk, monkeypatch):
    monkeypatch.setattr(android, "BOOT_POLL_SECONDS", 0.01)

    def handler(command):
        if command.argv[-2:] == ["devices", "-l"]:
            return _proc(stdout=DEVICES_OUTPUT)
        if command.argv[-2:] == ["shell", "getprop"]:
            return _proc(stdout=PROPS_OUTPUT)
        return _proc(stdout="0\n")

    monkeypatch.setattr(android, "run_command", FakeRunner(handler))
    task = asyncio.create_task(
        android.execute_android_operation(
            "wait", {"serial": "emulator-5554", "timeout": 30}, audit=False
        )
    )
    await asyncio.sleep(0.1)
    assert android.cancel_wait("emulator-5554") is True
    with pytest.raises(android.AndroidAgentError) as exc:
        await task
    assert exc.value.code == "cancelled"


async def test_cancel_without_wait_is_honest_noop():
    result = await android.execute_android_operation(
        "cancel", {"serial": "emulator-5554"}, audit=False
    )
    assert result["state"] == "noop"
    assert result["cancelled"] is False


async def test_run_command_timeout_kills_process(tmp_path):
    script = tmp_path / "sleepy.py"
    script.write_text("import time\ntime.sleep(10)\n", encoding="utf-8")
    command = android.AndroidCommand([sys.executable, str(script)], timeout=1)
    started = time.monotonic()
    result = await android.run_command(command)
    assert result.timed_out is True and result.returncode == 124
    assert time.monotonic() - started < 5


async def test_run_command_bounds_output(tmp_path):
    script = tmp_path / "chatty.py"
    script.write_text("import sys\nsys.stdout.write('x' * 100000)\n", encoding="utf-8")
    command = android.AndroidCommand([sys.executable, str(script)], timeout=10, max_bytes=1000)
    started = time.monotonic()
    result = await android.run_command(command)
    assert result.truncated is True
    assert len(result.stdout_bytes) == 1000
    # Over-ceiling stdout is stopped directly, not by waiting out the timeout.
    assert result.timed_out is False
    assert time.monotonic() - started < 5


async def test_run_command_cancellation_kills_process(tmp_path):
    script = tmp_path / "sleepy.py"
    script.write_text("import time\ntime.sleep(10)\n", encoding="utf-8")
    command = android.AndroidCommand([sys.executable, str(script)], timeout=30)
    task = asyncio.create_task(android.run_command(command))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ── AVD lifecycle ────────────────────────────────────────────────────────


async def test_start_rejects_unknown_avd(sdk, monkeypatch):
    monkeypatch.setattr(android, "list_avds", lambda: ["Pixel_7_API_34"])
    with pytest.raises(android.AndroidAgentError) as exc:
        await android.execute_android_operation(
            "start", {"avd": "Tablet_API_30"}, audit=False
        )
    assert exc.value.code == "avd_not_installed"


async def test_start_rejects_avd_outside_allowlist(sdk, monkeypatch):
    monkeypatch.setattr(android, "list_avds", lambda: ["Pixel_7_API_34"])
    monkeypatch.setenv(android.AVD_ALLOWLIST_ENV, "Other_AVD")
    with pytest.raises(android.AndroidAgentError) as exc:
        await android.execute_android_operation(
            "start", {"avd": "Pixel_7_API_34"}, audit=False
        )
    assert exc.value.code == "avd_not_allowed"


async def test_start_launches_detached_and_reports_pid(sdk, monkeypatch, tmp_path):
    monkeypatch.setattr(android, "list_avds", lambda: ["Pixel_7_API_34"])
    # Replace the fixture's fast emulator with one that stays up briefly.
    emulator = sdk / "emulator" / "emulator"
    _make_executable(emulator, "#!/bin/sh\nexec sleep 2\n")
    result = await android.execute_android_operation(
        "start", {"avd": "Pixel_7_API_34", "headless": True}, audit=False
    )
    assert result["state"] == "starting"
    assert result["pid"] > 0
    assert os.path.exists(result["log_path"])
    try:
        os.kill(result["pid"], 15)
    except (ProcessLookupError, PermissionError):
        pass


async def test_start_reports_immediate_emulator_failure(sdk, monkeypatch):
    monkeypatch.setattr(android, "list_avds", lambda: ["Pixel_7_API_34"])
    emulator = sdk / "emulator" / "emulator"
    _make_executable(emulator, "#!/bin/sh\necho 'PANIC: broken avd' >&2\nexit 2\n")
    with pytest.raises(android.AndroidAgentError) as exc:
        await android.execute_android_operation(
            "start", {"avd": "Pixel_7_API_34"}, audit=False
        )
    assert exc.value.code == "emulator_failed"
    assert "PANIC" in exc.value.message


async def test_stop_and_reboot_target_explicit_serial(sdk, monkeypatch):
    runner = FakeRunner(_default_handler)
    monkeypatch.setattr(android, "run_command", runner)
    stop = await android.execute_android_operation(
        "stop", {"serial": "emulator-5554"}, audit=False
    )
    assert stop["state"] == "ok"
    assert runner.argv[-1] == [
        android.resolve_binary("adb"),
        "-s",
        "emulator-5554",
        "emu",
        "kill",
    ]
    reboot = await android.execute_android_operation(
        "reboot", {"serial": "emulator-5554"}, audit=False
    )
    assert reboot["state"] == "rebooting"


# ── developer actions and evidence ───────────────────────────────────────


async def test_install_runs_after_target_resolution(sdk, monkeypatch, apk):
    seen = []

    def handler(command):
        seen.append(command.argv)
        return _default_handler(command)

    monkeypatch.setattr(android, "run_command", FakeRunner(handler))
    result = await android.execute_android_operation(
        "install", {"serial": "emulator-5554", "apk": apk}, audit=False
    )
    assert result["state"] == "ok"
    assert seen[0][-2:] == ["devices", "-l"]
    assert seen[-1][-5:] == ["-s", "emulator-5554", "install", "-r", apk]


async def test_install_failure_is_honest(sdk, monkeypatch, apk):
    def handler(command):
        if command.argv[-2:] == ["devices", "-l"]:
            return _proc(stdout=DEVICES_OUTPUT)
        if command.argv[-2:] == ["shell", "getprop"]:
            return _proc(stdout=PROPS_OUTPUT)
        if command.argv[-3:] == ["emu", "avd", "name"]:
            return _proc(stdout="Pixel_7_API_34\n")
        return _proc(rc=1, stdout="Failure [INSTALL_FAILED_INVALID_APK]")

    monkeypatch.setattr(android, "run_command", FakeRunner(handler))
    with pytest.raises(android.AndroidAgentError) as exc:
        await android.execute_android_operation(
            "install", {"serial": "emulator-5554", "apk": apk}, audit=False
        )
    assert exc.value.code == "install_failed"


async def test_deep_link_and_input_after_ready_check(sdk, monkeypatch):
    runner = FakeRunner(_default_handler)
    monkeypatch.setattr(android, "run_command", runner)
    await android.execute_android_operation(
        "deep_link",
        {"serial": "emulator-5554", "url": "https://example.com/x?a=1&b=2"},
        audit=False,
    )
    assert "am" in runner.argv[-1] and "android.intent.action.VIEW" in runner.argv[-1]
    await android.execute_android_operation(
        "input", {"serial": "emulator-5554", "input": "tap", "x": 5, "y": 6}, audit=False
    )
    assert runner.argv[-1][-4:] == ["input", "tap", "5", "6"]


async def test_screenshot_saves_bounded_evidence(sdk, monkeypatch):
    png = b"\x89PNG\r\n\x1a\n" + b"fake-image-bytes"

    def handler(command):
        if command.argv[-2:] == ["devices", "-l"]:
            return _proc(stdout=DEVICES_OUTPUT)
        if command.argv[-2:] == ["shell", "getprop"]:
            return _proc(stdout=PROPS_OUTPUT)
        if command.argv[-3:] == ["emu", "avd", "name"]:
            return _proc(stdout="Pixel_7_API_34\n")
        return _proc(stdout=png)

    monkeypatch.setattr(android, "run_command", FakeRunner(handler))
    result = await android.execute_android_operation(
        "screenshot", {"serial": "emulator-5554"}, audit=False
    )
    evidence = result["evidence"]
    assert evidence["bytes"] == len(png)
    assert os.path.isfile(evidence["path"])
    assert open(evidence["path"], "rb").read() == png


async def test_screenshot_oversize_fails_closed(sdk, monkeypatch):
    def handler(command):
        if command.argv[-2:] == ["devices", "-l"]:
            return _proc(stdout=DEVICES_OUTPUT)
        if command.argv[-2:] == ["shell", "getprop"]:
            return _proc(stdout=PROPS_OUTPUT)
        if command.argv[-3:] == ["emu", "avd", "name"]:
            return _proc(stdout="Pixel_7_API_34\n")
        return _proc(stdout=b"x" * 100, truncated=True)

    monkeypatch.setattr(android, "run_command", FakeRunner(handler))
    with pytest.raises(android.AndroidAgentError) as exc:
        await android.execute_android_operation(
            "screenshot", {"serial": "emulator-5554"}, audit=False
        )
    assert exc.value.code == "output_too_large"


async def test_record_pulls_evidence_and_cleans_up(sdk, monkeypatch):
    mp4 = b"\x00\x00\x00\x18ftypmp42 fake-video"

    def handler(command):
        argv = command.argv
        if argv[-2:] == ["devices", "-l"]:
            return _proc(stdout=DEVICES_OUTPUT)
        if argv[-2:] == ["shell", "getprop"]:
            return _proc(stdout=PROPS_OUTPUT)
        if argv[-3:] == ["emu", "avd", "name"]:
            return _proc(stdout="Pixel_7_API_34\n")
        if "pull" in argv:
            # Simulate the pulled file on disk.
            open(argv[-1], "wb").write(mp4)
            return _proc(stdout="1 file pulled")
        return _proc(stdout="Recording\n")

    runner = FakeRunner(handler)
    monkeypatch.setattr(android, "run_command", runner)
    result = await android.execute_android_operation(
        "record", {"serial": "emulator-5554", "seconds": 3}, audit=False
    )
    assert any("screenrecord" in command.argv for command in runner.commands)
    assert any("pull" in command.argv for command in runner.commands)
    assert any("rm" in command.argv for command in runner.commands)
    assert result["evidence"]["bytes"] == len(mp4)
    assert open(result["evidence"]["path"], "rb").read() == mp4


async def test_record_cleans_up_remote_after_pull_failure(sdk, monkeypatch):
    def handler(command):
        argv = command.argv
        if argv[-2:] == ["devices", "-l"]:
            return _proc(stdout=DEVICES_OUTPUT)
        if argv[-2:] == ["shell", "getprop"]:
            return _proc(stdout=PROPS_OUTPUT)
        if argv[-3:] == ["emu", "avd", "name"]:
            return _proc(stdout="Pixel_7_API_34\n")
        if "pull" in argv:
            return _proc(rc=1, stderr="adb: error: failed to copy")
        return _proc(stdout="Recording\n")

    runner = FakeRunner(handler)
    monkeypatch.setattr(android, "run_command", runner)
    with pytest.raises(android.AndroidAgentError):
        await android.execute_android_operation(
            "record", {"serial": "emulator-5554", "seconds": 3}, audit=False
        )
    assert any("rm" in command.argv for command in runner.commands)


async def test_logcat_truncates_and_rejects_bad_tag(sdk, monkeypatch):
    monkeypatch.setattr(android, "run_command", FakeRunner(_default_handler))
    with pytest.raises(android.AndroidAgentError) as exc:
        await android.execute_android_operation(
            "logcat", {"serial": "emulator-5554", "tag": "bad tag;rm"}, audit=False
        )
    assert exc.value.code == "invalid_tag"


async def test_banned_actions_do_not_exist():
    for banned in ("uninstall", "clear_data", "wipe", "shell", "pm"):
        with pytest.raises(android.AndroidAgentError) as exc:
            await android.execute_android_operation(banned, {}, audit=False)
        assert exc.value.code == "invalid_action"


def test_adapter_has_no_arbitrary_shell_surface():
    source = open(android.__file__, encoding="utf-8").read()
    assert "shell=True," not in source
    assert "shell = True" not in source
    assert "os.system" not in source
    tool_source = open(
        os.path.join(os.path.dirname(android.__file__), "agent_tools", "android_tools.py"),
        encoding="utf-8",
    ).read()
    assert "subprocess" not in tool_source
    assert "shell=True" not in tool_source


# ── audit ────────────────────────────────────────────────────────────────


async def test_audit_records_success_and_failure(sdk, monkeypatch, tmp_path, apk):
    runner = FakeRunner(_default_handler)
    monkeypatch.setattr(android, "run_command", runner)
    await android.execute_android_operation("devices", {}, owner="leo")
    with pytest.raises(android.AndroidAgentError):
        await android.execute_android_operation(
            "install", {"serial": "emulator-5554", "apk": "/nope/missing.apk"}, owner="leo"
        )
    lines = [
        json.loads(line)
        for line in (tmp_path / "android_audit.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [entry["state"] for entry in lines] == ["ok", "apk_not_found"]
    assert all(entry["actor"] == "leo" for entry in lines)
    assert lines[0]["action"] == "devices"


# ── tool wrapper and registration gates ──────────────────────────────────


async def test_tool_wrapper_success_shape(sdk, monkeypatch):
    monkeypatch.setattr(android, "run_command", FakeRunner(_default_handler))
    tool = AndroidDeviceTool()
    result = await tool.execute('{"action": "devices"}', {"owner": "leo"})
    assert result["exit_code"] == 0
    assert result["source"]["kind"] == "android_device"
    assert result["citation"] == "android devices"
    assert result["limits"]["max_output_bytes"] > 0
    assert result["devices"]


async def test_tool_wrapper_maps_errors(sdk):
    tool = AndroidDeviceTool()
    result = await tool.execute('{"action": "launch", "package": "com.example.app"}', {})
    assert result["exit_code"] == 1
    assert result["code"] == "serial_required"


async def test_tool_wrapper_rejects_unknown_action_and_bad_args():
    tool = AndroidDeviceTool()
    assert (await tool.execute('{"action": "rm_rf"}', {}))["code"] == "invalid_action"
    assert (await tool.execute("[]", {}))["code"] == "invalid_arguments"


async def test_tool_wrapper_defaults_to_status(sdk, monkeypatch):
    monkeypatch.setattr(android, "run_command", FakeRunner(_default_handler))
    tool = AndroidDeviceTool()
    result = await tool.execute("{}", {})
    assert result["exit_code"] == 0
    assert "Android SDK" in result["output"]


def test_android_device_is_admin_only():
    from src.tool_security import NON_ADMIN_BLOCKED_TOOLS, is_public_blocked_tool

    assert "android_device" in NON_ADMIN_BLOCKED_TOOLS
    assert is_public_blocked_tool("android_device") is True


async def test_non_admin_execution_is_blocked(monkeypatch):
    import src.tool_execution as te

    monkeypatch.setattr(te, "owner_is_admin_or_single_user", lambda owner: False)
    block = SimpleNamespace(tool_type="android_device", content='{"action": "devices"}')
    _, result = await te.execute_tool_block(block, owner="bob")
    assert result["exit_code"] == 1
    assert "restricted to admin users" in result["error"]


async def test_disabling_the_tool_changes_nothing_else(monkeypatch):
    import src.tool_execution as te
    from src.agent_tools import TOOL_HANDLERS

    block = SimpleNamespace(tool_type="android_device", content='{"action": "status"}')
    _, result = await te.execute_tool_block(
        block, owner="admin", disabled_tools={"android_device"}
    )
    assert "disabled by user" in result["error"]
    # Rollback is scoped: the adapter registration is additive and the
    # pre-existing governed tools remain registered.
    for name in ("ssh_node", "nextcloud_files", "read_file", "manage_settings"):
        assert name in TOOL_HANDLERS


# ── prompt / selection integration ───────────────────────────────────────


def test_android_domain_and_rules_are_wired():
    import src.agent_loop as al

    assert al._DOMAIN_TOOL_MAP["android"] == {"android_device"}
    rules = al._domain_rules_for_tools({"android_device"})
    assert any("Android device rules" in rule for rule in rules)
    section = al.tool_prompt_sections({"android_device"})
    assert "android_device" in section and "deep_link" in section


def test_android_intent_detection_seeds_the_domain():
    import src.agent_loop as al

    intent = al._classify_agent_request(
        [{"role": "user", "content": "start the android emulator"}],
        "start the android emulator",
    )
    assert "android" in intent["domains"]
    intent = al._classify_agent_request(
        [{"role": "user", "content": "what's the weather?"}], "what's the weather?"
    )
    assert "android" not in intent["domains"]


def test_action_intent_routes_android_requests():
    from src.action_intents import classify_tool_intent

    assert classify_tool_intent("start the android emulator").category == "android"
    assert classify_tool_intent("install app-debug.apk on the emulator").category == "android"


def test_native_function_call_converts_to_tool_block():
    from src.tool_schemas import function_call_to_tool_block

    block = function_call_to_tool_block(
        "android_device", json.dumps({"action": "devices"})
    )
    assert block is not None and block.tool_type == "android_device"
    assert json.loads(block.content) == {"action": "devices"}


def test_android_device_schema_and_catalog_present():
    from src.tool_catalog import builtin_tool_names, category_for
    from src.tool_index import BUILTIN_TOOL_DESCRIPTIONS
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS

    names = {schema["function"]["name"] for schema in FUNCTION_TOOL_SCHEMAS}
    assert "android_device" in names
    assert "android_device" in builtin_tool_names()
    assert category_for("android_device") == "System"
    assert len(BUILTIN_TOOL_DESCRIPTIONS["android_device"]) > 50
