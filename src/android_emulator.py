"""Governed Android emulator and ADB control (MAD-838).

This module is the policy + execution core behind the ``android_device`` agent
tool. It lets the agent discover the installation's Android SDK, inspect
connected emulators/devices, run bounded AVD lifecycle operations, and perform
explicit developer actions (install/launch/stop, deep links, screenshots,
screen recordings, scoped logcat, tap/swipe/text/key input) without ever
exposing a free-form shell.

Security contract:

* Every command is an argv list built server-side from a validated, typed
  argument. There is no ``shell=True``, no caller-supplied command line, and no
  path to ``adb shell <arbitrary>``. Free-text leaves (URL, input text, logcat
  tag) are POSIX-quoted for the device shell after charset validation.
* The adapter is admin-only at the tool layer. Destructive device operations
  (uninstall, clear-data, wipe, ``pm``/``am`` escalation, credential or Play
  Store automation) have no implementation here at all — not even a disabled
  branch — so a prompt-injected model cannot reach them through this surface.
* Device actions require an explicit serial. The serial must be present in
  ``adb devices`` output and in a ready state; unauthorized/offline/missing
  devices fail closed with honest copy.
* One serial is one critical section: concurrent commands against the same
  serial are rejected with ``busy_serial`` instead of racing.
* Every command is bounded by a hard timeout and a hard output ceiling; on
  timeout or caller cancellation the child process is killed.
* Every attempt is audited (action, state, serial, actor, bounds) with
  redacted, bounded detail. Private paths and raw command lines are never
  logged.
* The SDK location is installation-owned (env vars, then Settings). No
  personal path is hardcoded and no Android SDK is required in the base image;
  a missing SDK fails closed.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from core.platform_compat import safe_chmod
from src.constants import ANDROID_AUDIT_FILE, ANDROID_EVIDENCE_DIR

logger = logging.getLogger(__name__)

# ── installation-owned configuration ─────────────────────────────────────
#
# Resolution order is explicit env (installation-owned) → Settings value →
# PATH. No user-specific directory is named in code.
SDK_ROOT_ENV_VARS = (
    "PANDAMONIUM_ANDROID_SDK_ROOT",
    "ODYSSEUS_ANDROID_SDK_ROOT",
    "ANDROID_SDK_ROOT",
    "ANDROID_HOME",
)
AVD_ALLOWLIST_ENV = "PANDAMONIUM_ANDROID_AVD_ALLOWLIST"
SDK_ROOT_SETTING = "android_sdk_root"
AVD_ALLOWLIST_SETTING = "android_avd_allowlist"

# ── actions and bounds ───────────────────────────────────────────────────

ACTIONS = (
    "status",
    "devices",
    "avds",
    "start",
    "stop",
    "reboot",
    "wait",
    "install",
    "launch",
    "force_stop",
    "deep_link",
    "screenshot",
    "record",
    "logcat",
    "input",
    "cancel",
)
INPUT_KINDS = ("tap", "swipe", "text", "key")
READ_ACTIONS = frozenset({"status", "devices", "avds", "wait", "logcat", "screenshot"})
DEVICE_ACTIONS = frozenset(
    {
        "stop",
        "reboot",
        "wait",
        "install",
        "launch",
        "force_stop",
        "deep_link",
        "screenshot",
        "record",
        "logcat",
        "input",
    }
)

ADB_TIMEOUT_SECONDS = 30
INSTALL_TIMEOUT_SECONDS = 240
RECORD_MAX_SECONDS = 180
BOOT_WAIT_DEFAULT_SECONDS = 120
BOOT_WAIT_MIN_SECONDS = 5
BOOT_WAIT_MAX_SECONDS = 600
BOOT_POLL_SECONDS = 2.0
LOGCAT_MAX_LINES = 2000
LOGCAT_DEFAULT_LINES = 200
INPUT_TEXT_MAX_CHARS = 200
URL_MAX_CHARS = 2048
APK_PATH_MAX_CHARS = 1024
TEXT_MAX_BYTES = 64 * 1024
LIST_MAX_BYTES = 128 * 1024
SCREENSHOT_MAX_BYTES = 24 * 1024 * 1024
RECORD_MAX_BYTES = 256 * 1024 * 1024
COORD_MAX = 10000
SWIPE_DURATION_MAX_MS = 10000

# The only key events the adapter will send. Short aliases map to their
# KEYCODE_* form; an exact KEYCODE_* value from this map is also accepted.
ALLOWED_KEYCODES: dict[str, str] = {
    "HOME": "KEYCODE_HOME",
    "BACK": "KEYCODE_BACK",
    "ENTER": "KEYCODE_ENTER",
    "DEL": "KEYCODE_DEL",
    "FORWARD_DEL": "KEYCODE_FORWARD_DEL",
    "TAB": "KEYCODE_TAB",
    "SPACE": "KEYCODE_SPACE",
    "ESCAPE": "KEYCODE_ESCAPE",
    "MENU": "KEYCODE_MENU",
    "APP_SWITCH": "KEYCODE_APP_SWITCH",
    "POWER": "KEYCODE_POWER",
    "WAKEUP": "KEYCODE_WAKEUP",
    "SLEEP": "KEYCODE_SLEEP",
    "VOLUME_UP": "KEYCODE_VOLUME_UP",
    "VOLUME_DOWN": "KEYCODE_VOLUME_DOWN",
    "VOLUME_MUTE": "KEYCODE_VOLUME_MUTE",
    "CAMERA": "KEYCODE_CAMERA",
    "NOTIFICATION": "KEYCODE_NOTIFICATION",
    "SEARCH": "KEYCODE_SEARCH",
    "CLEAR": "KEYCODE_CLEAR",
    "PAGE_UP": "KEYCODE_PAGE_UP",
    "PAGE_DOWN": "KEYCODE_PAGE_DOWN",
    "MOVE_HOME": "KEYCODE_MOVE_HOME",
    "MOVE_END": "KEYCODE_MOVE_END",
    "DPAD_UP": "KEYCODE_DPAD_UP",
    "DPAD_DOWN": "KEYCODE_DPAD_DOWN",
    "DPAD_LEFT": "KEYCODE_DPAD_LEFT",
    "DPAD_RIGHT": "KEYCODE_DPAD_RIGHT",
    "DPAD_CENTER": "KEYCODE_DPAD_CENTER",
}
_ALLOWED_KEYCODE_VALUES = frozenset(ALLOWED_KEYCODES.values())

# ── validation patterns ──────────────────────────────────────────────────

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
# Serials come from adb (e.g. emulator-5554, 192.168.1.5:5555, R58M1234ABC).
# A leading dash is rejected so a serial can never become an adb flag.
_SERIAL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_AVD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,62}(?:\.[A-Za-z][A-Za-z0-9_]{0,62})+$")
_LOGCAT_TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
# Input text is typed through the device shell; keep it to characters that are
# inert there (no quotes, %, &, ;, |, $, backticks, redirection). Spaces are
# converted to %s, the documented `input text` separator.
_INPUT_TEXT_RE = re.compile(r"^[A-Za-z0-9 .,_@:+/#()\[\]-]{0,200}$")
# Deep links are device-shell quoted, so common URL punctuation is allowed;
# whitespace, quotes, backslash, and control characters are not.
_URL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]{1,31}://[^\s'\"\\\x00-\x1f\x7f]{1,2048}$")


class AndroidAgentError(RuntimeError):
    """Governed adapter failure; callers map this to honest copy."""

    def __init__(self, code: str, message: str, *, reason: str = ""):
        super().__init__(message)
        self.code = str(code or "failed")
        self.message = str(message or "")
        self.reason = str(reason or self.code)


# ── SDK resolution ───────────────────────────────────────────────────────


def _env_value(name: str) -> str:
    return str(os.getenv(name) or "").strip()


def _setting_value(key: str) -> str:
    try:
        from src.settings import get_setting

        return str(get_setting(key, "") or "").strip()
    except Exception:
        return ""


def resolve_sdk_root() -> str:
    """The configured Android SDK root, or "" when none is usable.

    Installation-owned: explicit env first, then the ``android_sdk_root``
    setting. A configured-but-missing directory resolves to "" so callers fail
    closed instead of running against a stale path.
    """
    for name in SDK_ROOT_ENV_VARS:
        candidate = _env_value(name)
        if candidate:
            return candidate if os.path.isdir(candidate) else ""
    configured = _setting_value(SDK_ROOT_SETTING)
    if configured and os.path.isdir(configured):
        return configured
    return ""


def _sdk_candidates(root: str) -> dict[str, list[str]]:
    return {
        "adb": [
            os.path.join(root, "platform-tools", "adb"),
            os.path.join(root, "platform-tools", "adb.exe"),
        ],
        "emulator": [
            os.path.join(root, "emulator", "emulator"),
            os.path.join(root, "emulator", "emulator.exe"),
        ],
        "sdkmanager": [
            os.path.join(root, "cmdline-tools", "latest", "bin", "sdkmanager"),
            os.path.join(root, "cmdline-tools", "latest", "bin", "sdkmanager.bat"),
            os.path.join(root, "cmdline-tools", "bin", "sdkmanager"),
            os.path.join(root, "tools", "bin", "sdkmanager"),
        ],
    }


def _which(name: str) -> str:
    """PATH fallback so a native install on the workstation resolves."""
    found = shutil.which(name)
    return found or ""


def resolve_binary(name: str) -> str:
    """Resolve one SDK binary (adb/emulator/sdkmanager) to an absolute path."""
    if name not in ("adb", "emulator", "sdkmanager"):
        return ""
    root = resolve_sdk_root()
    if root:
        for candidate in _sdk_candidates(root).get(name, []):
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
    return _which(name)


def configured_avd_allowlist() -> list[str]:
    """Operator allowlist of startable AVD names ("" = any installed AVD).

    Env wins over the Settings value; both accept a comma/newline list. Names
    are validated with the same pattern as the AVD argument so a malformed
    allowlist entry is ignored instead of widening the surface.
    """
    raw = _env_value(AVD_ALLOWLIST_ENV) or _setting_value(AVD_ALLOWLIST_SETTING)
    if not raw:
        return []
    names: list[str] = []
    for item in re.split(r"[\n,]", raw):
        name = str(item or "").strip()
        if name and _AVD_RE.fullmatch(name) and name not in names:
            names.append(name)
    return names[:64]


def sdk_status() -> dict[str, Any]:
    """Redacted SDK resolution snapshot for the `status` action."""
    root = resolve_sdk_root()
    adb = resolve_binary("adb")
    emulator = resolve_binary("emulator")
    sdkmanager = resolve_binary("sdkmanager")
    configured = bool(root)
    available = bool(adb)
    if available:
        message = "The Android SDK toolchain is available."
    elif configured or any(_env_value(name) for name in SDK_ROOT_ENV_VARS):
        message = (
            "The configured Android SDK root does not contain a usable adb. "
            "Check the path and that platform-tools is installed."
        )
    else:
        message = (
            "No Android SDK is configured. Set PANDAMONIUM_ANDROID_SDK_ROOT "
            "(or the android_sdk_root setting) to the SDK directory that "
            "contains platform-tools/adb, then retry."
        )
    return {
        "available": available,
        "sdk_root": root,
        "adb": adb,
        "emulator": emulator,
        "sdkmanager": sdkmanager,
        "avd_allowlist": configured_avd_allowlist(),
        "message": message,
    }


# ── argument validation ──────────────────────────────────────────────────


def _normalized(value: Any) -> str:
    return " ".join(str(value if value is not None else "").split())


def validate_serial(value: Any) -> str:
    serial = str(value or "").strip()
    if not serial:
        raise AndroidAgentError(
            "serial_required",
            "Name the target device serial explicitly (see the devices action).",
        )
    if len(serial) > 128 or not _SERIAL_RE.fullmatch(serial) or _CONTROL_RE.search(serial):
        raise AndroidAgentError(
            "invalid_serial",
            "The device serial may use letters, numbers, dot, dash, underscore, and colon only.",
        )
    return serial


def validate_avd(value: Any) -> str:
    name = str(value or "").strip()
    if not name or len(name) > 64 or not _AVD_RE.fullmatch(name):
        raise AndroidAgentError(
            "invalid_avd",
            "The AVD name may use letters, numbers, dot, dash, and underscore only.",
        )
    return name


def validate_package(value: Any) -> str:
    package = str(value or "").strip()
    if not package or len(package) > 255 or not _PACKAGE_RE.fullmatch(package):
        raise AndroidAgentError(
            "invalid_package",
            "Enter a full Android package name, e.g. com.example.app.",
        )
    return package


def validate_apk_path(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw or len(raw) > APK_PATH_MAX_CHARS or _CONTROL_RE.search(raw):
        raise AndroidAgentError("invalid_apk", "Enter the absolute path to one .apk file.")
    if not os.path.isabs(raw):
        raise AndroidAgentError("invalid_apk", "The APK path must be absolute.")
    if not raw.lower().endswith(".apk"):
        raise AndroidAgentError("invalid_apk", "The file must be an .apk.")
    resolved = os.path.realpath(raw)
    if not os.path.isfile(resolved):
        raise AndroidAgentError(
            "apk_not_found",
            f"No APK exists at {resolved}. Put the build artifact on the machine "
            "that runs the adapter, then retry.",
        )
    return resolved


def validate_url(value: Any) -> str:
    url = str(value or "").strip()
    if not url or len(url) > URL_MAX_CHARS or not _URL_RE.fullmatch(url):
        raise AndroidAgentError(
            "invalid_url",
            "Enter one http(s)-style deep link URL (no quotes, spaces, or control characters).",
        )
    return url


def validate_input_text(value: Any) -> str:
    text = str(value if value is not None else "")
    if not text or len(text) > INPUT_TEXT_MAX_CHARS or not _INPUT_TEXT_RE.fullmatch(text):
        raise AndroidAgentError(
            "invalid_text",
            "Input text may use letters, numbers, spaces, and . , _ - @ : + / # ( ) [ ] only.",
        )
    return text


def validate_key(value: Any) -> str:
    key = str(value or "").strip().upper()
    keycode = ALLOWED_KEYCODES.get(key)
    if keycode is None and key in _ALLOWED_KEYCODE_VALUES:
        keycode = key
    if keycode is None:
        raise AndroidAgentError(
            "invalid_key",
            "That key is not allowed. Allowed keys: "
            + ", ".join(sorted(ALLOWED_KEYCODES.keys()))
            + ".",
        )
    return keycode


def _clamped_int(value: Any, default: int, *, low: int, high: int, code: str, message: str) -> int:
    if value is None or value == "":
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise AndroidAgentError(code, message)
    return max(low, min(high, number))


def validate_coords(args: dict[str, Any], *, pairs: int) -> list[int]:
    keys = ("x", "y") if pairs == 1 else ("x1", "y1", "x2", "y2")
    coords: list[int] = []
    for key in keys:
        raw = args.get(key)
        if raw is None or raw == "":
            raise AndroidAgentError("invalid_coords", "Provide numeric screen coordinates.")
        try:
            number = int(raw)
        except (TypeError, ValueError):
            raise AndroidAgentError("invalid_coords", "Screen coordinates must be whole numbers.")
        if number < 0 or number > COORD_MAX:
            raise AndroidAgentError(
                "invalid_coords", f"Screen coordinates must be between 0 and {COORD_MAX}."
            )
        coords.append(number)
    return coords


def _device_shell_quote(value: str) -> str:
    """POSIX single-quote a leaf for the device shell (adb shell joins argv)."""
    return "'" + str(value).replace("'", "'\\''") + "'"


# ── command plans (pure, testable) ───────────────────────────────────────


@dataclass(frozen=True)
class AndroidCommand:
    """One bounded, fully-resolved command plan."""

    argv: list[str]
    timeout: int = ADB_TIMEOUT_SECONDS
    max_bytes: int = TEXT_MAX_BYTES
    binary: bool = False
    serial: str = ""
    kind: str = "adb"


def _adb(serial: str) -> list[str]:
    adb = resolve_binary("adb")
    if not adb:
        raise AndroidAgentError(
            "unavailable",
            sdk_status()["message"],
            reason="android_sdk_unavailable",
        )
    return [adb, "-s", serial]


def plan_devices() -> AndroidCommand:
    adb = resolve_binary("adb")
    if not adb:
        raise AndroidAgentError("unavailable", sdk_status()["message"], reason="android_sdk_unavailable")
    return AndroidCommand([adb, "devices", "-l"], max_bytes=LIST_MAX_BYTES)


def plan_avds() -> AndroidCommand:
    emulator = resolve_binary("emulator")
    if not emulator:
        raise AndroidAgentError(
            "unavailable",
            "The Android emulator binary is not available. Point the SDK root at a "
            "full SDK install (emulator/emulator) and retry.",
            reason="emulator_unavailable",
        )
    return AndroidCommand([emulator, "-list-avds"], max_bytes=TEXT_MAX_BYTES, kind="emulator")


def plan_start_avd(avd: str, *, headless: bool) -> AndroidCommand:
    emulator = resolve_binary("emulator")
    if not emulator:
        raise AndroidAgentError(
            "unavailable",
            "The Android emulator binary is not available. Point the SDK root at a "
            "full SDK install (emulator/emulator) and retry.",
            reason="emulator_unavailable",
        )
    argv = [emulator, "-avd", avd, "-no-snapshot-save"]
    if headless:
        argv.append("-no-window")
    return AndroidCommand(argv, timeout=0, max_bytes=TEXT_MAX_BYTES, kind="emulator")


def plan_stop(serial: str) -> AndroidCommand:
    return AndroidCommand([*_adb(serial), "emu", "kill"], serial=serial)


def plan_reboot(serial: str) -> AndroidCommand:
    return AndroidCommand([*_adb(serial), "reboot"], serial=serial)


def plan_boot_probe(serial: str) -> AndroidCommand:
    return AndroidCommand([*_adb(serial), "shell", "getprop", "sys.boot_completed"], serial=serial)


def plan_device_props(serial: str) -> AndroidCommand:
    return AndroidCommand([*_adb(serial), "shell", "getprop"], serial=serial, max_bytes=LIST_MAX_BYTES)


def plan_avd_name(serial: str) -> AndroidCommand:
    return AndroidCommand([*_adb(serial), "emu", "avd", "name"], serial=serial)


def plan_install(serial: str, apk_path: str) -> AndroidCommand:
    return AndroidCommand(
        [*_adb(serial), "install", "-r", apk_path],
        timeout=INSTALL_TIMEOUT_SECONDS,
        serial=serial,
    )


def plan_launch(serial: str, package: str) -> AndroidCommand:
    return AndroidCommand(
        [
            *_adb(serial),
            "shell",
            "monkey",
            "-p",
            _device_shell_quote(package),
            "-c",
            "android.intent.category.LAUNCHER",
            "1",
        ],
        serial=serial,
    )


def plan_force_stop(serial: str, package: str) -> AndroidCommand:
    return AndroidCommand(
        [*_adb(serial), "shell", "am", "force-stop", _device_shell_quote(package)],
        serial=serial,
    )


def plan_deep_link(serial: str, url: str, package: str = "") -> AndroidCommand:
    argv = [
        *_adb(serial),
        "shell",
        "am",
        "start",
        "-W",
        "-a",
        "android.intent.action.VIEW",
        "-d",
        _device_shell_quote(url),
    ]
    if package:
        argv.extend(["-p", _device_shell_quote(package)])
    return AndroidCommand(argv, serial=serial)


def plan_screenshot(serial: str) -> AndroidCommand:
    return AndroidCommand(
        [*_adb(serial), "exec-out", "screencap", "-p"],
        max_bytes=SCREENSHOT_MAX_BYTES,
        binary=True,
        serial=serial,
    )


def plan_record(serial: str, remote_path: str, seconds: int) -> AndroidCommand:
    return AndroidCommand(
        [
            *_adb(serial),
            "shell",
            "screenrecord",
            "--time-limit",
            str(seconds),
            remote_path,
        ],
        timeout=seconds + 20,
        serial=serial,
    )


def plan_record_pull(serial: str, remote_path: str, local_path: str) -> AndroidCommand:
    return AndroidCommand(
        [*_adb(serial), "pull", remote_path, local_path],
        timeout=RECORD_MAX_SECONDS,
        max_bytes=RECORD_MAX_BYTES,
        serial=serial,
    )


def plan_record_cleanup(serial: str, remote_path: str) -> AndroidCommand:
    return AndroidCommand(
        [*_adb(serial), "shell", "rm", "-f", remote_path],
        timeout=ADB_TIMEOUT_SECONDS,
        serial=serial,
    )


def plan_logcat(serial: str, lines: int, tag: str = "") -> AndroidCommand:
    argv = [*_adb(serial), "logcat", "-d", "-t", str(lines)]
    if tag:
        argv.extend(["-s", tag])
    return AndroidCommand(argv, max_bytes=LIST_MAX_BYTES, serial=serial)


def plan_input(serial: str, kind: str, args: dict[str, Any]) -> AndroidCommand:
    base = [*_adb(serial), "shell", "input"]
    if kind == "tap":
        x, y = validate_coords(args, pairs=1)
        return AndroidCommand([*base, "tap", str(x), str(y)], serial=serial)
    if kind == "swipe":
        x1, y1, x2, y2 = validate_coords(args, pairs=2)
        duration = _clamped_int(
            args.get("duration_ms"),
            300,
            low=1,
            high=SWIPE_DURATION_MAX_MS,
            code="invalid_coords",
            message="Swipe duration must be a whole number of milliseconds.",
        )
        return AndroidCommand(
            [*base, "swipe", str(x1), str(y1), str(x2), str(y2), str(duration)],
            serial=serial,
        )
    if kind == "text":
        text = validate_input_text(args.get("text"))
        return AndroidCommand(
            [*base, "text", _device_shell_quote(text.replace(" ", "%s"))], serial=serial
        )
    if kind == "key":
        return AndroidCommand([*base, "keyevent", validate_key(args.get("key"))], serial=serial)
    raise AndroidAgentError(
        "invalid_input",
        "The input kind must be tap, swipe, text, or key.",
    )


# ── bounded execution ────────────────────────────────────────────────────


@dataclass
class AndroidProcessResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""
    stdout_bytes: bytes = b""
    truncated: bool = False
    timed_out: bool = False
    duration_ms: int = 0


def _kill(proc) -> None:
    try:
        proc.kill()
    except (ProcessLookupError, OSError):
        pass


async def _read_bounded(stream, limit: int) -> tuple[bytes, bool]:
    """Read one pipe to EOF or the byte ceiling, whichever comes first."""
    if stream is None:
        return b"", False
    chunks: list[bytes] = []
    total = 0
    truncated = False
    while True:
        try:
            chunk = await stream.read(65536)
        except (ValueError, OSError):
            break
        if not chunk:
            break
        remaining = limit - total
        if remaining <= 0:
            truncated = True
            break
        if len(chunk) > remaining:
            chunks.append(chunk[:remaining])
            total += remaining
            truncated = True
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks), truncated


async def run_command(
    command: AndroidCommand,
    *,
    progress_cb: Optional[Callable[[dict], Awaitable[None]]] = None,
) -> AndroidProcessResult:
    """Run one plan with a hard timeout, a hard output ceiling, and kill-on-cancel."""
    started = time.monotonic()
    if progress_cb is not None:
        try:
            await progress_cb({"status": "running", "elapsed_s": 0.0, "tail": ""})
        except Exception:
            pass
    try:
        proc = await asyncio.create_subprocess_exec(
            *command.argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        raise AndroidAgentError(
            "unavailable",
            "The Android tool binary is no longer available. Recheck the SDK configuration.",
            reason="binary_missing",
        )
    except OSError as exc:
        raise AndroidAgentError(
            "unavailable",
            f"Could not start the Android tool ({exc.__class__.__name__}).",
            reason="binary_missing",
        )

    async def _drain_stdout() -> tuple[bytes, bool]:
        data, truncated = await _read_bounded(proc.stdout, command.max_bytes)
        if truncated:
            # Stop reading the pipe so an over-ceiling child hits EPIPE and
            # exits instead of blocking forever on a full pipe.
            try:
                proc.stdout.close()
            except Exception:
                pass
        return data, truncated

    out_task = asyncio.ensure_future(_drain_stdout())
    err_task = asyncio.ensure_future(_read_bounded(proc.stderr, 8192))
    timed_out = False
    try:
        try:
            await asyncio.wait_for(proc.wait(), timeout=command.timeout or None)
        except asyncio.TimeoutError:
            timed_out = True
            _kill(proc)
            try:
                await asyncio.wait_for(proc.wait(), timeout=2)
            except Exception:
                pass
    except asyncio.CancelledError:
        # Caller cancelled the turn: never leave the device command running.
        _kill(proc)
        try:
            await asyncio.wait_for(proc.wait(), timeout=2)
        except Exception:
            pass
        out_task.cancel()
        err_task.cancel()
        raise

    try:
        stdout_bytes, out_truncated = await out_task
    except (asyncio.CancelledError, Exception):
        stdout_bytes, out_truncated = b"", False
    try:
        stderr_bytes, _ = await err_task
    except (asyncio.CancelledError, Exception):
        stderr_bytes = b""

    returncode = 124 if timed_out else (proc.returncode if proc.returncode is not None else 127)
    result = AndroidProcessResult(
        returncode=returncode,
        stdout=stdout_bytes.decode("utf-8", errors="replace"),
        stderr=stderr_bytes.decode("utf-8", errors="replace"),
        stdout_bytes=stdout_bytes,
        truncated=out_truncated,
        timed_out=timed_out,
        duration_ms=int((time.monotonic() - started) * 1000),
    )
    if progress_cb is not None:
        tail = "\n".join(result.stdout.splitlines()[-8:])
        try:
            await progress_cb(
                {
                    "status": "done",
                    "elapsed_s": round(result.duration_ms / 1000, 1),
                    "tail": tail,
                }
            )
        except Exception:
            pass
    return result


# ── serialization and cancellation ───────────────────────────────────────

_serial_locks: dict[str, "asyncio.Lock"] = {}
_cancel_events: dict[str, "asyncio.Event"] = {}
_registry_guard = threading.Lock()


def reset_runtime_state() -> None:
    """Drop serial locks and cancel events (used by tests and shutdown)."""
    with _registry_guard:
        _serial_locks.clear()
        _cancel_events.clear()


async def _acquire_serial(serial: str) -> "asyncio.Lock":
    with _registry_guard:
        lock = _serial_locks.get(serial)
        if lock is None:
            lock = asyncio.Lock()
            _serial_locks[serial] = lock
    if lock.locked():
        raise AndroidAgentError(
            "busy_serial",
            f"Another command is already running against {serial}. "
            "Wait for it to finish, cancel it, or use a different serial.",
        )
    await lock.acquire()
    return lock


def _cancel_event(serial: str) -> "asyncio.Event":
    with _registry_guard:
        event = _cancel_events.get(serial)
        if event is None:
            event = asyncio.Event()
            _cancel_events[serial] = event
        return event


def cancel_wait(serial: str) -> bool:
    """Signal an in-flight `wait` for one serial; True when one was registered."""
    with _registry_guard:
        event = _cancel_events.get(serial)
    if event is None:
        return False
    event.set()
    return True


def _clear_cancel(serial: str) -> None:
    with _registry_guard:
        _cancel_events.pop(serial, None)


# ── error classification ─────────────────────────────────────────────────


def _redacted_tail(text: str, limit: int = 300) -> str:
    try:
        from src.authority_protocol import redact_secret_text

        return redact_secret_text(str(text or "")[:limit]).strip()
    except Exception:
        return ""


def raise_for_adb_result(result: AndroidProcessResult, *, action: str) -> None:
    """Map a completed adb command to an honest, fail-closed error."""
    if result.returncode == 0:
        return
    if result.timed_out or result.returncode == 124:
        raise AndroidAgentError(
            "timeout",
            f"The {action} operation did not finish in time. The device command was stopped.",
            reason="timeout",
        )
    stderr = result.stderr or ""
    lower = stderr.lower()
    combined = f"{stderr}\n{result.stdout}".lower()
    if "unauthorized" in combined:
        raise AndroidAgentError(
            "device_unauthorized",
            "The device is connected but not authorized. Unlock it and accept the USB "
            "debugging prompt, then retry.",
            reason="device_unauthorized",
        )
    if "offline" in combined:
        raise AndroidAgentError(
            "device_offline",
            "The device is offline. Wait for it to finish booting (or reconnect it), then retry.",
            reason="device_offline",
        )
    if (
        "device not found" in combined
        or "no devices/emulators found" in combined
        or "not found" in lower and "device" in lower
    ):
        raise AndroidAgentError(
            "device_not_found",
            "That device serial is no longer connected. Run the devices action and pick a live serial.",
            reason="device_not_found",
        )
    if "adb server" in combined or "cannot connect to daemon" in combined:
        raise AndroidAgentError(
            "adb_unavailable",
            "The adb server is not reachable. Start it (`adb start-server`) on the machine "
            "running the adapter, then retry.",
            reason="adb_unavailable",
        )
    if result.truncated:
        raise AndroidAgentError(
            "output_too_large",
            f"The {action} output exceeded the adapter's ceiling and was stopped.",
            reason="output_too_large",
        )
    safe = _redacted_tail(stderr)
    message = f"The {action} command exited with code {result.returncode}."
    if safe:
        message = f"{message} {safe}"
    raise AndroidAgentError("command_failed", message, reason="command_failed")


# ── device discovery ─────────────────────────────────────────────────────


def _parse_devices(output: str) -> list[dict[str, Any]]:
    devices: list[dict[str, Any]] = []
    for raw_line in str(output or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("List of devices") or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        if not _SERIAL_RE.fullmatch(serial):
            continue
        entry: dict[str, Any] = {
            "serial": serial,
            "state": state,
            "authorized": state == "device",
            "emulator": serial.startswith("emulator-"),
        }
        for token in parts[2:]:
            if ":" in token:
                key, _, value = token.partition(":")
                if key in ("model", "product", "device", "transport_id"):
                    entry[key] = value
        devices.append(entry)
    return devices


def _parse_props(output: str) -> dict[str, str]:
    props: dict[str, str] = {}
    for match in re.finditer(r"^\[([^\]]+)\]:\s*\[(.*)\]$", str(output or ""), re.MULTILINE):
        props[match.group(1)] = match.group(2)
    return props


async def list_devices() -> list[dict[str, Any]]:
    """Live device list with state, model, Android/API version, and boot state."""
    result = await run_command(plan_devices())
    raise_for_adb_result(result, action="devices")
    devices = _parse_devices(result.stdout)
    for entry in devices:
        if entry["state"] != "device":
            continue
        try:
            props_result = await run_command(plan_device_props(entry["serial"]))
            if props_result.returncode == 0:
                props = _parse_props(props_result.stdout)
                entry["model"] = props.get("ro.product.model", entry.get("model", ""))
                entry["android"] = props.get("ro.build.version.release", "")
                entry["api"] = props.get("ro.build.version.sdk", "")
                entry["boot_completed"] = props.get("sys.boot_completed", "") == "1"
            if entry.get("emulator"):
                name_result = await run_command(plan_avd_name(entry["serial"]))
                if name_result.returncode == 0 and name_result.stdout.strip():
                    entry["avd"] = name_result.stdout.strip().splitlines()[-1].strip()
        except AndroidAgentError:
            # Enrichment is best-effort; the device still appears with its
            # adb-reported state instead of failing the whole listing.
            continue
    return devices


async def require_ready_device(serial: str) -> dict[str, Any]:
    """Confirm the serial exists and is ready, or raise honest fail-closed copy."""
    devices = await list_devices()
    match = next((entry for entry in devices if entry["serial"] == serial), None)
    if match is None:
        raise AndroidAgentError(
            "device_not_found",
            f"No connected device or emulator has serial '{serial}'. "
            "Run the devices action and pick a live serial.",
            reason="device_not_found",
        )
    if match["state"] == "unauthorized":
        raise AndroidAgentError(
            "device_unauthorized",
            f"Device '{serial}' is connected but not authorized. Unlock it and accept "
            "the USB debugging prompt, then retry.",
            reason="device_unauthorized",
        )
    if match["state"] != "device":
        raise AndroidAgentError(
            "device_offline",
            f"Device '{serial}' is {match['state']}. Wait for it to become ready, then retry.",
            reason="device_offline",
        )
    return match


def list_avds() -> list[str]:
    """Installed AVD names from `emulator -list-avds` (sync, bounded runner)."""
    emulator = resolve_binary("emulator")
    if not emulator:
        raise AndroidAgentError(
            "unavailable",
            "The Android emulator binary is not available. Point the SDK root at a "
            "full SDK install (emulator/emulator) and retry.",
            reason="emulator_unavailable",
        )
    return _list_avds_sync(emulator)


def _list_avds_sync(emulator: str) -> list[str]:
    import subprocess

    try:
        proc = subprocess.run(
            [emulator, "-list-avds"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=ADB_TIMEOUT_SECONDS,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise AndroidAgentError(
            "unavailable",
            f"Could not list AVDs ({exc.__class__.__name__}).",
            reason="emulator_unavailable",
        )
    names: list[str] = []
    for line in (proc.stdout or "").splitlines():
        name = line.strip()
        if name and _AVD_RE.fullmatch(name) and name not in names:
            names.append(name)
    return names[:256]


# ── evidence + audit ─────────────────────────────────────────────────────


def _event_dir() -> Path:
    path = Path(ANDROID_EVIDENCE_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_evidence(data: bytes, *, suffix: str) -> dict[str, Any]:
    """Persist one bounded evidence blob and return a redacted descriptor."""
    directory = _event_dir()
    name = f"{uuid.uuid4().hex}{suffix}"
    path = directory / name
    try:
        with open(path, "wb") as handle:
            handle.write(data)
        safe_chmod(path, 0o600)
    except OSError as exc:
        raise AndroidAgentError(
            "evidence_failed",
            f"Could not write the evidence file ({exc.__class__.__name__}).",
            reason="evidence_failed",
        )
    return {
        "kind": "android_file",
        "name": name,
        "path": str(path),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def record_android_audit(
    action: str,
    state: str,
    *,
    serial: str = "",
    actor: Optional[str] = None,
    detail: Optional[dict[str, Any]] = None,
) -> None:
    """Append one redacted audit event for a governed Android attempt."""
    entry: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "action": str(action or "")[:64],
        "state": str(state or "")[:64],
        "serial": str(serial or "")[:128],
        "actor": str(actor or "")[:128],
    }
    if isinstance(detail, dict) and detail:
        safe: dict[str, Any] = {}
        for key, value in list(detail.items())[:24]:
            name = str(key)[:40]
            if isinstance(value, bool) or isinstance(value, (int, float)):
                safe[name] = value
            else:
                safe[name] = _redacted_tail(str(value), 500)
        entry["detail"] = safe
    logger.info(
        "android adapter action=%s state=%s serial=%s",
        entry["action"],
        entry["state"],
        entry["serial"],
    )
    try:
        path = Path(ANDROID_AUDIT_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        new_file = not path.exists()
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
        if new_file:
            safe_chmod(path, 0o600)
    except OSError:
        logger.warning("Could not write the Android adapter audit event")


# ── action implementations ───────────────────────────────────────────────


def _bounded_text(text: str, limit: int, truncated: bool) -> tuple[str, bool]:
    raw = str(text or "").encode("utf-8")
    hit = bool(truncated) or len(raw) > limit
    if len(raw) > limit:
        raw = raw[:limit]
    output = raw.decode("utf-8", errors="replace")
    if hit:
        output += f"\n... [truncated at {limit} bytes]"
    return output, hit


def _citation(target: str, *, avd: str = "", label: str = "") -> str:
    if avd:
        return f'android AVD "{avd}" ({target})'
    if target:
        return f'android device "{target}"{f" ({label})" if label else ""}'
    return "android"


def _source(action: str, *, serial: str = "", extra: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    source: dict[str, Any] = {
        "kind": "android_device",
        "action": action,
        "read_only": action in READ_ACTIONS,
    }
    if serial:
        source["serial"] = serial
    if extra:
        source.update({key: value for key, value in extra.items() if value not in (None, "")})
    return source


async def _action_status(args: dict[str, Any]) -> dict[str, Any]:
    status = sdk_status()
    lines = [
        f"Android SDK: {'available' if status['available'] else 'unavailable'}",
        f"  sdk root: {status['sdk_root'] or '(not configured)'}",
        f"  adb: {status['adb'] or '(not found)'}",
        f"  emulator: {status['emulator'] or '(not found)'}",
        f"  sdkmanager: {status['sdkmanager'] or '(not found)'}",
        f"  AVD allowlist: {', '.join(status['avd_allowlist']) or '(any installed AVD)'}",
        status["message"],
    ]
    return {
        "ok": True,
        "action": "status",
        "state": "ok" if status["available"] else "unavailable",
        "output": "\n".join(lines),
        "source": _source("status"),
        "citation": "android SDK",
        "limits": {"timeout_seconds": 0, "max_output_bytes": TEXT_MAX_BYTES},
        "truncated": False,
        "sdk": status,
    }


async def _action_devices(args: dict[str, Any]) -> dict[str, Any]:
    devices = await list_devices()
    lines = [f"{len(devices)} connected target(s):"]
    for entry in devices:
        detail = " ".join(
            part
            for part in (
                entry.get("model", ""),
                f"android {entry['android']}" if entry.get("android") else "",
                f"api {entry['api']}" if entry.get("api") else "",
                "booted" if entry.get("boot_completed") else "",
                f"avd {entry['avd']}" if entry.get("avd") else "",
            )
            if part
        )
        lines.append(f"  {entry['serial']}  {entry['state']}  {detail}".rstrip())
    if not devices:
        lines.append(
            "  (none) Start an AVD with the start action, or connect a device and "
            "accept its USB debugging prompt."
        )
    output, truncated = _bounded_text("\n".join(lines), LIST_MAX_BYTES, False)
    return {
        "ok": True,
        "action": "devices",
        "state": "ok",
        "output": output,
        "source": _source("devices"),
        "citation": "android devices",
        "limits": {"timeout_seconds": ADB_TIMEOUT_SECONDS, "max_output_bytes": LIST_MAX_BYTES},
        "truncated": truncated,
        "devices": devices,
    }


async def _action_avds(args: dict[str, Any]) -> dict[str, Any]:
    installed = await asyncio.to_thread(list_avds)
    allowlist = configured_avd_allowlist()
    lines = [f"{len(installed)} installed AVD(s):"]
    for name in installed:
        allowed = not allowlist or name in allowlist
        lines.append(f"  {name}{'' if allowed else '  (not in the AVD allowlist)'}")
    if not installed:
        lines.append("  (none) Create an AVD with the SDK's avdmanager, then retry.")
    if allowlist:
        lines.append(f"Allowlist: {', '.join(allowlist)}")
    output, truncated = _bounded_text("\n".join(lines), TEXT_MAX_BYTES, False)
    return {
        "ok": True,
        "action": "avds",
        "state": "ok",
        "output": output,
        "source": _source("avds"),
        "citation": "android AVDs",
        "limits": {"timeout_seconds": ADB_TIMEOUT_SECONDS, "max_output_bytes": TEXT_MAX_BYTES},
        "truncated": truncated,
        "avds": [
            {"name": name, "allowed": not allowlist or name in allowlist} for name in installed
        ],
        "avd_allowlist": allowlist,
    }


def _validate_start_avd(avd: str) -> list[str]:
    installed = list_avds()
    if avd not in installed:
        raise AndroidAgentError(
            "avd_not_installed",
            f"No installed AVD is named '{avd}'. Installed AVDs: "
            + (", ".join(installed) if installed else "(none)"),
            reason="avd_not_installed",
        )
    allowlist = configured_avd_allowlist()
    if allowlist and avd not in allowlist:
        raise AndroidAgentError(
            "avd_not_allowed",
            f"AVD '{avd}' is not in the operator allowlist. Allowed: {', '.join(allowlist)}.",
            reason="avd_not_allowed",
        )
    return installed


async def _action_start(args: dict[str, Any], *, progress_cb=None) -> dict[str, Any]:
    avd = validate_avd(args.get("avd"))
    headless = bool(args.get("headless") is True or str(args.get("headless", "")).lower() in ("1", "true", "yes"))
    await asyncio.to_thread(_validate_start_avd, avd)
    command = plan_start_avd(avd, headless=headless)
    directory = _event_dir()
    log_path = directory / f"avd-{avd}-{uuid.uuid4().hex[:8]}.log"
    try:
        log_handle = open(log_path, "ab")
    except OSError as exc:
        raise AndroidAgentError(
            "evidence_failed", f"Could not open the emulator log ({exc.__class__.__name__})."
        )
    try:
        proc = await asyncio.create_subprocess_exec(
            *command.argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=log_handle,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
    except (FileNotFoundError, OSError) as exc:
        log_handle.close()
        raise AndroidAgentError(
            "unavailable",
            f"Could not start the emulator ({exc.__class__.__name__}).",
            reason="binary_missing",
        )
    try:
        returncode: Optional[int] = None
        try:
            returncode = await asyncio.wait_for(proc.wait(), timeout=0.75)
        except asyncio.TimeoutError:
            returncode = None
        if returncode is not None:
            tail = ""
            try:
                tail = _redacted_tail(log_path.read_text(encoding="utf-8", errors="replace")[-1200:])
            except OSError:
                tail = ""
            message = f"The emulator exited immediately with code {returncode}."
            if tail:
                message = f"{message} {tail}"
            raise AndroidAgentError("emulator_failed", message, reason="emulator_failed")
        return {
            "ok": True,
            "action": "start",
            "state": "starting",
            "output": (
                f"Starting AVD '{avd}' (pid {proc.pid}). Use wait with the booted "
                "device's serial to confirm it is ready."
            ),
            "source": _source("start", extra={"avd": avd, "headless": headless}),
            "citation": _citation("", avd=avd),
            "limits": {"timeout_seconds": 0, "max_output_bytes": TEXT_MAX_BYTES},
            "truncated": False,
            "avd": avd,
            "pid": proc.pid,
            "log_path": str(log_path),
        }
    finally:
        log_handle.close()


async def _action_stop(serial: str) -> dict[str, Any]:
    await require_ready_device(serial)
    result = await run_command(plan_stop(serial))
    raise_for_adb_result(result, action="stop")
    return {
        "ok": True,
        "action": "stop",
        "state": "ok",
        "output": (result.stdout.strip() or f"Stopped {serial}."),
        "source": _source("stop", serial=serial),
        "citation": _citation(serial),
        "limits": {"timeout_seconds": ADB_TIMEOUT_SECONDS, "max_output_bytes": TEXT_MAX_BYTES},
        "truncated": result.truncated,
    }


async def _action_reboot(serial: str) -> dict[str, Any]:
    await require_ready_device(serial)
    result = await run_command(plan_reboot(serial))
    raise_for_adb_result(result, action="reboot")
    return {
        "ok": True,
        "action": "reboot",
        "state": "rebooting",
        "output": f"Reboot requested for {serial}. Use wait to confirm it is ready again.",
        "source": _source("reboot", serial=serial),
        "citation": _citation(serial),
        "limits": {"timeout_seconds": ADB_TIMEOUT_SECONDS, "max_output_bytes": TEXT_MAX_BYTES},
        "truncated": False,
    }


async def _action_wait(serial: str, args: dict[str, Any], *, progress_cb=None) -> dict[str, Any]:
    timeout = _clamped_int(
        args.get("timeout"),
        BOOT_WAIT_DEFAULT_SECONDS,
        low=BOOT_WAIT_MIN_SECONDS,
        high=BOOT_WAIT_MAX_SECONDS,
        code="invalid_timeout",
        message="The wait timeout must be a whole number of seconds.",
    )
    await require_ready_device(serial)
    event = _cancel_event(serial)
    deadline = time.monotonic() + timeout
    last_progress = 0.0
    while True:
        if event.is_set():
            raise AndroidAgentError("cancelled", "The wait was cancelled.", reason="cancelled")
        result = await run_command(plan_boot_probe(serial))
        if result.returncode == 0 and result.stdout.strip() == "1":
            return {
                "ok": True,
                "action": "wait",
                "state": "ready",
                "output": f"{serial} is booted and ready.",
                "source": _source("wait", serial=serial),
                "citation": _citation(serial),
                "limits": {"timeout_seconds": timeout, "max_output_bytes": TEXT_MAX_BYTES},
                "truncated": False,
                "boot_completed": True,
            }
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AndroidAgentError(
                "timeout",
                f"{serial} did not finish booting within {timeout}s. It may still be "
                "starting; run wait again or check the emulator log.",
                reason="timeout",
            )
        now = time.monotonic()
        if progress_cb is not None and now - last_progress >= 2.0:
            last_progress = now
            try:
                await progress_cb(
                    {
                        "status": "waiting",
                        "elapsed_s": round(timeout - remaining, 1),
                        "tail": f"waiting for {serial} to boot",
                    }
                )
            except Exception:
                pass
        await asyncio.sleep(min(BOOT_POLL_SECONDS, remaining))


async def _action_install(serial: str, args: dict[str, Any], *, progress_cb=None) -> dict[str, Any]:
    apk = validate_apk_path(args.get("apk"))
    await require_ready_device(serial)
    result = await run_command(plan_install(serial, apk), progress_cb=progress_cb)
    combined = f"{result.stdout}\n{result.stderr}"
    if "Failure" in combined or "failure" in combined.lower():
        # `adb install` reports package-manager rejections here; surface the
        # bounded reason instead of a generic exit-code message.
        raise AndroidAgentError(
            "install_failed",
            f"The package manager rejected the APK. {result.stdout.strip()[:300]}".strip(),
            reason="install_failed",
        )
    raise_for_adb_result(result, action="install")
    output, truncated = _bounded_text(result.stdout.strip(), TEXT_MAX_BYTES, result.truncated)
    return {
        "ok": True,
        "action": "install",
        "state": "ok",
        "output": output or f"Installed {os.path.basename(apk)}.",
        "source": _source("install", serial=serial, extra={"apk": os.path.basename(apk)}),
        "citation": _citation(serial),
        "limits": {
            "timeout_seconds": INSTALL_TIMEOUT_SECONDS,
            "max_output_bytes": TEXT_MAX_BYTES,
        },
        "truncated": truncated,
    }


async def _action_launch(serial: str, args: dict[str, Any]) -> dict[str, Any]:
    package = validate_package(args.get("package"))
    await require_ready_device(serial)
    result = await run_command(plan_launch(serial, package))
    raise_for_adb_result(result, action="launch")
    return {
        "ok": True,
        "action": "launch",
        "state": "ok",
        "output": result.stdout.strip() or f"Launched {package}.",
        "source": _source("launch", serial=serial, extra={"package": package}),
        "citation": _citation(serial, label=package),
        "limits": {"timeout_seconds": ADB_TIMEOUT_SECONDS, "max_output_bytes": TEXT_MAX_BYTES},
        "truncated": result.truncated,
    }


async def _action_force_stop(serial: str, args: dict[str, Any]) -> dict[str, Any]:
    package = validate_package(args.get("package"))
    await require_ready_device(serial)
    result = await run_command(plan_force_stop(serial, package))
    raise_for_adb_result(result, action="force_stop")
    return {
        "ok": True,
        "action": "force_stop",
        "state": "ok",
        "output": result.stdout.strip() or f"Force-stopped {package}.",
        "source": _source("force_stop", serial=serial, extra={"package": package}),
        "citation": _citation(serial, label=package),
        "limits": {"timeout_seconds": ADB_TIMEOUT_SECONDS, "max_output_bytes": TEXT_MAX_BYTES},
        "truncated": result.truncated,
    }


async def _action_deep_link(serial: str, args: dict[str, Any]) -> dict[str, Any]:
    url = validate_url(args.get("url"))
    package = str(args.get("package") or "").strip()
    if package:
        package = validate_package(package)
    await require_ready_device(serial)
    result = await run_command(plan_deep_link(serial, url, package))
    raise_for_adb_result(result, action="deep_link")
    return {
        "ok": True,
        "action": "deep_link",
        "state": "ok",
        "output": result.stdout.strip() or f"Opened {url}.",
        "source": _source("deep_link", serial=serial, extra={"package": package}),
        "citation": _citation(serial),
        "limits": {"timeout_seconds": ADB_TIMEOUT_SECONDS, "max_output_bytes": TEXT_MAX_BYTES},
        "truncated": result.truncated,
    }


async def _action_screenshot(serial: str) -> dict[str, Any]:
    await require_ready_device(serial)
    result = await run_command(plan_screenshot(serial))
    if result.returncode != 0:
        raise_for_adb_result(result, action="screenshot")
    if result.truncated or not result.stdout_bytes:
        raise AndroidAgentError(
            "output_too_large",
            "The screenshot exceeded the adapter's size ceiling and was not saved.",
            reason="output_too_large",
        )
    evidence = write_evidence(result.stdout_bytes, suffix=".png")
    return {
        "ok": True,
        "action": "screenshot",
        "state": "ok",
        "output": (
            f"Saved a screenshot from {serial} ({evidence['bytes']} bytes, "
            f"sha256 {evidence['sha256'][:16]}…)."
        ),
        "source": _source("screenshot", serial=serial, extra={"evidence": evidence["name"]}),
        "citation": _citation(serial),
        "limits": {
            "timeout_seconds": ADB_TIMEOUT_SECONDS,
            "max_output_bytes": SCREENSHOT_MAX_BYTES,
        },
        "truncated": False,
        "evidence": evidence,
    }


async def _action_record(serial: str, args: dict[str, Any], *, progress_cb=None) -> dict[str, Any]:
    seconds = _clamped_int(
        args.get("seconds"),
        15,
        low=1,
        high=RECORD_MAX_SECONDS,
        code="invalid_duration",
        message=f"Recording length must be between 1 and {RECORD_MAX_SECONDS} seconds.",
    )
    await require_ready_device(serial)
    remote = f"/sdcard/pandamonium-record-{uuid.uuid4().hex[:10]}.mp4"
    local = _event_dir() / f"record-{uuid.uuid4().hex[:10]}.mp4"
    if progress_cb is not None:
        try:
            await progress_cb(
                {
                    "status": "recording",
                    "elapsed_s": 0.0,
                    "tail": f"recording {seconds}s on {serial}",
                }
            )
        except Exception:
            pass
    try:
        result = await run_command(plan_record(serial, remote, seconds), progress_cb=progress_cb)
        raise_for_adb_result(result, action="record")
        pull = await run_command(plan_record_pull(serial, remote, str(local)), progress_cb=progress_cb)
        if pull.returncode != 0 or not local.exists():
            raise_for_adb_result(pull, action="record")
        data = local.read_bytes()
        if len(data) > RECORD_MAX_BYTES:
            raise AndroidAgentError(
                "output_too_large",
                "The screen recording exceeded the adapter's size ceiling and was not saved.",
                reason="output_too_large",
            )
    finally:
        try:
            local.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            await run_command(plan_record_cleanup(serial, remote))
        except AndroidAgentError:
            pass
    evidence = write_evidence(data, suffix=".mp4")
    return {
        "ok": True,
        "action": "record",
        "state": "ok",
        "output": (
            f"Recorded {seconds}s from {serial} ({evidence['bytes']} bytes, "
            f"sha256 {evidence['sha256'][:16]}…)."
        ),
        "source": _source("record", serial=serial, extra={"evidence": evidence["name"]}),
        "citation": _citation(serial),
        "limits": {
            "timeout_seconds": RECORD_MAX_SECONDS,
            "max_output_bytes": RECORD_MAX_BYTES,
        },
        "truncated": False,
        "evidence": evidence,
    }


async def _action_logcat(serial: str, args: dict[str, Any]) -> dict[str, Any]:
    lines = _clamped_int(
        args.get("lines"),
        LOGCAT_DEFAULT_LINES,
        low=1,
        high=LOGCAT_MAX_LINES,
        code="invalid_lines",
        message=f"Logcat lines must be between 1 and {LOGCAT_MAX_LINES}.",
    )
    tag = str(args.get("tag") or "").strip()
    if tag and not _LOGCAT_TAG_RE.fullmatch(tag):
        raise AndroidAgentError(
            "invalid_tag", "The logcat tag may use letters, numbers, dot, dash, underscore, and colon only."
        )
    await require_ready_device(serial)
    result = await run_command(plan_logcat(serial, lines, tag))
    raise_for_adb_result(result, action="logcat")
    output, truncated = _bounded_text(result.stdout.strip(), LIST_MAX_BYTES, result.truncated)
    return {
        "ok": True,
        "action": "logcat",
        "state": "ok",
        "output": output or "(no logcat output)",
        "source": _source("logcat", serial=serial, extra={"tag": tag, "lines": lines}),
        "citation": _citation(serial),
        "limits": {"timeout_seconds": ADB_TIMEOUT_SECONDS, "max_output_bytes": LIST_MAX_BYTES},
        "truncated": truncated,
    }


async def _action_input(serial: str, args: dict[str, Any]) -> dict[str, Any]:
    kind = str(args.get("input") or args.get("kind") or "").strip().lower()
    if kind not in INPUT_KINDS:
        raise AndroidAgentError(
            "invalid_input", "The input kind must be tap, swipe, text, or key."
        )
    command = plan_input(serial, kind, args)
    await require_ready_device(serial)
    result = await run_command(command)
    raise_for_adb_result(result, action=f"input {kind}")
    return {
        "ok": True,
        "action": "input",
        "state": "ok",
        "output": result.stdout.strip() or f"Sent {kind} input to {serial}.",
        "source": _source("input", serial=serial, extra={"input": kind}),
        "citation": _citation(serial),
        "limits": {"timeout_seconds": ADB_TIMEOUT_SECONDS, "max_output_bytes": TEXT_MAX_BYTES},
        "truncated": result.truncated,
    }


async def execute_android_operation(
    action: Any,
    arguments: Optional[dict[str, Any]] = None,
    *,
    owner: Optional[str] = None,
    progress_cb: Optional[Callable[[dict], Awaitable[None]]] = None,
    audit: bool = True,
) -> dict[str, Any]:
    """Run one governed Android action and audit the attempt.

    Device actions take the per-serial lock for their whole duration; a second
    command against the same serial fails fast with ``busy_serial``.
    """
    action_value = str(action or "").strip().lower()
    args = dict(arguments or {})
    if action_value not in ACTIONS:
        raise AndroidAgentError(
            "invalid_action",
            "The Android action must be one of: " + ", ".join(ACTIONS) + ".",
        )
    serial = ""
    if action_value in DEVICE_ACTIONS:
        serial = validate_serial(args.get("serial"))
    elif action_value == "cancel":
        serial = validate_serial(args.get("serial"))
        return _cancel_result(serial, owner=owner, audit=audit)
    lock: Optional[asyncio.Lock] = None
    try:
        if serial:
            lock = await _acquire_serial(serial)
            _clear_cancel(serial)
        if action_value == "status":
            result = await _action_status(args)
        elif action_value == "devices":
            result = await _action_devices(args)
        elif action_value == "avds":
            result = await _action_avds(args)
        elif action_value == "start":
            result = await _action_start(args, progress_cb=progress_cb)
        elif action_value == "stop":
            result = await _action_stop(serial)
        elif action_value == "reboot":
            result = await _action_reboot(serial)
        elif action_value == "wait":
            result = await _action_wait(serial, args, progress_cb=progress_cb)
        elif action_value == "install":
            result = await _action_install(serial, args, progress_cb=progress_cb)
        elif action_value == "launch":
            result = await _action_launch(serial, args)
        elif action_value == "force_stop":
            result = await _action_force_stop(serial, args)
        elif action_value == "deep_link":
            result = await _action_deep_link(serial, args)
        elif action_value == "screenshot":
            result = await _action_screenshot(serial)
        elif action_value == "record":
            result = await _action_record(serial, args, progress_cb=progress_cb)
        elif action_value == "logcat":
            result = await _action_logcat(serial, args)
        elif action_value == "input":
            result = await _action_input(serial, args)
        else:  # pragma: no cover - ACTIONS is exhaustive above
            raise AndroidAgentError("invalid_action", "Unsupported Android action.")
    except AndroidAgentError as exc:
        if audit:
            record_android_audit(
                action_value,
                exc.reason,
                serial=serial,
                actor=owner,
                detail={"code": exc.code},
            )
        raise
    except Exception as exc:  # noqa: BLE001 - map unexpected failures to honest copy
        if audit:
            record_android_audit(
                action_value,
                "operation_failed",
                serial=serial,
                actor=owner,
                detail={"error": exc.__class__.__name__},
            )
        raise AndroidAgentError(
            "operation_failed",
            "The Android operation failed before it could complete.",
            reason="operation_failed",
        ) from exc
    finally:
        if lock is not None and lock.locked():
            lock.release()
    if audit:
        record_android_audit(
            action_value,
            "ok",
            serial=serial,
            actor=owner,
            detail={
                "state": result.get("state", "ok"),
                "truncated": result.get("truncated", False),
                "evidence": (result.get("evidence") or {}).get("name", ""),
            },
        )
    return result


def _cancel_result(serial: str, *, owner: Optional[str], audit: bool) -> dict[str, Any]:
    cancelled = cancel_wait(serial)
    if audit:
        record_android_audit(
            "cancel",
            "cancelled" if cancelled else "noop",
            serial=serial,
            actor=owner,
        )
    return {
        "ok": True,
        "action": "cancel",
        "state": "cancelled" if cancelled else "noop",
        "output": (
            f"Cancelled the in-flight wait for {serial}."
            if cancelled
            else f"No wait is in flight for {serial}."
        ),
        "source": _source("cancel", serial=serial),
        "citation": _citation(serial),
        "limits": {"timeout_seconds": 0, "max_output_bytes": TEXT_MAX_BYTES},
        "truncated": False,
        "cancelled": cancelled,
    }
