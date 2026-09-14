"""Governed agent SSH tool (MAD-936).

Runs bounded list/read/run operations against an operator-saved SSH
connection. The agent never supplies a target: it names a saved connection by
id or label, and the connection's allowlist gates `run`. Every attempt is
audited through ``src.ssh_connections.execute_agent_operation`` and the result
carries a node-scoped citation (connection + path/command).

No interactive TTY, no arbitrary target, no secret material in results.
"""

from __future__ import annotations

import asyncio
import json

from src import ssh_connections as ssh
from src.tool_utils import _parse_tool_args


class SshNodeTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        raw = str(content or "").strip()
        args = None
        if raw:
            try:
                parsed = json.loads(raw)
            except (TypeError, ValueError):
                parsed = None
            if isinstance(parsed, dict):
                args = parsed
        if args is None:
            try:
                parsed = _parse_tool_args(raw)
            except Exception:
                parsed = None
            if isinstance(parsed, dict):
                args = parsed
        if not isinstance(args, dict):
            return {
                "error": (
                    "ssh_node arguments must be a JSON object, e.g. "
                    '{"connection": "Home VPS", "action": "list", "path": "/srv"}.'
                ),
                "exit_code": 1,
                "code": "invalid_arguments",
            }

        action = str(args.get("action") or "list").strip().lower()
        owner = str((ctx or {}).get("owner") or "") or None
        if action not in ssh.AGENT_ACTIONS:
            return {
                "error": "The SSH action must be list, read, or run.",
                "exit_code": 1,
                "code": "invalid_action",
            }

        reference = str(args.get("connection") or args.get("connection_id") or "").strip()
        try:
            connection = await asyncio.to_thread(ssh.resolve_saved_connection, reference)
        except ssh.SshAgentError as exc:
            ssh.record_ssh_audit(
                "",
                f"agent-{action}",
                exc.reason,
                "unresolved_connection",
                owner,
                detail={"source": "agent", "action": action, "state": exc.reason},
            )
            return {"error": exc.message, "exit_code": 1, "code": exc.code}

        try:
            result = await asyncio.to_thread(
                ssh.execute_agent_operation,
                connection,
                action,
                args,
                actor=owner,
            )
        except ssh.SshAgentError as exc:
            return {"error": exc.message, "exit_code": 1, "code": exc.code}
        except Exception:
            return {
                "error": "The SSH operation failed before it could complete.",
                "exit_code": 1,
                "code": "operation_failed",
            }

        return {
            "output": result["output"],
            "exit_code": 0,
            "source": result["source"],
            "citation": result["citation"],
            "limits": result["limits"],
            "truncated": result["truncated"],
        }
