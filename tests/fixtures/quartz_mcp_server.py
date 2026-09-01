#!/usr/bin/env python3
"""Reference-neutral JSON-lines MCP fixture for native stdio ownership tests."""

import json
import sys


TOOL = {
    "name": "inspect_crystal",
    "description": "Inspect one reference-neutral crystal fixture.",
    "inputSchema": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
}


def response(message):
    method = message.get("method")
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": message.get("id"),
            "result": {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "quartz", "version": "1" * 40},
            },
        }
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": message.get("id"),
            "result": {"tools": [TOOL]},
        }
    if method == "tools/call":
        return {
            "jsonrpc": "2.0",
            "id": message.get("id"),
            "result": {
                "content": [{"type": "text", "text": "quartz-ok"}],
                "isError": False,
            },
        }
    return {
        "jsonrpc": "2.0",
        "id": message.get("id"),
        "error": {"code": -32601, "message": "Method not found"},
    }


for line in sys.stdin:
    message = json.loads(line)
    result = response(message)
    if result is not None:
        print(json.dumps(result, separators=(",", ":")), flush=True)
