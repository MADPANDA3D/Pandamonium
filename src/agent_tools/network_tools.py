"""Constrained, read-only inspection of the running host's network view."""

from __future__ import annotations

import asyncio
import json
import os
import socket
from typing import Final

from src.tool_utils import _truncate


NETWORK_PROBES: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("interfaces", ("ip", "-brief", "address", "show")),
    ("ipv4_routes", ("ip", "-4", "route", "show")),
    ("ipv6_routes", ("ip", "-6", "route", "show")),
    ("neighbors", ("ip", "neighbor", "show")),
    ("tailscale", ("tailscale", "status")),
)

_TRUSTED_EXECUTABLES: Final[dict[str, tuple[str, ...]]] = {
    "ip": ("/usr/sbin/ip", "/usr/bin/ip", "/sbin/ip", "/bin/ip"),
    "tailscale": ("/usr/bin/tailscale", "/usr/local/bin/tailscale"),
}
_PROBE_TIMEOUT_SECONDS: Final[float] = 5.0
_PROBE_OUTPUT_CHARS: Final[int] = 8_000


def _resolve_executable(name: str) -> str | None:
    """Resolve only an allowlisted binary from fixed system paths."""
    for candidate in _TRUSTED_EXECUTABLES.get(name, ()):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


async def _run_probe(argv: tuple[str, ...]) -> dict:
    executable = _resolve_executable(argv[0])
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


class NetworkInspectionTool:
    """Collect a bounded network snapshot without accepting commands or paths."""

    async def execute(self, content: str, ctx: dict) -> dict:
        del content, ctx  # Calls are intentionally parameterless and owner-scoped upstream.

        probes = {
            name: await _run_probe(argv)
            for name, argv in NETWORK_PROBES
        }
        successful = [
            name
            for name, result in probes.items()
            if result.get("available") and result.get("exit_code") == 0
        ]
        snapshot = {
            "capability": "read_only_network_snapshot",
            "scope": "network view visible to the running Pandamonium service",
            "hostname": socket.gethostname(),
            "inspection_available": bool(successful),
            "successful_probes": successful,
            "probes": probes,
        }
        rendered = json.dumps(snapshot, ensure_ascii=False, indent=2)
        if successful:
            return {**snapshot, "output": rendered, "exit_code": 0}
        return {
            **snapshot,
            "output": rendered,
            "error": "No approved network probe completed successfully; report this limitation.",
            "exit_code": 1,
        }
