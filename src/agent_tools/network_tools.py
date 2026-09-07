"""Constrained, read-only inspection of the running host's network view."""

from __future__ import annotations

import asyncio
import json
import ntpath
import os
import socket
import sys
from typing import Final

from src.tool_utils import _truncate


NETWORK_PROBES_BY_PLATFORM: Final[
    dict[str, tuple[tuple[str, tuple[str, ...]], ...]]
] = {
    "linux": (
        ("interfaces", ("ip", "-brief", "address", "show")),
        ("ipv4_routes", ("ip", "-4", "route", "show")),
        ("ipv6_routes", ("ip", "-6", "route", "show")),
        ("neighbors", ("ip", "neighbor", "show")),
        ("tailscale", ("tailscale", "status")),
    ),
    "macos": (
        ("interfaces", ("ifconfig", "-a")),
        ("default_route", ("route", "-n", "get", "default")),
        ("neighbors", ("arp", "-an")),
        ("tailscale", ("tailscale", "status")),
    ),
    "windows": (
        ("interfaces", ("ipconfig", "/all")),
        ("routes", ("route", "print")),
        ("neighbors", ("arp", "-a")),
        ("tailscale", ("tailscale", "status")),
    ),
}

_POSIX_TRUSTED_EXECUTABLES: Final[dict[str, dict[str, tuple[str, ...]]]] = {
    "linux": {
        "ip": ("/usr/sbin/ip", "/usr/bin/ip", "/sbin/ip", "/bin/ip"),
        "tailscale": ("/usr/bin/tailscale", "/usr/local/bin/tailscale"),
    },
    "macos": {
        "ifconfig": ("/sbin/ifconfig",),
        "route": ("/sbin/route",),
        "arp": ("/usr/sbin/arp",),
        "tailscale": (
            "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
            "/opt/homebrew/bin/tailscale",
            "/usr/local/bin/tailscale",
            "/usr/bin/tailscale",
        ),
    },
}
_PROBE_TIMEOUT_SECONDS: Final[float] = 5.0
_PROBE_OUTPUT_CHARS: Final[int] = 1_200
_NETWORK_SNAPSHOT_CHARS: Final[int] = 9_000
_LINUX_KERNEL_TABLE_CHARS: Final[int] = 300
_LINUX_KERNEL_TABLES: Final[tuple[tuple[str, str], ...]] = (
    ("interfaces", "/proc/net/dev"),
    ("ipv4_routes", "/proc/net/route"),
    ("ipv6_routes", "/proc/net/ipv6_route"),
    ("neighbors", "/proc/net/arp"),
)


def _platform_family(platform: str | None = None) -> str:
    platform_id = (platform or sys.platform).lower()
    if platform_id == "windows" or platform_id.startswith("win"):
        return "windows"
    if platform_id in {"darwin", "macos"}:
        return "macos"
    return "linux"


def _trusted_executables(platform: str | None = None) -> dict[str, tuple[str, ...]]:
    family = _platform_family(platform)
    if family != "windows":
        return _POSIX_TRUSTED_EXECUTABLES[family]

    windows_roots = tuple(dict.fromkeys(filter(None, (
        os.environ.get("SystemRoot"),
        os.environ.get("WINDIR"),
        r"C:\Windows",
    ))))
    system32 = tuple(ntpath.join(root, "System32") for root in windows_roots)
    return {
        "ipconfig": tuple(ntpath.join(root, "ipconfig.exe") for root in system32),
        "route": tuple(ntpath.join(root, "route.exe") for root in system32),
        "arp": tuple(ntpath.join(root, "arp.exe") for root in system32),
        "tailscale": (
            r"C:\Program Files\Tailscale\tailscale.exe",
            r"C:\Program Files (x86)\Tailscale\tailscale.exe",
        ),
    }


def _platform_probes(platform: str | None = None) -> tuple[tuple[str, tuple[str, ...]], ...]:
    return NETWORK_PROBES_BY_PLATFORM[_platform_family(platform)]


def _resolve_executable(name: str, platform: str | None = None) -> str | None:
    """Resolve only an allowlisted binary from fixed system paths."""
    for candidate in _trusted_executables(platform).get(name, ()):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


