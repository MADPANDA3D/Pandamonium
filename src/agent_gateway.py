"""Authenticated MCP projection of Pandamonium's existing per-turn tool runtime."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
import uuid
from urllib.parse import urlsplit

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse

from src.action_protocol import classify_target, compose_capability_catalog, normalize_action_call, validate_action_call
from src.authority_protocol import action_effect_for, authority_store, operator_identity, redact_secrets
from src.jarvis_agent import internal_token_valid, require_session_owner
from src.settings import get_setting
from src.agent_tools import FUNCTION_TOOL_SCHEMAS, function_call_to_tool_block
from src.tool_security import blocked_tools_for_owner
from src.tool_utils import get_mcp_manager


_public_host = urlsplit(os.getenv("APP_PUBLIC_URL", "")).netloc
gateway = FastMCP("Pandamonium", stateless_http=True, json_response=True, streamable_http_path="/",
                  transport_security=TransportSecuritySettings(
                      allowed_hosts=["127.0.0.1", "127.0.0.1:*", "localhost", "localhost:*", *([_public_host] if _public_host else [])],
                      allowed_origins=["http://127.0.0.1:*", "http://localhost:*", *([os.environ["APP_PUBLIC_URL"].rstrip("/")] if _public_host else [])],
                  ))
# ponytail: contexts are process-local; after restart the next user turn renews them.
_contexts: dict[str, dict] = {}


def register_context(context: dict, *, owner: str, workspace=None, tool_policy=None,
                     extension_bridge=None, active_document=None, active_email=None,
                     used_memories=None, context_manifest=None) -> str:
    require_session_owner(context["session_id"], owner)
    now = time.monotonic()
    for key in list(_contexts):
        if _contexts[key]["expires"] < now:
            del _contexts[key]
    if len(_contexts) >= 1024:
        del _contexts[next(iter(_contexts))]  # Oldest turn expires; new messages stay available.
    key = secrets.token_urlsafe(32)
    _contexts[key] = {
        "owner": owner, "expires": now + 24 * 60 * 60,
        "workspace": workspace, "tool_policy": tool_policy,
        "extension_bridge": extension_bridge or {},
        "public": {**{k: v for k, v in context.items() if k != "images"},
                   "active_document": {"id": active_document.id, "title": active_document.title}
                   if active_document is not None else None,
                   "active_email": active_email, "used_memories": used_memories or [],
                   "context_manifest": context_manifest or {}},
        "lock": asyncio.Lock(),
    }
    return key


def _context(context_id: str) -> dict:
    row = _contexts.get(context_id)
    if not row or row["expires"] < time.monotonic():
        raise ValueError("Pandamonium context expired or unavailable. Send a new message through Pandamonium.")
    require_session_owner(row["public"]["session_id"], row["owner"])
    return row


def _catalog(row: dict) -> tuple[dict, dict]:
    from src.agent_loop import _load_mcp_disabled_map

    manager = get_mcp_manager()
    schemas = list(FUNCTION_TOOL_SCHEMAS)
    policies = {}
    if manager:
        schemas.extend(manager.get_all_openai_schemas(_load_mcp_disabled_map()))
        policies.update(manager.get_action_policies())
    bridge = row["extension_bridge"]
    schemas.extend(bridge.get("extra_tool_schemas") or [])
    policies.update(bridge.get("extension_capabilities") or {})
    disabled = blocked_tools_for_owner(row["owner"]) | set(get_setting("disabled_tools", []) or [])
    policy = row["tool_policy"]
    schemas = [schema for schema in schemas if schema["function"]["name"] not in disabled
               and not (policy and policy.blocks(schema["function"]["name"]))]
    return compose_capability_catalog(schemas), policies


@gateway.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False))
def read_context(context_id: str) -> dict:
    """Read this Pandamonium turn's interface, session, attachment and selected-document context.

    context_id is supplied in the incoming Pandamonium message. Native agent configuration remains in effect.
    """
    return redact_secrets(_context(context_id)["public"])


@gateway.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False))
def discover(context_id: str, query: str = "", offset: int = 0) -> dict:
    """Discover authorized Pandamonium tools and exact argument schemas, 20 at a time.

    Search names/descriptions with query; use next_offset to continue. Availability is checked again on execution.
    """
    catalog, _ = _catalog(_context(context_id))
    if offset < 0 or len(query) > 200:
        raise ValueError("Invalid discovery query.")
    words = query.lower().split()
    matches = [schema for schema in catalog["schemas"].values()
               if all(word in json.dumps(schema).lower() for word in words)]
    return {"version": catalog["version"], "tools": matches[offset:offset + 20],
            "next_offset": offset + 20 if offset + 20 < len(matches) else None,
            "total": len(matches)}


@gateway.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True))
async def execute(context_id: str, name: str, arguments: dict) -> dict:
    """Invoke a discovered Pandamonium tool in the originating user's session.

    Uses Pandamonium validation and authority checks. An approval_required result means the action did not execute;
    obtain approval through Pandamonium before trying again. Never substitute native tools to bypass that decision.
    """
    return await _execute(context_id, name, arguments)


@gateway.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True))
async def read_tool(context_id: str, name: str, arguments: dict) -> dict:
    """Invoke a discovered read-only Pandamonium tool. Effectful or unclassified calls are rejected.

    This preserves native read-only policies without marking the general execution gateway as read-only.
    """
    return await _execute(context_id, name, arguments, read_only=True)


async def _execute(context_id: str, name: str, arguments: dict, *, read_only: bool = False) -> dict:
    from src.agent_tools import ToolBlock
    from src.tool_execution import execute_tool_block

    row = _context(context_id)
    async with row["lock"]:
        catalog, policies = _catalog(row)
        session = row["public"]["session_id"]
        call = normalize_action_call(
            request_id=str(uuid.uuid4()), call_id=str(uuid.uuid4()),
            agent_id=row["public"]["presenter"], actor="pandamonium:agent-gateway",
            capability_version=catalog["version"], name=name, arguments=arguments,
            target=classify_target(name, mcp_names=set(), extension_ids={
                key: value.get("extension_id", "") for key, value in policies.items()
            }),
            capability_policy=policies.get(name), authority_ref=None,
        )
        error = validate_action_call(call, catalog)
        if error:
            return {"status": "denied", "error": error}
        if read_only and action_effect_for(call) != "read":
            return {"status": "denied", "error": "This tool call is not proven read-only. Use execute with the required approval."}
        decision = authority_store.decide(
            call, operator_id=operator_identity(row["owner"]), session_id=session,
            configured_workspace=row["workspace"],
            native_approval_gate=name in {"send_email", "reply_to_email", "bulk_email"}
            and bool(get_setting("agent_email_confirm", True)),
        )
        if decision["decision"] != "allow":
            return {"status": decision["decision"], "decision": redact_secrets(decision)}
        document_id = (row["public"].get("active_document") or {}).get("id")
        if not document_id and (name in {"edit_document", "update_document", "suggest_document"}
                                or (name == "manage_documents" and call["arguments"].get("action") == "delete"
                                    and not any(call["arguments"].get(key) for key in ("document_id", "id", "uid")))):
            return {"status": "denied", "error": "Select a document in Pandamonium and send a new message first."}
        content = json.dumps(call["arguments"])
        bridge = row["extension_bridge"]
        extra_names = {schema["function"]["name"] for schema in bridge.get("extra_tool_schemas") or []}
        block = ToolBlock(name, content) if name.startswith("mcp__") or name in extra_names else function_call_to_tool_block(name, content)
        if block is None:
            return {"status": "denied", "error": "Tool is not executable."}
        async def progress(_event):
            pass
        result = None
        if bridge.get("tool_executor"):
            result = await bridge["tool_executor"](block, progress)
        if result is None:
            result = await execute_tool_block(
                block, session_id=session, owner=row["owner"], workspace=row["workspace"],
                tool_policy=row["tool_policy"], presenter=row["public"]["presenter"],
                document_id=document_id,
            )
        description, output = result
        return {"description": description, "result": redact_secrets(output)}


_mcp_app = gateway.streamable_http_app()


async def authenticated_gateway(scope, receive, send):
    headers = dict(scope.get("headers") or [])
    if not internal_token_valid(headers.get(b"authorization", b"").decode("latin-1")):
        await JSONResponse({"error": "Unauthorized"}, status_code=401)(scope, receive, send)
        return
    await _mcp_app(scope, receive, send)
