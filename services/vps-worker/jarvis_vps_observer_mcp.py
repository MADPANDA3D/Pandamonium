#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from typing import Any

from jarvis_vps_observe import request

SERVER_INFO = {"name": "jarvis-vps-observer", "version": "1.0.0"}
ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}


def _tool(name: str, description: str, properties: dict[str, Any] | None = None, required: list[str] | None = None) -> dict:
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties or {},
            "required": required or [],
            "additionalProperties": False,
        },
        "annotations": ANNOTATIONS,
    }


TOOLS = [
    _tool("server_health", "Read the VPS observer health and hostname."),
    _tool("server_resources", "Read current CPU, memory, disk, and load facts."),
    _tool("server_ports", "Read currently listening network ports."),
    _tool(
        "server_service_status",
        "Read status for one server-allowlisted systemd service.",
        {"target": {"type": "string", "description": "Allowlisted systemd unit name."}},
        ["target"],
    ),
    _tool(
        "server_service_journal",
        "Read a bounded journal tail for one server-allowlisted systemd service.",
        {
            "target": {"type": "string", "description": "Allowlisted systemd unit name."},
            "lines": {"type": "integer", "minimum": 1, "maximum": 200, "default": 80},
        },
        ["target"],
    ),
    _tool("server_containers", "Read bounded container status without Docker socket access."),
    _tool("server_nginx_validation", "Run the fixed read-only nginx configuration validation."),
    _tool("server_deployments", "Read status for root-configured deployment directories."),
]

TOOL_ACTIONS = {
    "server_health": "health",
    "server_resources": "resources",
    "server_ports": "ports",
    "server_service_status": "services",
    "server_service_journal": "journal",
    "server_containers": "containers",
    "server_nginx_validation": "nginx",
    "server_deployments": "deployments",
}


def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def handle(message: dict[str, Any]) -> dict[str, Any] | None:
    request_id = message.get("id")
    method = str(message.get("method") or "")
    params = message.get("params") or {}
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        requested = str(params.get("protocolVersion") or "2024-11-05")
        return _result(request_id, {
            "protocolVersion": requested,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
        })
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": TOOLS})
    if method in {"resources/list", "prompts/list"}:
        return _result(request_id, {"resources" if method.startswith("resources") else "prompts": []})
    if method != "tools/call":
        return _error(request_id, -32601, "Method not found")

    name = str(params.get("name") or "")
    if name not in TOOL_ACTIONS:
        return _error(request_id, -32602, "Unknown observer tool")
    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        return _error(request_id, -32602, "Tool arguments must be an object")
    target = str(arguments.get("target") or "")
    lines = max(1, min(int(arguments.get("lines") or 80), 200))
    try:
        observed = request(TOOL_ACTIONS[name], target=target, lines=lines)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return _result(request_id, {
            "content": [{"type": "text", "text": f"Observer request failed: {str(exc)[:240]}"}],
            "isError": True,
        })
    text = json.dumps(observed, indent=2, sort_keys=True)
    return _result(request_id, {
        "content": [{"type": "text", "text": text}],
        "structuredContent": observed,
        "isError": not bool(observed.get("ok", True)),
    })


def main() -> None:
    for line in sys.stdin:
        try:
            message = json.loads(line)
            response = handle(message)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            response = _error(None, -32700, f"Invalid JSON-RPC request: {str(exc)[:160]}")
        except Exception as exc:
            response = _error(None, -32603, f"Observer MCP failure: {str(exc)[:160]}")
        if response is not None:
            sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
