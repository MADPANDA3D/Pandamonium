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
        ("resolver", ("scutil", "--dns")),
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
        "scutil": ("/usr/sbin/scutil",),
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
_PROBE_READ_CHUNK_BYTES: Final[int] = 4_096
_LINUX_KERNEL_OUTPUT_CHARS: Final[int] = 2_200
_NETWORK_SNAPSHOT_CHARS: Final[int] = 9_000
_LINUX_KERNEL_TABLE_CHARS: Final[int] = 300
_LINUX_KERNEL_TABLES: Final[tuple[tuple[str, str], ...]] = (
    ("resolver", "/etc/resolv.conf"),
    ("ipv4_routes", "/proc/net/route"),
    ("neighbors", "/proc/net/arp"),
    ("ipv6_routes", "/proc/net/ipv6_route"),
    ("interfaces", "/proc/net/dev"),
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


async def _read_probe_stream(stream: asyncio.StreamReader) -> tuple[bytes, int]:
    """Drain a subprocess pipe while retaining only a fixed byte prefix."""
    retained = bytearray()
    total_bytes = 0
    while chunk := await stream.read(_PROBE_READ_CHUNK_BYTES):
        total_bytes += len(chunk)
        remaining = _PROBE_OUTPUT_CHARS - len(retained)
        if remaining > 0:
            retained.extend(chunk[:remaining])
    return bytes(retained), total_bytes


def _decode_probe_stream(retained: bytes, total_bytes: int) -> str:
    """Decode a bounded pipe prefix and identify omitted bytes within the cap."""
    text = retained.decode("utf-8", errors="replace").strip()
    if total_bytes <= len(retained):
        return text
    suffix = f"\n... (truncated, {total_bytes} bytes total)"
    return text[:max(0, _PROBE_OUTPUT_CHARS - len(suffix))] + suffix


async def _terminate_probe(process, reader_tasks: tuple[asyncio.Task, ...]) -> None:
    """Stop a probe and await all local tasks during timeout or cancellation."""
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    await process.wait()
    for task in reader_tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*reader_tasks, return_exceptions=True)


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
    stdout_task = asyncio.create_task(_read_probe_stream(process.stdout))
    stderr_task = asyncio.create_task(_read_probe_stream(process.stderr))
    reader_tasks = (stdout_task, stderr_task)
    try:
        await asyncio.wait_for(process.wait(), timeout=_PROBE_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        await _terminate_probe(process, reader_tasks)
        return {
            "available": True,
            "command": " ".join(argv),
            "error": f"probe timed out after {_PROBE_TIMEOUT_SECONDS:g} seconds",
            "exit_code": None,
        }
    except asyncio.CancelledError:
        await _terminate_probe(process, reader_tasks)
        raise

    (stdout, stdout_total), (stderr, stderr_total) = await asyncio.gather(*reader_tasks)
    decoded_stdout = _decode_probe_stream(stdout, stdout_total)
    decoded_stderr = _decode_probe_stream(stderr, stderr_total)
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
            content = table.read(_LINUX_KERNEL_TABLE_CHARS + 1)
        if len(content) > _LINUX_KERNEL_TABLE_CHARS:
            suffix = "\n... (truncated at fixed table budget)"
            content = content[:_LINUX_KERNEL_TABLE_CHARS - len(suffix)] + suffix
        return content.strip()
    except OSError:
        return ""


def _linux_kernel_probe() -> dict:
    """Collect the container-visible Linux network view without OS packages."""
    try:
        interfaces = [name for _index, name in socket.if_nameindex()]
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
            json.dumps(data, ensure_ascii=False, indent=2),
            _LINUX_KERNEL_OUTPUT_CHARS,
        ),
        **({} if available else {"error": "Linux kernel network data is unavailable"}),
    }


def _json_content_chars(value: str) -> int:
    """Return the rendered size of a JSON string excluding its quote pair."""
    return len(json.dumps(value, ensure_ascii=False)) - 2


def _truncate_to_json_budget(value: str, budget: int) -> str:
    """Fit one string into a serialized-content budget without breaking JSON."""
    if budget <= 0:
        return ""
    if _json_content_chars(value) <= budget:
        return value

    best = ""
    low = 1
    high = len(value)
    while low <= high:
        midpoint = (low + high) // 2
        candidate = _truncate(value, midpoint)
        if _json_content_chars(candidate) <= budget:
            best = candidate
            low = midpoint + 1
        else:
            high = midpoint - 1
    return best


def _render_bounded_snapshot(snapshot: dict) -> str:
    """Share the output budget across probes while keeping complete JSON."""
    rendered = json.dumps(snapshot, ensure_ascii=False, indent=2)
    if len(rendered) <= _NETWORK_SNAPSHOT_CHARS:
        return rendered

    bounded = {
        **snapshot,
        "probes": {
            name: dict(result)
            for name, result in snapshot.get("probes", {}).items()
        },
    }
    slots: list[tuple[dict, str, str, int]] = []
    for result in bounded["probes"].values():
        for key in ("output", "error"):
            value = result.get(key)
            if isinstance(value, str) and value:
                slots.append((result, key, value, _json_content_chars(value)))
                result[key] = ""

    empty_rendered = json.dumps(bounded, ensure_ascii=False, indent=2)
    remaining_budget = max(0, _NETWORK_SNAPSHOT_CHARS - len(empty_rendered))
    allocations = [0] * len(slots)
    pending = set(range(len(slots)))
    while pending:
        fair_share = remaining_budget // len(pending)
        satisfied = [index for index in pending if slots[index][3] <= fair_share]
        if not satisfied:
            for index in pending:
                allocations[index] = fair_share
            break
        for index in satisfied:
            demand = slots[index][3]
            allocations[index] = demand
            remaining_budget -= demand
            pending.remove(index)

    for allocation, (result, key, value, _demand) in zip(allocations, slots):
        result[key] = _truncate_to_json_budget(value, allocation)
    return json.dumps(bounded, ensure_ascii=False, indent=2)


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
        rendered = _render_bounded_snapshot(snapshot)
        # Return only the canonical formatter fields. Keeping the same snapshot
        # duplicated as additional structured keys would feed it to the model a
        # second time through format_tool_result.
        result = {"output": rendered, "exit_code": 0 if successful else 1}
        if not successful:
            result["error"] = snapshot["limitation"]
        return result