async def _run_probe(argv: tuple[str, ...], platform: str) -> dict:
    executable = _resolve_executable(argv[0], platform)
    if executable is None:
        return {
            "available": False,
            "command": " ".join(argv),
            "error": f"{argv[0]} is not installed in an approved system path",
        }

    try:
        process = await asyncio.create_subprocess_exec(
            executable,
            *argv[1:],
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        return {
            "available": False,
            "command": " ".join(argv),
            "error": f"probe could not start: {exc}",
        }
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=_PROBE_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        process.kill()
        await process.communicate()
        return {
            "available": True,
            "command": " ".join(argv),
            "error": f"probe timed out after {_PROBE_TIMEOUT_SECONDS:g} seconds",
            "exit_code": None,
        }

    decoded_stdout = _truncate(
        stdout.decode("utf-8", errors="replace").strip(), _PROBE_OUTPUT_CHARS
    )
    decoded_stderr = _truncate(
        stderr.decode("utf-8", errors="replace").strip(), _PROBE_OUTPUT_CHARS
    )
    result = {
        "available": True,
        "command": " ".join(argv),
        "exit_code": process.returncode,
        "output": decoded_stdout,
    }
    if decoded_stderr:
        result["error"] = decoded_stderr
    return result


def _read_linux_kernel_table(path: str) -> str:
    """Read one fixed procfs network table with bounded output."""
    try:
        with open(path, encoding="utf-8", errors="replace") as table:
            return _truncate(table.read(), _LINUX_KERNEL_TABLE_CHARS).strip()
    except OSError:
        return ""


def _linux_kernel_probe() -> dict:
    """Collect the container-visible Linux network view without OS packages."""
    try:
        interfaces = [
            {"index": index, "name": name}
            for index, name in socket.if_nameindex()
        ]
    except OSError:
        interfaces = []

    addresses: set[str] = set()
    try:
        for item in socket.getaddrinfo(socket.gethostname(), None):
            address = item[4][0]
            if address:
                addresses.add(address)
    except (OSError, socket.gaierror):
        pass

    kernel_tables = {
        name: output
        for name, path in _LINUX_KERNEL_TABLES
        if (output := _read_linux_kernel_table(path))
    }
    data = {
        "source": "python stdlib and fixed Linux kernel tables",
        "interfaces": interfaces,
        "addresses": sorted(addresses),
        "kernel_tables": kernel_tables,
    }
    available = bool(interfaces or addresses or kernel_tables)
    return {
        "available": available,
        "exit_code": 0 if available else 1,
        "output": _truncate(
            json.dumps(data, ensure_ascii=False, indent=2), _PROBE_OUTPUT_CHARS
        ),
        **({} if available else {"error": "Linux kernel network data is unavailable"}),
    }


class NetworkInspectionTool:
    """Collect a bounded network snapshot without accepting commands or paths."""

    async def execute(self, content: str, ctx: dict) -> dict:
        del content, ctx  # Calls are intentionally parameterless and owner-scoped upstream.

        platform = _platform_family()
        probes = {}
        if platform == "linux":
            # python:3.x-slim does not ship iproute2. This fixed internal probe
            # keeps the official container useful without adding a dependency.
            probes["kernel_network"] = _linux_kernel_probe()
        probes.update({
            name: await _run_probe(argv, platform)
            for name, argv in _platform_probes(platform)
        })
        successful = [
            name
            for name, result in probes.items()
            if result.get("available") and result.get("exit_code") == 0
        ]
        snapshot = {
            "capability": "read_only_network_snapshot",
            "scope": "network view visible to the running Pandamonium service",
            "platform": platform,
            "hostname": socket.gethostname(),
            "inspection_available": bool(successful),
            "successful_probes": successful,
            "probes": probes,
        }
        if not successful:
            snapshot["limitation"] = (
                "No approved network probe completed successfully; report this limitation."
            )
        rendered = _truncate(
            json.dumps(snapshot, ensure_ascii=False, indent=2),
            _NETWORK_SNAPSHOT_CHARS,
        )
        # Return only the canonical formatter fields. Keeping the same snapshot
        # duplicated as additional structured keys would feed it to the model a
        # second time through format_tool_result.
        result = {"output": rendered, "exit_code": 0 if successful else 1}
        if not successful:
            result["error"] = snapshot["limitation"]
        return result
