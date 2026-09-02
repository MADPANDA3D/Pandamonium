"""Read-only MCP wrapper for operator-admitted Graphify roots."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.graphify_runtime import (  # noqa: E402
    GraphifyConfigurationError,
    configured_roots,
    graph_status,
    query_graph,
)


server = Server("graphify_guarded")


@server.list_tools()
async def list_tools() -> list[Tool]:
    root_ids = sorted(configured_roots())
    return [
        Tool(
            name="graphify_status",
            description="Report whether an explicitly admitted repository graph is ready.",
            inputSchema={
                "type": "object",
                "properties": {"root_id": {"type": "string", "enum": root_ids}},
                "required": ["root_id"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="graphify_query",
            description="Query one prebuilt local code graph by configured root ID; never scans a path.",
            inputSchema={
                "type": "object",
                "properties": {
                    "root_id": {"type": "string", "enum": root_ids},
                    "question": {"type": "string", "maxLength": 2000},
                    "mode": {"type": "string", "enum": ["bfs", "dfs"], "default": "bfs"},
                    "depth": {"type": "integer", "minimum": 1, "maximum": 6, "default": 3},
                    "token_budget": {
                        "type": "integer",
                        "minimum": 100,
                        "maximum": 4000,
                        "default": 2000,
                    },
                },
                "required": ["root_id", "question"],
                "additionalProperties": False,
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        if name == "graphify_status":
            result = json.dumps(graph_status(arguments.get("root_id", "")), sort_keys=True)
        elif name == "graphify_query":
            result = query_graph(
                arguments.get("root_id", ""),
                arguments.get("question", ""),
                mode=arguments.get("mode", "bfs"),
                depth=arguments.get("depth", 3),
                token_budget=arguments.get("token_budget", 2000),
            )
        else:
            result = "Error: graphify capability unavailable"
    except GraphifyConfigurationError as exc:
        result = f"Error: {exc}"
    return [TextContent(type="text", text=result)]


async def run() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(run())
