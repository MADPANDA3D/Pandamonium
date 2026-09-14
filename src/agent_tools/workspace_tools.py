"""Agent tool: set / clear / report the active workspace (MAD-883).

The workspace picker and ``/workspace`` slash command are UI surfaces; until
this tool existed the in-app agent could only read the binding
(``get_workspace``) and had no way to change it. ``manage_workspace`` changes
the server-persisted per-session workspace so the setting is visible to every
client that opens the chat.

Security is unchanged: the path is validated with the same ``vet_workspace``
used at chat-bind time (real directory, not a filesystem root, not a sensitive
path), and the tool stays admin-only because the workspace-backed file/shell
tools are admin-only.
"""
from __future__ import annotations

import json

from src.tool_execution import get_active_workspace, vet_workspace
from src.tool_security import owner_is_admin_or_single_user
from src.workspace_store import (
    clear_session_workspace,
    read_session_workspace,
    set_session_workspace,
)

_SHOW_ACTIONS = {"show", "status", "get", "info", ""}
_SET_ACTIONS = {"set", "use", "cd", "switch"}
_CLEAR_ACTIONS = {"clear", "unset", "none", "off", "reset"}


def _parse_args(content: str) -> dict:
    """Accept the JSON form (preferred) or a bare path / action line."""
    raw = str(content or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        parsed = None
    if isinstance(parsed, dict) and parsed:
        return parsed
    first = raw.split("\n")[0].strip()
    low = first.lower()
    if low in _SHOW_ACTIONS | _SET_ACTIONS | _CLEAR_ACTIONS:
        return {"action": low}
    if first:
        return {"action": "set", "path": first}
    return {}


class ManageWorkspaceTool:
    """Set/clear/report the active workspace for this chat."""

    async def execute(self, content: str, ctx: dict) -> dict:
        ctx = ctx or {}
        owner = str(ctx.get("owner") or "") or None
        session_id = str(ctx.get("session_id") or "") or None

        if not owner_is_admin_or_single_user(owner):
            return {
                "error": (
                    "The workspace can only be changed by an admin on this "
                    "installation. Ask an admin to sign in and set it."
                ),
                "exit_code": 1,
                "code": "admin_only",
            }

        args = _parse_args(content)
        raw_action = args.get("action", "")
        action = str(raw_action or "show").strip().lower()
        if action not in _SHOW_ACTIONS | _SET_ACTIONS | _CLEAR_ACTIONS:
            action = "set" if args.get("path") else "show"

        if action in _SHOW_ACTIONS:
            current = get_active_workspace() or read_session_workspace(session_id)
            if current:
                return {
                    "output": (
                        f"Active workspace: {current}\n"
                        "(File tools are confined to this folder for this chat; "
                        "the shell starts here but is not sandboxed.)"
                    ),
                    "workspace": current,
                    "session_id": session_id,
                    "exit_code": 0,
                }
            return {
                "output": (
                    "No workspace is set for this chat. File tools use the "
                    "default allowed roots. Call manage_workspace with "
                    '{"action": "set", "path": "/abs/folder"} to bind one.'
                ),
                "workspace": "",
                "session_id": session_id,
                "exit_code": 0,
            }

        if action in _SET_ACTIONS:
            requested = str(
                args.get("path")
                or args.get("workspace")
                or args.get("folder")
                or ""
            ).strip()
            resolved = vet_workspace(requested)
            if not resolved:
                return {
                    "error": (
                        f"'{requested}' is not a usable workspace folder. It must "
                        "be an existing directory, not a filesystem root or a "
                        "sensitive path (.ssh, .gnupg, ...)."
                    ),
                    "exit_code": 1,
                    "code": "invalid_workspace",
                }
            if not session_id:
                return {
                    "error": "No chat session is bound to this turn; cannot persist a workspace.",
                    "exit_code": 1,
                    "code": "no_session",
                }
            if not set_session_workspace(session_id, resolved):
                return {
                    "error": "Could not persist the workspace for this chat.",
                    "exit_code": 1,
                    "code": "persist_failed",
                }
            return {
                "output": (
                    f"Workspace set to {resolved}. It applies to the remaining "
                    "tool calls in this request and to this chat from now on; "
                    "the workspace pill updates on the client."
                ),
                "workspace": resolved,
                "workspace_changed": True,
                "session_id": session_id,
                "exit_code": 0,
            }

        if not session_id:
            return {
                "error": "No chat session is bound to this turn; cannot clear a workspace.",
                "exit_code": 1,
                "code": "no_session",
            }
        clear_session_workspace(session_id)
        return {
            "output": (
                "Workspace cleared. File tools use the default allowed roots "
                "for the rest of this request and for this chat."
            ),
            "workspace": "",
            "workspace_changed": True,
            "session_id": session_id,
            "exit_code": 0,
        }
