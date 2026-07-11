#!/usr/bin/env python3
from __future__ import annotations

import grp
import json
import os
import shutil
import socketserver
import subprocess
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SOCKET_PATH = Path(os.getenv("JARVIS_VPS_OBSERVER_SOCKET", "/run/jarvis-vps-observer/observer.sock"))
SOCKET_GROUP = os.getenv("JARVIS_VPS_OBSERVER_GROUP", "jarvis-observer")
MAX_OUTPUT = 120_000
SERVICE_ALLOWLIST = {
    value.strip()
    for value in os.getenv(
        "JARVIS_VPS_OBSERVER_SERVICES",
        "containerd.service,docker.service,jarvis-vps-codex.service,jarvis-vps-observer.service,ssh.service,tailscaled.service",
    ).split(",")
    if value.strip()
}
DEPLOY_ROOTS = tuple(
    Path(value)
    for value in os.getenv("JARVIS_VPS_DEPLOY_ROOTS", "/var/www:/srv:/opt").split(":")
    if value
)


def _run(args: list[str], timeout: int = 12) -> dict:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "output": (result.stdout + result.stderr)[:MAX_OUTPUT].strip(),
        }
    except FileNotFoundError:
        return {"ok": False, "returncode": 127, "output": f"{args[0]} is not installed"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "returncode": 124, "output": "observer command timed out"}


def _meminfo() -> dict[str, int]:
    values: dict[str, int] = {}
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        key, raw = line.split(":", 1)
        values[key] = int(raw.strip().split()[0]) * 1024
    return values


def observe_health(_query: dict[str, list[str]]) -> dict:
    uptime = float(Path("/proc/uptime").read_text(encoding="utf-8").split()[0])
    disk = shutil.disk_usage("/")
    return {
        "hostname": os.uname().nodename,
        "uptime_seconds": int(uptime),
        "load": list(os.getloadavg()),
        "disk": {"total": disk.total, "used": disk.used, "free": disk.free},
    }


def observe_resources(_query: dict[str, list[str]]) -> dict:
    memory = _meminfo()
    processes = _run(["ps", "-eo", "pid,user,comm,%cpu,%mem", "--sort=-%cpu"])
    processes["output"] = "\n".join(processes["output"].splitlines()[:26])
    return {
        "load": list(os.getloadavg()),
        "memory": {
            "total": memory.get("MemTotal", 0),
            "available": memory.get("MemAvailable", 0),
            "swap_total": memory.get("SwapTotal", 0),
            "swap_free": memory.get("SwapFree", 0),
        },
        "processes": processes,
    }


def observe_ports(_query: dict[str, list[str]]) -> dict:
    return _run(["ss", "-ltnupH"])


def observe_services(query: dict[str, list[str]]) -> dict:
    target = str((query.get("target") or [""])[0])
    targets = [target] if target else sorted(SERVICE_ALLOWLIST)
    if any(service not in SERVICE_ALLOWLIST for service in targets):
        raise ValueError("service_not_allowed")
    result = {}
    for service in targets:
        state = _run(["systemctl", "show", service, "--property=ActiveState,SubState,UnitFileState", "--value"])
        result[service] = state
    return result


def observe_journal(query: dict[str, list[str]]) -> dict:
    target = str((query.get("target") or [""])[0])
    if target not in SERVICE_ALLOWLIST:
        raise ValueError("service_not_allowed")
    try:
        lines = max(1, min(100, int((query.get("lines") or ["80"])[0])))
    except ValueError as exc:
        raise ValueError("invalid_line_count") from exc
    return _run(["journalctl", "-u", target, "-n", str(lines), "--no-pager", "-o", "short-iso"])


def observe_containers(_query: dict[str, list[str]]) -> dict:
    return _run([
        "docker", "ps", "--all", "--format",
        "{{json .}}",
    ])


def observe_nginx(_query: dict[str, list[str]]) -> dict:
    return _run(["nginx", "-t"])


def observe_deployments(_query: dict[str, list[str]]) -> dict:
    roots = []
    for root in DEPLOY_ROOTS:
        if not root.exists():
            continue
        for child in sorted(root.iterdir())[:80]:
            if child.is_dir():
                stat = child.stat()
                roots.append({"path": str(child), "modified_at": int(stat.st_mtime)})
    return {"roots": roots}


ACTIONS = {
    "health": observe_health,
    "resources": observe_resources,
    "ports": observe_ports,
    "services": observe_services,
    "journal": observe_journal,
    "containers": observe_containers,
    "nginx": observe_nginx,
    "deployments": observe_deployments,
}


class Handler(BaseHTTPRequestHandler):
    server_version = "JarvisVpsObserver/1.0"

    def log_message(self, _fmt: str, *_args) -> None:
        return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        parts = parsed.path.strip("/").split("/")
        if len(parts) != 3 or parts[:2] != ["v1", "observe"] or parts[2] not in ACTIONS:
            self._json(404, {"error": "unknown_observer_action"})
            return
        try:
            result = ACTIONS[parts[2]](parse_qs(parsed.query))
            self._json(200, {"action": parts[2], "result": result})
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
        except Exception as exc:
            self._json(500, {"error": type(exc).__name__})

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def main() -> None:
    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    SOCKET_PATH.unlink(missing_ok=True)
    server = Server(str(SOCKET_PATH), Handler)
    group_id = grp.getgrnam(SOCKET_GROUP).gr_gid
    os.chown(SOCKET_PATH, 0, group_id)
    os.chmod(SOCKET_PATH, 0o660)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        SOCKET_PATH.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
