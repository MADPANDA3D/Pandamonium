"""
mcp_manager.py

Manages connections to MCP (Model Context Protocol) tool servers.
Each server exposes tools that are made available to the agent loop.
"""

import asyncio
import copy
import hashlib
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from src.database import McpServer, SessionLocal

from src.runtime_paths import get_app_root

logger = logging.getLogger(__name__)


def _static_http_headers(oauth_tokens: Optional[str]) -> Optional[Dict[str, str]]:
    """Recover the one supported static HTTP credential from encrypted storage.

    ``McpServer.oauth_tokens`` is encrypted by the database type.  Keeping the
    parsing here lets startup, UI reconnects, and agent reconnects share one
    bounded path without ever placing the bearer value in ordinary MCP config.
    """
    if not oauth_tokens:
        return None
    try:
        payload = json.loads(oauth_tokens)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    token = payload.get("static_bearer_token")
    if not isinstance(token, str):
        return None
    token = token.strip()
    if not token or len(token) > 4096 or any(ord(char) < 32 for char in token):
        return None
    return {"Authorization": f"Bearer {token}"}

def _format_mcp_connection_error(name: str, command: str = "", args: Optional[List[str]] = None, error: Exception = None) -> str:
    """Return a user-actionable MCP connection error message."""
    args = args or []
    raw_error = str(error) if error else "Unknown error"
    command_line = " ".join([command or "", *args]).strip()
    lower_command = command_line.lower()

    if "@playwright/mcp" in lower_command:
        return (
            f"{raw_error}\n\n"
            "Browser MCP could not start. Current Docker images already include the pinned package and Chromium; rebuild the image before retrying. "
            "Native installs can cache the supported package with:\n\n"
            "npx -y @playwright/mcp@0.0.80 --version\n\n"
            "Then install its Chromium runtime as documented and restart Pandamonium."
        )

    return raw_error


def _mcp_server_info(initialized: Any) -> Dict[str, str]:
    """Return only the bounded public identity from an MCP initialize result."""
    raw = getattr(initialized, "serverInfo", None) or getattr(initialized, "server_info", None)
    if raw is None:
        return {}
    if isinstance(raw, dict):
        name, version = raw.get("name"), raw.get("version")
    else:
        name, version = getattr(raw, "name", None), getattr(raw, "version", None)
    result = {}
    for key, value in (("name", name), ("version", version)):
        text = str(value or "").strip()
        if text and len(text) <= 200 and not any(ord(char) < 32 for char in text):
            result[key] = text
    return result


def _mcp_instructions(initialized: Any) -> str:
    """Return bounded public server guidance from the initialize result."""
    raw = getattr(initialized, "instructions", None)
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(raw or ""))
    return re.sub(r"\s+", " ", text).strip()[:4000]


_ROUTING_STOPWORDS = frozenset({
    "a", "an", "and", "api", "for", "from", "in", "integration", "mcp",
    "my", "of", "on", "portal", "server", "service", "the", "to", "tool",
    "tools", "use", "using", "with",
})


def _routing_tokens(value: Any) -> Set[str]:
    return {
        token for token in re.findall(r"[a-z0-9][a-z0-9_-]{1,}", str(value or "").lower())
        if token not in _ROUTING_STOPWORDS
    }


def _tool_name_is_negated(
    query: str, name: str, *, allow_intervening: bool = False
) -> bool:
    """Return whether a named tool appears in a nearby negative clause."""
    query_text = str(query or "").lower().replace("\r\n", "\n").replace("\r", "\n")
    query_text = re.sub(r"[\x00-\x09\x0b-\x1f\x7f]+", " ", query_text)
    name_parts = re.findall(r"[a-z0-9][a-z0-9_-]*", str(name or "").lower())
    if not query_text or not name_parts:
        return False
    separator = (
        r"(?:[ \t_-]+(?:[a-z0-9][a-z0-9_-]*[ \t_-]+){0,6})"
        if allow_intervening
        else r"[.\s]+"
    )
    name_pattern = (
        r"(?<![a-z0-9_-])"
        + separator.join(re.escape(part) for part in name_parts)
        + r"(?![a-z0-9_-])"
    )
    negated: bool | None = None
    for match in re.finditer(name_pattern, query_text):
        clause_prefix = re.split(
            r"(?:[!?;\n]|\.(?=\s|$))", query_text[:match.start()]
        )[-1]
        negations = list(re.finditer(
            r"\b(?:do\s+not|don['’ ]?t|never|avoid|exclude|without|cannot|"
            r"rather\s+than|not(?!\s+only\b)|"
            r"(?:must|should|can|could|would|may|might)\s+not|"
            r"can['’ ]?t|won['’ ]?t|"
            r"(?:mustn|shouldn|couldn|wouldn)['’ ]?t)\b",
            clause_prefix,
        ))
        if not negations:
            negated = False
            continue
        reset_action = (
            r"(?:use|using|call|calling|invoke|invoking|select|selecting|"
            r"include|including|expose|exposing|admit|admitting|run|running|"
            r"execute|executing|choose|choosing)"
        )
        resets = list(re.finditer(
            rf"(?:"
            rf"(?:,\s*|\b(?:and|but|however|instead|rather|then|yet)\s+)"
            rf"(?:(?:actually|please|instead|rather)\s+)?{reset_action}\b|"
            rf"\b(?:do\s+not|don['’ ]?t|never)\s+(?:"
            rf"(?:forget|fail)\s+to\s+{reset_action}|omit|exclude|avoid"
            rf")\b|\b(?:except|other\s+than)\b)",
            clause_prefix,
        ))
        latest_negation = negations[-1]
        latest_reset_end = resets[-1].end() if resets else -1
        negated = latest_negation.start() >= latest_reset_end
    return bool(negated)


# Caps for rendering untrusted MCP tool schemas into the agent prompt (issue #2660).
# MCP servers are third-party/user-added, so field names and parameter counts are
# untrusted input — bound them so an odd or hostile schema cannot distort the prompt.
_MCP_PARAM_MAX = 12   # max params rendered per tool
_MCP_TOKEN_MAX = 40   # max chars per rendered name / type token
_MCP_HINT_MAX = 300   # total-length backstop for the whole hint


def _sanitize_schema_token(value: Any, limit: int = _MCP_TOKEN_MAX) -> str:
    """Make an untrusted JSON-Schema token safe to splice into the prompt.

    Replaces control chars / newlines with a space, collapses whitespace, and
    length-caps the result, so a weird field name or type cannot inject newlines
    or run on. Normal short identifiers pass through unchanged.
    """
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value))
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


def _format_mcp_params(input_schema: Any) -> str:
    """Render an MCP tool's JSON-Schema inputs as a compact prompt hint.

    Without this the agent only sees a tool's name + description and has to
    guess its arguments (issue #2509). Produces e.g.
    ` Args (JSON): {"path": string (required), "limit": integer}` — names,
    coarse types, and required-ness, kept short so it stays prompt-friendly.
    Returns "" when there are no parameters.

    MCP servers are third-party, so names/types are sanitized and the parameter
    count + total length are capped (issue #2660); normal schemas are unaffected.
    """
    if not isinstance(input_schema, dict):
        return ""
    props = input_schema.get("properties")
    if not isinstance(props, dict) or not props:
        return ""
    required = set(input_schema.get("required") or [])
    parts = []
    for pname, pinfo in list(props.items())[:_MCP_PARAM_MAX]:
        pinfo = pinfo if isinstance(pinfo, dict) else {}
        ptype = pinfo.get("type") or "any"
        if isinstance(ptype, list):
            ptype = "|".join(str(x) for x in ptype)
        tag = f'"{_sanitize_schema_token(pname)}": {_sanitize_schema_token(ptype)}'
        if pname in required:
            tag += " (required)"
        parts.append(tag)
    extra = len(props) - len(parts)
    if extra > 0:
        parts.append(f"…+{extra} more")
    hint = " Args (JSON): {" + ", ".join(parts) + "}"
    if len(hint) > _MCP_HINT_MAX:
        hint = hint[:_MCP_HINT_MAX - 1].rstrip() + "…"
    return hint


def _mcp_tool_record(tool: Any) -> Dict[str, Any]:
    """Project one SDK tool into the shared transport-neutral catalog shape."""
    return {
        "name": tool.name,
        "description": tool.description or "",
        "input_schema": tool.inputSchema if hasattr(tool, "inputSchema") else {},
        "annotations": getattr(tool, "annotations", None),
    }


# Tool-name prefixes that denote a read-only/inspection operation. Used to
# classify MCP tools for plan mode when the server provides no readOnlyHint.
# These are PREFIXES, not whole words (matched via str.startswith below), so a
# stem like "summar" intentionally covers "summarise"/"summarize"/"summary".
_MCP_READONLY_VERBS = (
    "list", "get", "read", "search", "fetch", "query", "find", "describe",
    "show", "view", "lookup", "count", "status", "info", "inspect", "summar",
)


def mcp_tool_action_effect(tool: Dict) -> Optional[str]:
    """Map trustworthy MCP annotations to the canonical execution effect.

    Prefer the server's own annotations (readOnlyHint / destructiveHint). When
    absent, fall back to the existing read-name heuristic. Anything that still
    cannot be classified gets no policy and fails closed at execution.
    """
    ann = tool.get("annotations")
    # annotations may be a dict or a pydantic model
    read_hint = None
    destructive = None
    if ann is not None:
        if isinstance(ann, dict):
            read_hint = ann.get("readOnlyHint")
            destructive = ann.get("destructiveHint")
        else:
            read_hint = getattr(ann, "readOnlyHint", None)
            destructive = getattr(ann, "destructiveHint", None)
    if destructive is True:
        return "destructive_or_difficult_to_recover"
    if read_hint is True:
        return "read"
    if read_hint is False and destructive is False:
        return "reversible_write"
    if read_hint is False:
        return None
    # No usable hint — heuristic on the tool name's leading verb.
    name = (tool.get("name") or "").lower()
    return "read" if name.startswith(_MCP_READONLY_VERBS) else None


def mcp_tool_is_readonly(tool: Dict) -> bool:
    """Fail closed unless MCP metadata or the read-name heuristic proves a read."""
    return mcp_tool_action_effect(tool) == "read"


class McpManager:
    """Manages MCP server connections and tool routing."""

    def __init__(self):
        # server_id -> connection state
        self._connections: Dict[str, Dict[str, Any]] = {}
        # server_id -> list of tool schemas
        self._tools: Dict[str, List[Dict]] = {}
        # server_id -> MCP ClientSession
        self._sessions: Dict[str, Any] = {}
        # server_id -> exit stack (for cleanup)
        self._stacks: Dict[str, Any] = {}
        # stdio MCP context managers must exit in the same asyncio task that
        # entered them. Keep that owner task alive until disconnect requests
        # teardown instead of moving its AsyncExitStack across task boundaries.
        self._stdio_owners: Dict[str, Tuple[asyncio.Task, asyncio.Event]] = {}
        # server_id -> background connect task (HTTP transport / OAuth)
        self._connect_tasks: Dict[str, Any] = {}
        # Tracking updates to tools/connections for RAG indexing / prompt cache
        self._generation = 0
        # MCP servers owned by installed extensions remain invisible to the
        # ordinary MCP catalog. Their reconciled schemas are exposed only by
        # the extension engagement and authority path.
        self._extension_servers: Set[str] = set()
        # Portal-owned catalog state.  These caches contain only bounded public
        # routing metadata plus the one selected descriptor per hash; provider
        # data and credentials never enter them.
        self._portal_tool_cache: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._portal_reference_cache: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._portal_proxy_tools: Dict[str, Dict[str, Any]] = {}

    @staticmethod
    def _portal_payload_items(payload: Any) -> List[Dict[str, Any]]:
        """Return list-shaped items from the Portal's bounded envelope."""
        if not isinstance(payload, dict):
            return []
        candidates = [payload.get("items"), payload.get("results")]
        data = payload.get("data")
        if isinstance(data, dict):
            candidates.extend([data.get("items"), data.get("results")])
        for value in candidates:
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return []

    @staticmethod
    def _portal_trace_id(result: Dict[str, Any]) -> str:
        payload = result.get("structured_content")
        if not isinstance(payload, dict):
            return ""
        value = str(payload.get("traceId") or "").strip()
        return value[:160] if re.fullmatch(r"[A-Za-z0-9._:-]+", value) else ""

    @staticmethod
    def _portal_model_projection(payload: Any, max_chars: int = 16_000) -> str:
        """Bound a provider result without collapsing a requested item sample."""
        def project(value: Any, *, depth: int, string_limit: int) -> Any:
            if depth > 8:
                return "[nested data omitted]"
            if isinstance(value, str):
                stripped = value.strip()
                if stripped.startswith(("{", "[")) and len(stripped) <= 1_000_000:
                    try:
                        return project(json.loads(stripped), depth=depth + 1, string_limit=string_limit)
                    except json.JSONDecodeError:
                        pass
                return value[:string_limit] + ("…" if len(value) > string_limit else "")
            if isinstance(value, list):
                return [
                    project(item, depth=depth + 1, string_limit=string_limit)
                    for item in value[:25]
                ]
            if isinstance(value, dict):
                return {
                    str(key)[:120]: project(item, depth=depth + 1, string_limit=string_limit)
                    for key, item in list(value.items())[:40]
                }
            if value is None or isinstance(value, (bool, int, float)):
                return value
            return str(value)[:string_limit]

        rendered = ""
        for string_limit in (800, 400, 200, 100):
            rendered = json.dumps(
                project(payload, depth=0, string_limit=string_limit),
                ensure_ascii=False,
                indent=2,
                default=str,
            )
            if len(rendered) <= max_chars:
                return rendered
        return rendered[:max_chars] + f"\n... (bounded from {len(rendered)} chars)"

    @staticmethod
    def _is_portal_connection(tools: List[Dict[str, Any]]) -> bool:
        names = {str(tool.get("name") or "") for tool in tools}
        return {
            "portal.list_services",
            "portal.find_tools",
            "portal.get_tool_reference",
            "portal.call_read_tool",
        } <= names

    async def _sync_portal_services(self, server_id: str) -> None:
        """Synchronize Portal service identities after the native handshake.

        The Portal exposes no supported bulk downstream-tool catalog.  This
        therefore stores only list_services metadata and leaves tool discovery
        to request-time portal.find_tools calls.
        """
        session = self._sessions.get(server_id)
        conn = self._connections.get(server_id)
        tools = self._tools.get(server_id, [])
        if not session or not conn or not self._is_portal_connection(tools):
            return
        try:
            result = await asyncio.wait_for(
                self._do_call(
                    session,
                    "portal.list_services",
                    {},
                    max_output_bytes=1_000_000,
                ),
                timeout=20,
            )
        except Exception as exc:
            logger.warning(
                "Portal service metadata sync failed for %s: %s",
                server_id,
                type(exc).__name__,
            )
            return
        if result.get("exit_code") != 0:
            return
        payload = result.get("structured_content")
        compact: List[Dict[str, Any]] = []
        for item in self._portal_payload_items(payload):
            service_id = re.sub(
                r"[^a-zA-Z0-9._-]+", "", str(item.get("id") or "")
            )[:120]
            if not service_id:
                continue
            compact.append({
                "id": service_id,
                "name": re.sub(r"\s+", " ", str(item.get("name") or "")).strip()[:160],
                "description": re.sub(
                    r"\s+", " ", str(item.get("description") or "")
                ).strip()[:500],
                "configured": bool(
                    item.get("configured") or item.get("integrationConfigured")
                ),
                "state": re.sub(r"\s+", " ", str(item.get("state") or "")).strip()[:80],
                "catalog_version": re.sub(
                    r"\s+", " ", str(item.get("catalogVersion") or "")
                ).strip()[:160],
            })
        revision = hashlib.sha256(
            json.dumps(compact, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        changed = conn.get("portal_catalog_revision") != revision
        conn["portal_services"] = compact
        conn["portal_catalog_revision"] = revision
        conn["portal_catalog_trace_id"] = self._portal_trace_id(result)
        self.set_connection_catalog_terms(
            server_id,
            [value for item in compact for value in (item["id"], item["name"])],
        )
        if changed:
            self._portal_tool_cache.pop(server_id, None)
            self._portal_reference_cache.pop(server_id, None)
            stale = [
                name for name, row in self._portal_proxy_tools.items()
                if row.get("server_id") == server_id
            ]
            for name in stale:
                self._portal_proxy_tools.pop(name, None)
            self._generation += 1

    def get_portal_index_records(self) -> List[Dict[str, str]]:
        """Project compact verified Portal metadata into the existing index."""
        records: List[Dict[str, str]] = []
        for server_id, conn in self._connections.items():
            if conn.get("status") != "connected":
                continue
            find_name = f"mcp__{server_id}__portal.find_tools"
            for service in conn.get("portal_services") or []:
                if not isinstance(service, dict):
                    continue
                service_id = str(service.get("id") or "")
                records.append({
                    "id": "mcp_portal_service_" + hashlib.sha256(
                        f"{server_id}:{service_id}".encode("utf-8")
                    ).hexdigest()[:24],
                    "tool_name": find_name,
                    "document": (
                        f"Portal service: {service_id} {service.get('name') or ''}. "
                        f"{service.get('description') or ''} "
                        f"Configuration: {service.get('state') or ('configured' if service.get('configured') else 'not configured')}. "
                        f"Catalog version: {service.get('catalog_version') or 'unversioned'}."
                    ).strip(),
                })
            for item in (self._portal_tool_cache.get(server_id) or {}).values():
                records.append({
                    "id": "mcp_portal_tool_" + hashlib.sha256(
                        f"{server_id}:{item.get('service_id')}:{item.get('tool_name')}".encode("utf-8")
                    ).hexdigest()[:24],
                    "tool_name": find_name,
                    "document": (
                        f"Portal downstream tool: {item.get('service_id')} "
                        f"{item.get('tool_name')}. {item.get('description') or ''} "
                        f"Risk: {item.get('risk') or 'unknown'}. "
                        f"Descriptor hash: {item.get('descriptor_hash') or 'unknown'}."
                    ).strip(),
                })
        return records

    async def connect_server(
        self,
        server_id: str,
        name: str,
        transport: str,
        command: Optional[str] = None,
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        url: Optional[str] = None,
        identity_from_env: bool = True,
        headers: Optional[Dict[str, str]] = None,
    ) -> bool:
        """Connect to an MCP server via stdio, SSE, or Streamable HTTP transport."""
        try:
            if transport == "stdio":
                res = await self._connect_stdio(
                    server_id,
                    name,
                    command,
                    args or [],
                    env or {},
                    identity_from_env=identity_from_env,
                )
            elif transport == "sse":
                res = await self._connect_sse(server_id, name, url)
            elif transport == "http":
                if headers:
                    res = await self._start_http_connect(
                        server_id,
                        name,
                        url,
                        headers=headers,
                    )
                else:
                    res = await self._start_http_connect(server_id, name, url)
            else:
                logger.error(f"Unknown MCP transport: {transport}")
                res = False
            if res:
                self._generation += 1
            return res
        except Exception as e:
            logger.error(f"Failed to connect MCP server {name} ({server_id}): {e}")
            error_message = _format_mcp_connection_error(name, command or "", args or [], e)
            self._connections[server_id] = {"status": "error", "error": error_message, "name": name}
            self._generation += 1
            return False

    async def _connect_stdio(
        self,
        server_id: str,
        name: str,
        command: str,
        args: List[str],
        env: Dict[str, str],
        *,
        identity_from_env: bool = True,
    ) -> bool:
        """Connect to an MCP server via stdio transport."""
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
            from contextlib import AsyncExitStack

            server_params = StdioServerParameters(
                command=command,
                args=args,
                env={**os.environ, **env} if env else None,
            )

            loop = asyncio.get_running_loop()
            ready = loop.create_future()
            stop = asyncio.Event()

            async def own_stdio_context() -> None:
                stack = AsyncExitStack()
                try:
                    transport = await stack.enter_async_context(stdio_client(server_params))
                    read_stream, write_stream = transport
                    session = await stack.enter_async_context(
                        ClientSession(read_stream, write_stream)
                    )
                    initialized = await session.initialize()
                    tools_result = await session.list_tools()
                    if not ready.done():
                        ready.set_result((session, initialized, tools_result))
                    await stop.wait()
                except asyncio.CancelledError:
                    if not ready.done():
                        ready.cancel()
                    raise
                except Exception as exc:
                    if not ready.done():
                        ready.set_exception(exc)
                    else:
                        logger.warning(f"MCP stdio owner failed for {server_id}: {exc}")
                finally:
                    try:
                        await stack.aclose()
                    except Exception as exc:
                        logger.warning(f"Error closing MCP server {server_id}: {exc}")

            owner = asyncio.create_task(
                own_stdio_context(), name=f"mcp-stdio-owner:{server_id}"
            )
            self._stdio_owners[server_id] = (owner, stop)
            try:
                session, initialized, tools_result = await ready
            except BaseException:
                stop.set()
                try:
                    await owner
                except (Exception, asyncio.CancelledError):
                    pass
                if self._stdio_owners.get(server_id, (None, None))[0] is owner:
                    self._stdio_owners.pop(server_id, None)
                raise

            tools = []
            for tool in tools_result.tools:
                tools.append(_mcp_tool_record(tool))

            self._sessions[server_id] = session
            self._tools[server_id] = tools
            # Extract identity hints from env vars (e.g. email address, API name)
            # so tool descriptions can distinguish between multiple instances of
            # the same MCP server (e.g. two email accounts).
            identity_hints = []
            for k, v in (env or {}).items() if identity_from_env else ():
                k_lower = k.lower()
                if any(x in k_lower for x in ['email_address', 'account', 'user', 'username']):
                    identity_hints.append(v)
            identity = ", ".join(identity_hints) if identity_hints else ""

            self._connections[server_id] = {
                "status": "connected",
                "name": name,
                "transport": "stdio",
                "tool_count": len(tools),
                "identity": identity,
                "server_info": _mcp_server_info(initialized),
                "instructions": _mcp_instructions(initialized),
            }

            await self._sync_portal_services(server_id)
            logger.info(f"MCP server connected: {name} ({server_id}) - {len(tools)} tools via stdio")
            return True

        except ImportError:
            logger.warning("MCP package not installed. Install with: pip install mcp")
            self._connections[server_id] = {
                "status": "error",
                "error": "mcp package not installed",
                "name": name,
            }
            return False

    async def _connect_sse(self, server_id: str, name: str, url: str) -> bool:
        """Connect to an MCP server via SSE transport."""
        try:
            from mcp import ClientSession
            from mcp.client.sse import sse_client
            from contextlib import AsyncExitStack

            stack = AsyncExitStack()
            registered = False

            try:
                transport = await stack.enter_async_context(sse_client(url))
                read_stream, write_stream = transport
                session = await stack.enter_async_context(ClientSession(read_stream, write_stream))

                initialized = await session.initialize()

                # Discover tools
                tools_result = await session.list_tools()
                tools = []
                for tool in tools_result.tools:
                    tools.append(_mcp_tool_record(tool))

                self._sessions[server_id] = session
                self._stacks[server_id] = stack
                self._tools[server_id] = tools
                self._connections[server_id] = {
                    "status": "connected",
                    "name": name,
                    "transport": "sse",
                    "tool_count": len(tools),
                    "server_info": _mcp_server_info(initialized),
                    "instructions": _mcp_instructions(initialized),
                }

                registered = True

                await self._sync_portal_services(server_id)
                logger.info(f"MCP server connected: {name} ({server_id}) - {len(tools)} tools via SSE")
                return True

            finally:
                if not registered:
                    await stack.aclose()

        except ImportError:
            logger.warning("MCP package not installed. Install with: pip install mcp")
            self._connections[server_id] = {"status": "error", "error": "mcp package not installed", "name": name}
            return False

    async def _start_http_connect(
        self,
        server_id: str,
        name: str,
        url: str,
        wait: float = 8.0,
        *,
        headers: Optional[Dict[str, str]] = None,
    ) -> bool:
        """Begin a Streamable HTTP connect in the background. Returns within
        `wait` seconds: True if it connected (cached-token path), otherwise the
        flow is awaiting browser authorization and status becomes 'needs_auth'."""
        import asyncio
        self._connections[server_id] = {"status": "connecting", "name": name, "transport": "http"}
        task = asyncio.create_task(
            self._connect_http(server_id, name, url, headers=headers)
        )
        self._connect_tasks[server_id] = task
        done, _ = await asyncio.wait({task}, timeout=wait)
        if task in done:
            try:
                return task.result()
            except Exception as e:
                self._connections[server_id] = {"status": "error", "error": str(e), "name": name}
                return False
        # Still running → either awaiting authorization, or discovery/DCR is
        # still in flight. If _on_redirect already published needs_auth+auth_url,
        # leave it; otherwise mark needs_auth (auth_url filled in once it fires).
        from src.mcp_oauth import pop_auth_url
        cur = self._connections.get(server_id, {})
        if cur.get("status") != "needs_auth":
            self._connections[server_id] = {
                "status": "needs_auth", "name": name, "transport": "http",
                "auth_url": pop_auth_url(server_id),
            }
        return False

    async def _connect_http(
        self,
        server_id: str,
        name: str,
        url: str,
        *,
        headers: Optional[Dict[str, str]] = None,
    ) -> bool:
        """Connect over Streamable HTTP with static headers or automatic OAuth."""
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client
            from contextlib import AsyncExitStack
            from src.mcp_oauth import clear_auth_url

            def _on_redirect(auth_url):
                # Publish needs_auth the moment the URL is known, independent of
                # how long discovery/DCR took (may exceed the bounded start wait).
                self._connections[server_id] = {
                    "status": "needs_auth", "name": name, "transport": "http",
                    "auth_url": auth_url,
                }

            stack = AsyncExitStack()
            if headers:
                transport_cm = streamablehttp_client(url, headers=dict(headers))
            else:
                from src.mcp_oauth import build_provider

                provider = build_provider(server_id, url, on_redirect=_on_redirect)
                transport_cm = streamablehttp_client(url, auth=provider)
            transport = await stack.enter_async_context(transport_cm)
            read_stream, write_stream, _get_session_id = transport
            session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
            initialized = await session.initialize()

            tools_result = await session.list_tools()
            tools = []
            for tool in tools_result.tools:
                tools.append(_mcp_tool_record(tool))

            self._sessions[server_id] = session
            self._stacks[server_id] = stack
            self._tools[server_id] = tools
            self._connections[server_id] = {
                "status": "connected", "name": name, "transport": "http",
                "tool_count": len(tools),
                "server_info": _mcp_server_info(initialized),
                "instructions": _mcp_instructions(initialized),
            }
            clear_auth_url(server_id)
            await self._sync_portal_services(server_id)
            # Tools changed (this can complete after connect_server already
            # returned, via the background OAuth flow), so bump the generation
            # to invalidate the tool-prompt cache.
            self._generation += 1
            logger.info(f"MCP server connected: {name} ({server_id}) - {len(tools)} tools via http")
            return True
        except ImportError:
            logger.warning("MCP package not installed. Install with: pip install mcp")
            self._connections[server_id] = {"status": "error", "error": "mcp package not installed", "name": name}
            return False
        except Exception as e:
            from src.authority_protocol import redact_secret_text

            safe_error = redact_secret_text(str(e))[:500]
            logger.error("Failed to connect HTTP MCP server %s (%s): %s", name, server_id, safe_error)
            self._connections[server_id] = {"status": "error", "error": safe_error, "name": name}
            return False

    async def disconnect_server(self, server_id: str):
        """Disconnect from an MCP server."""
        # Cancel any in-flight HTTP/OAuth background connect so it stops
        # publishing status for a server that may be getting deleted.
        task = self._connect_tasks.pop(server_id, None)
        if task is not None and not task.done():
            task.cancel()
        try:
            from src.mcp_oauth import clear_auth_url
            clear_auth_url(server_id)
        except Exception:
            pass

        stdio_owner = self._stdio_owners.pop(server_id, None)
        if stdio_owner:
            owner, stop = stdio_owner
            stop.set()
            try:
                await owner
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"Error closing MCP server {server_id}: {e}")

        stack = self._stacks.pop(server_id, None)
        if stack:
            try:
                await stack.aclose()
            except Exception as e:
                logger.warning(f"Error closing MCP server {server_id}: {e}")

        self._sessions.pop(server_id, None)
        self._tools.pop(server_id, None)
        self._connections.pop(server_id, None)
        self._portal_tool_cache.pop(server_id, None)
        self._portal_reference_cache.pop(server_id, None)
        for name in [
            name for name, row in self._portal_proxy_tools.items()
            if row.get("server_id") == server_id
        ]:
            self._portal_proxy_tools.pop(name, None)
        self._generation += 1
        logger.info(f"MCP server disconnected: {server_id}")

    async def disconnect_all(self):
        """Disconnect from all MCP servers."""
        ids = list(self._sessions.keys())
        for sid in ids:
            await self.disconnect_server(sid)


    async def connect_all_enabled(self):
        db = SessionLocal()
        try:
            servers = db.query(McpServer).filter(McpServer.is_enabled == True).all()

            tasks = [
                asyncio.create_task(self._connect_with_timeout(srv))
                for srv in servers
            ]

            await asyncio.gather(*tasks)
        finally:
            db.close()


    async def _connect_with_timeout(self, srv):
        args = json.loads(srv.args) if srv.args else []
        env = json.loads(srv.env) if srv.env else {}

        try:
            await asyncio.wait_for(
                self.connect_server(
                    server_id=srv.id,
                    name=srv.name,
                    transport=srv.transport,
                    command=srv.command,
                    args=args,
                    env=env,
                    url=srv.url,
                    headers=_static_http_headers(getattr(srv, "oauth_tokens", None)),
                ),
                timeout=20,
            )
        except asyncio.TimeoutError:
            logger.warning("Timed out connecting to %s", srv.name)
            self._connections[srv.id] = {
                "status": "timeout",
                "error": "Timed out after 20 seconds",
                "name": srv.name,
            }

    async def call_tool(
        self,
        qualified_name: str,
        arguments: Dict,
        *,
        timeout_seconds: Optional[float] = None,
        max_output_bytes: Optional[int] = None,
    ) -> Dict:
        """Call an MCP tool by its qualified name (mcp__{server_id}__{tool_name}).

        Returns a result dict compatible with agent_tools format.
        """
        portal_relay = self._portal_proxy_tools.get(qualified_name)
        parts = qualified_name.split("__", 2)
        if len(parts) != 3 or parts[0] != "mcp":
            return {"error": f"Invalid MCP tool name: {qualified_name}", "exit_code": 1}

        server_id = str((portal_relay or {}).get("server_id") or parts[1])
        tool_name = parts[2]
        if portal_relay:
            provider_arguments = dict(arguments or {})
            provider_arguments.update(portal_relay.get("fixed_arguments") or {})
            arguments = {
                "serviceId": portal_relay["service_id"],
                "toolName": portal_relay["tool_name"],
                "arguments": provider_arguments,
            }
            tool_name = "portal.call_read_tool"

        session = self._sessions.get(server_id)
        if not session:
            return {"error": f"MCP server not connected: {server_id}", "exit_code": 1}

        try:
            call = self._do_call(
                session, tool_name, arguments, max_output_bytes=max_output_bytes
            )
            result = (
                await asyncio.wait_for(call, timeout=timeout_seconds)
                if timeout_seconds is not None
                else await call
            )
        except asyncio.TimeoutError:
            return {"error": "MCP tool call timed out", "exit_code": 1}
        except Exception as e:
            # Auto-reconnect for builtin servers whose subprocess may have died
            if self.is_builtin(server_id):
                logger.warning(f"MCP call failed for {qualified_name}, attempting reconnect: {e}")
                reconnected = await self._reconnect_builtin(server_id)
                if reconnected:
                    session = self._sessions.get(server_id)
                    if session:
                        try:
                            retry = self._do_call(
                                session,
                                tool_name,
                                arguments,
                                max_output_bytes=max_output_bytes,
                            )
                            result = (
                                await asyncio.wait_for(retry, timeout=timeout_seconds)
                                if timeout_seconds is not None
                                else await retry
                            )
                        except asyncio.TimeoutError:
                            return {"error": "MCP tool call timed out", "exit_code": 1}
                        except Exception as e2:
                            logger.error(f"MCP tool call failed after reconnect: {qualified_name}: {e2}")
                            return {"error": str(e2), "exit_code": 1}
                    else:
                        return {"error": f"Reconnected but no session for {server_id}", "exit_code": 1}
                else:
                    logger.error(f"MCP reconnect failed for {server_id}")
                    return {"error": f"MCP server crashed and reconnect failed: {server_id}", "exit_code": 1}
            else:
                from src.authority_protocol import redact_secret_text

                safe_error = redact_secret_text(str(e))[:500]
                logger.error("MCP tool call failed: %s: %s", qualified_name, safe_error)
                return {"error": safe_error, "exit_code": 1}

        if portal_relay and isinstance(result, dict):
            result["model_content"] = self._portal_model_projection(
                result.get("structured_content")
            )
            relay_evidence = {
                "service_id": portal_relay["service_id"],
                "tool_name": portal_relay["tool_name"],
                "descriptor_hash": portal_relay.get("descriptor_hash") or "",
                "catalog_version": portal_relay.get("catalog_version") or "",
                "trace_id": self._portal_trace_id(result),
                "arguments": provider_arguments,
                "item_count": self._portal_result_item_count(
                    result.get("structured_content")
                ),
            }
            context = self._portal_result_context(result.get("structured_content"))
            if context:
                relay_evidence["context"] = context
            result["portal_relay"] = relay_evidence
        return result

    async def _do_call(
        self,
        session,
        tool_name: str,
        arguments: Dict,
        *,
        max_output_bytes: Optional[int] = None,
    ) -> Dict:
        """Execute a single MCP tool call and return result dict."""
        result = await session.call_tool(tool_name, arguments)
        content_items = getattr(result, "content", None)
        if not isinstance(content_items, (list, tuple)):
            raise ValueError("MCP result malformed")
        output_parts = []
        images = []
        total_bytes = 0
        for content in content_items:
            if hasattr(content, 'text'):
                value = getattr(content, "text", None)
                if not isinstance(value, str):
                    raise ValueError("MCP result malformed")
                total_bytes += len(value.encode("utf-8"))
                output_parts.append(value)
            elif getattr(content, 'type', '') == 'image' and hasattr(content, 'data'):
                # Image content (e.g. Playwright screenshots)
                mime = getattr(content, 'mimeType', 'image/png')
                data = getattr(content, "data", None)
                if not isinstance(data, str) or not isinstance(mime, str):
                    raise ValueError("MCP result malformed")
                total_bytes += len(data.encode("utf-8"))
                images.append({"data": data, "mimeType": mime})
                output_parts.append(f"[Screenshot captured ({mime})]")
            elif hasattr(content, 'data'):
                data = getattr(content, "data", None)
                if not isinstance(data, (str, int, float, bool, dict, list)):
                    raise ValueError("MCP result malformed")
                value = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
                total_bytes += len(value.encode("utf-8"))
                output_parts.append(value)
            else:
                raise ValueError("MCP result malformed")
            if max_output_bytes is not None and total_bytes > max_output_bytes:
                raise ValueError("MCP result too large")

        output = "\n".join(output_parts)
        is_error = getattr(result, 'isError', False)

        result_dict = {
            "stdout": output if not is_error else "",
            "stderr": output if is_error else "",
            "exit_code": 1 if is_error else 0,
        }
        structured = getattr(result, "structuredContent", None)
        if structured is None:
            structured = getattr(result, "structured_content", None)
        if hasattr(structured, "model_dump"):
            structured = structured.model_dump(mode="json")
        if isinstance(structured, (dict, list, str, int, float, bool)):
            structured_bytes = len(
                json.dumps(structured, ensure_ascii=False).encode("utf-8")
            )
            if max_output_bytes is not None and total_bytes + structured_bytes > max_output_bytes:
                raise ValueError("MCP result too large")
            result_dict["structured_content"] = structured
        if images:
            result_dict["images"] = images
        return result_dict

    def reserve_extension_server(self, server_id: str) -> None:
        if server_id not in self._extension_servers:
            self._extension_servers.add(server_id)
            self._generation += 1

    def release_extension_server(self, server_id: str) -> None:
        if server_id in self._extension_servers:
            self._extension_servers.remove(server_id)
            self._generation += 1

    def is_extension_server(self, server_id: str) -> bool:
        return server_id in self._extension_servers

    def get_server_tools(self, server_id: str) -> List[Dict]:
        return list(self._tools.get(server_id, []))

    def set_connection_catalog_terms(self, server_id: str, values: List[Any]) -> None:
        """Attach bounded public catalog labels to a live connection identity."""
        conn = self._connections.get(server_id)
        if conn is None:
            return
        terms = []
        for value in values[:500]:
            text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or ""))
            text = re.sub(r"\s+", " ", text).strip()[:160]
            if text and text not in terms:
                terms.append(text)
        if conn.get("catalog_terms") != terms:
            conn["catalog_terms"] = terms
            self._generation += 1

    def _portal_connection_for_request(self, query: str) -> str:
        query_tokens = _routing_tokens(query)
        normalized = " ".join(re.findall(r"[a-z0-9._-]+", str(query or "").lower()))
        matches: List[Tuple[int, int, str]] = []
        for index, (server_id, conn) in enumerate(self._connections.items()):
            tools = self._tools.get(server_id, [])
            if conn.get("status") != "connected" or not self._is_portal_connection(tools):
                continue
            identities = [
                str(conn.get("name") or ""),
                str((conn.get("server_info") or {}).get("name") or ""),
            ]
            explicit = 0
            for value in identities:
                phrase = " ".join(re.findall(r"[a-z0-9._-]+", value.lower()))
                if phrase and re.search(rf"(?:^| ){re.escape(phrase)}(?: |$)", normalized):
                    explicit = max(explicit, len(phrase.split()))
            catalog_tokens = _routing_tokens(" ".join(
                str(value or "")
                for service in conn.get("portal_services") or []
                if isinstance(service, dict)
                for value in (service.get("id"), service.get("name"))
            ))
            overlap = len(query_tokens & catalog_tokens)
            if explicit or overlap:
                matches.append((explicit, overlap, server_id))
        matches.sort(key=lambda item: (-item[0], -item[1]))
        return matches[0][2] if matches else ""

    def _portal_service_for_request(self, server_id: str, query: str) -> Dict[str, Any]:
        query_tokens = _routing_tokens(query)
        normalized = str(query or "").lower()
        scored: List[Tuple[int, int, Dict[str, Any]]] = []
        for index, service in enumerate(
            self._connections.get(server_id, {}).get("portal_services") or []
        ):
            if not isinstance(service, dict) or not service.get("configured"):
                continue
            service_id = str(service.get("id") or "").lower()
            name = str(service.get("name") or "").lower()
            tokens = _routing_tokens(f"{service_id} {name} {service.get('description') or ''}")
            explicit = int(bool(
                service_id
                and re.search(rf"(?<![a-z0-9_-]){re.escape(service_id)}(?![a-z0-9_-])", normalized)
            ))
            score = explicit * 100 + len(query_tokens & tokens)
            if score:
                scored.append((score, -index, service))
        scored.sort(key=lambda row: (-row[0], -row[1]))
        return scored[0][2] if scored else {}

    @staticmethod
    def _portal_discovery_query(latest_query: str) -> str:
        """Reduce a request to the outcome phrase Portal search ranks on.

        This is routing normalization, not a provider catalog.  It removes
        negated constraints that otherwise dominate semantic search (for
        example "do not include vectors") and keeps the requested data shape.
        """
        text = re.sub(r"\s+", " ", str(latest_query or "").strip().lower())
        text = re.sub(
            r"\b(?:do\s+not|don't|never|without)\b[^.!?;]*(?:[.!?;]|$)",
            " ",
            text,
        )
        if re.search(r"\bcollections?\b", text) and re.search(
            r"\b(?:inside|contents?|information|examples?|payloads?|points?|records?|items?)\b",
            text,
        ):
            return "collection contents"
        if re.search(r"\bpayloads?\b", text):
            return "payloads"
        if re.search(r"\b(?:messages?|channel history)\b", text):
            return "read messages"
        if re.search(r"\bcollections?\b", text):
            return "collections"
        words = [
            word for word in re.findall(r"[a-z0-9][a-z0-9_-]+", text)
            if word not in _ROUTING_STOPWORDS
            and word not in {
                "mad", "mcp", "please", "last", "first", "show", "give",
                "tell", "from", "into", "inside", "using", "portal",
            }
            and not word.isdigit()
        ]
        return " ".join(words[:8]) or text[:160]

    def _cache_portal_tool_items(
        self, server_id: str, payload: Any
    ) -> List[Dict[str, Any]]:
        cache = self._portal_tool_cache.setdefault(server_id, {})
        projected: List[Dict[str, Any]] = []
        changed = False
        for item in self._portal_payload_items(payload):
            service_id = re.sub(
                r"[^a-zA-Z0-9._-]+", "", str(item.get("serviceId") or "")
            )[:120]
            tool_name = re.sub(
                r"[^a-zA-Z0-9._-]+", "", str(item.get("toolName") or "")
            )[:200]
            if not service_id or not tool_name:
                continue
            row = {
                "service_id": service_id,
                "service_name": re.sub(
                    r"\s+", " ", str(item.get("serviceName") or "")
                ).strip()[:160],
                "tool_name": tool_name,
                "description": re.sub(
                    r"\s+", " ", str(item.get("description") or "")
                ).strip()[:800],
                "risk": re.sub(r"\s+", " ", str(item.get("risk") or "")).strip()[:40],
                "descriptor_hash": re.sub(
                    r"[^a-zA-Z0-9._-]+", "", str(item.get("descriptorHash") or "")
                )[:160],
            }
            key = f"{service_id}:{tool_name}"
            if cache.get(key) != row:
                cache[key] = row
                changed = True
            projected.append(row)
        if changed:
            self._generation += 1
        return projected

    @staticmethod
    def _portal_candidate_score(
        item: Dict[str, Any], latest_query: str, discovery_query: str
    ) -> int:
        if str(item.get("risk") or "").lower() != "read":
            return -10_000
        query_tokens = _routing_tokens(f"{latest_query} {discovery_query}")
        haystack = f"{item.get('tool_name') or ''} {item.get('description') or ''}"
        score = len(query_tokens & _routing_tokens(haystack)) * 10
        name = str(item.get("tool_name") or "").lower()
        latest = str(latest_query or "").lower()
        if discovery_query == "collection contents":
            score += 200 if re.search(r"(?:list|scroll|read|query|search)[._-]?points", name) else 0
            score -= 200 if "list-collections" in name else 0
        elif discovery_query == "payloads":
            score += 200 if "list-points" in name else 0
        elif discovery_query == "collections":
            score += 200 if "list-collections" in name else 0
        elif discovery_query == "read messages":
            score += 200 if re.search(r"(?:^|[._-])read[._-]?messages$", name) else 0
        if "private" in name and "private" not in latest:
            score -= 100
        return score

    async def _portal_reference(
        self,
        server_id: str,
        item: Dict[str, Any],
        trace_events: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        key = f"{item.get('service_id')}:{item.get('tool_name')}"
        cached = self._portal_reference_cache.setdefault(server_id, {}).get(key)
        if cached and (
            not item.get("descriptor_hash")
            or cached.get("descriptorHash") == item.get("descriptor_hash")
        ):
            return copy.deepcopy(cached)
        arguments = {
            "serviceId": item["service_id"],
            "toolName": item["tool_name"],
        }
        result = await self.call_tool(
            f"mcp__{server_id}__portal.get_tool_reference",
            arguments,
            timeout_seconds=20,
            max_output_bytes=1_000_000,
        )
        trace_events.append({
            "tool": f"mcp__{server_id}__portal.get_tool_reference",
            "arguments": arguments,
            "trace_id": self._portal_trace_id(result),
        })
        if result.get("exit_code") != 0:
            return None
        payload = result.get("structured_content")
        descriptor = None
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, dict) and isinstance(data.get("descriptor"), dict):
                descriptor = data["descriptor"]
            elif isinstance(payload.get("descriptor"), dict):
                descriptor = payload["descriptor"]
        if not isinstance(descriptor, dict) or not isinstance(descriptor.get("inputSchema"), dict):
            return None
        safe = copy.deepcopy(descriptor)
        self._portal_reference_cache.setdefault(server_id, {})[key] = safe
        return copy.deepcopy(safe)

    @staticmethod
    def _portal_named_channel(latest_query: str) -> str:
        match = re.search(r"#([a-zA-Z0-9_-]{1,100})", str(latest_query or ""))
        return match.group(1) if match else ""

    @staticmethod
    def _portal_find_named_id(payload: Any, target: str) -> str:
        queue: List[Any] = [payload]
        target_norm = re.sub(r"[^a-z0-9]+", "", target.lower())
        visited = 0
        fallback = ""
        while queue and visited < 1000:
            visited += 1
            value = queue.pop(0)
            if isinstance(value, str):
                stripped = value.strip()
                if stripped.startswith(("{", "[")):
                    try:
                        queue.append(json.loads(stripped))
                    except json.JSONDecodeError:
                        pass
                continue
            if isinstance(value, list):
                queue.extend(value[:200])
                continue
            if not isinstance(value, dict):
                continue
            raw_id = value.get("channel_id") or value.get("channelId") or value.get("id")
            candidate_id = str(raw_id or "").strip()
            if re.fullmatch(r"[0-9]{8,30}", candidate_id):
                fallback = fallback or candidate_id
                raw_name = (
                    value.get("channel_name") or value.get("channelName")
                    or value.get("displayName") or value.get("name") or ""
                )
                name_norm = re.sub(r"[^a-z0-9]+", "", str(raw_name).lower())
                if target_norm and target_norm in name_norm:
                    return candidate_id
            queue.extend(value.values())
        return fallback

    @staticmethod
    def _portal_result_item_count(payload: Any) -> Optional[int]:
        """Return the first bounded provider item count without retaining content."""
        queue: List[Any] = [payload]
        visited = 0
        while queue and visited < 1000:
            visited += 1
            value = queue.pop(0)
            if isinstance(value, str):
                stripped = value.strip()
                if stripped.startswith(("{", "[")) and len(stripped) <= 1_000_000:
                    try:
                        queue.append(json.loads(stripped))
                    except json.JSONDecodeError:
                        pass
                continue
            if isinstance(value, list):
                queue.extend(value[:200])
                continue
            if not isinstance(value, dict):
                continue
            for key in ("items", "messages", "points", "collections", "records", "results"):
                items = value.get(key)
                if isinstance(items, list):
                    return len(items)
            queue.extend(value.values())
        return None

    @staticmethod
    def _portal_result_context(payload: Any) -> Dict[str, List[str]]:
        """Extract bounded resource identifiers needed by referential follow-ups."""
        resource_fields = {
            "collections": ("collection_names", ("collection_name", "name", "id")),
            "channels": ("channel_ids", ("channel_id", "id", "name")),
        }
        context: Dict[str, List[str]] = {}
        queue: List[Any] = [payload]
        visited = 0
        while queue and visited < 1000:
            visited += 1
            value = queue.pop(0)
            if isinstance(value, str):
                stripped = value.strip()
                if stripped.startswith(("{", "[")) and len(stripped) <= 1_000_000:
                    try:
                        queue.append(json.loads(stripped))
                    except json.JSONDecodeError:
                        pass
                continue
            if isinstance(value, list):
                queue.extend(value[:200])
                continue
            if not isinstance(value, dict):
                continue
            for source_key, (context_key, item_keys) in resource_fields.items():
                items = value.get(source_key)
                if not isinstance(items, list):
                    continue
                identifiers: List[str] = []
                for item in items[:25]:
                    raw = item
                    if isinstance(item, dict):
                        raw = next(
                            (item.get(key) for key in item_keys if item.get(key) is not None),
                            "",
                        )
                    if not isinstance(raw, (str, int)):
                        continue
                    identifier = re.sub(r"[\x00-\x1f\x7f]+", " ", str(raw))
                    identifier = re.sub(r"\s+", " ", identifier).strip()[:160]
                    if identifier and identifier not in identifiers:
                        identifiers.append(identifier)
                if identifiers:
                    context[context_key] = identifiers
            queue.extend(value.values())
        return context

    async def _portal_resolve_channel(
        self,
        server_id: str,
        service_id: str,
        channel_name: str,
        trace_events: List[Dict[str, Any]],
    ) -> str:
        find_arguments = {
            "query": "find channel",
            "service": service_id,
            "risk": "read",
            "configuredOnly": True,
            "limit": 8,
        }
        found = await self.call_tool(
            f"mcp__{server_id}__portal.find_tools",
            find_arguments,
            timeout_seconds=20,
            max_output_bytes=1_000_000,
        )
        trace_events.append({
            "tool": f"mcp__{server_id}__portal.find_tools",
            "arguments": find_arguments,
            "trace_id": self._portal_trace_id(found),
        })
        items = self._cache_portal_tool_items(
            server_id, found.get("structured_content")
        )
        resolvers = [
            item for item in items
            if item.get("service_id") == service_id
            and item.get("risk") == "read"
            and re.search(r"(?:find|resolve|lookup)[._-]?channel", str(item.get("tool_name") or ""), re.I)
        ]
        if not resolvers:
            return ""
        resolver = resolvers[0]
        descriptor = await self._portal_reference(
            server_id, resolver, trace_events
        )
        if not descriptor:
            return ""
        provider_args = {"channel_name": channel_name}
        call_arguments = {
            "serviceId": service_id,
            "toolName": resolver["tool_name"],
            "arguments": provider_args,
        }
        result = await self.call_tool(
            f"mcp__{server_id}__portal.call_read_tool",
            call_arguments,
            timeout_seconds=30,
            max_output_bytes=1_000_000,
        )
        trace_events.append({
            "tool": f"mcp__{server_id}__portal.call_read_tool",
            "arguments": call_arguments,
            "trace_id": self._portal_trace_id(result),
        })
        if result.get("exit_code") != 0:
            return ""
        return self._portal_find_named_id(
            result.get("structured_content"), channel_name
        )

    @staticmethod
    def _portal_request_schema(
        input_schema: Dict[str, Any], latest_query: str
    ) -> Dict[str, Any]:
        """Project exact descriptor fields needed by this one request."""
        schema = copy.deepcopy(input_schema)
        properties = schema.get("properties")
        if not isinstance(properties, dict) or not properties:
            return schema
        required = {
            str(name) for name in schema.get("required") or [] if isinstance(name, str)
        }
        query_words = {
            word.removesuffix("s")
            for word in re.findall(r"[a-z0-9]+", str(latest_query or "").lower())
        }
        bounded_count = bool(
            re.search(r"\b(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|"
                      r"last|first|sample|examples?)\b", str(latest_query or ""), re.I)
        )
        selected: Dict[str, Any] = {}
        for name, definition in properties.items():
            field_words = {
                word.removesuffix("s")
                for word in re.findall(r"[a-z0-9]+", str(name).lower())
                if word not in {"id", "name", "include"}
            }
            keep = name in required or bool(field_words & query_words)
            if name.lower() in {"limit", "count", "max_results", "page_size"}:
                keep = keep or bounded_count
            if keep:
                selected[name] = copy.deepcopy(definition)
        if not selected:
            selected = {
                name: copy.deepcopy(definition)
                for name, definition in properties.items()
                if name in required
            }
        projected = {
            key: copy.deepcopy(value)
            for key, value in schema.items()
            if key not in {"properties", "$defs", "definitions", "required"}
        }
        projected["properties"] = selected
        if required:
            projected["required"] = [
                name for name in schema.get("required") or [] if name in selected
            ]

        # Retain only definitions actually referenced by a selected field.
        serialized = json.dumps(selected, sort_keys=True, default=str)
        for defs_key in ("$defs", "definitions"):
            defs = schema.get(defs_key)
            if not isinstance(defs, dict):
                continue
            used = {
                name: copy.deepcopy(value)
                for name, value in defs.items()
                if f"#/{defs_key}/{name}" in serialized
            }
            if used:
                projected[defs_key] = used
        return projected

    async def prepare_portal_read(
        self,
        latest_query: str,
        routing_query: str,
        *,
        context_arguments: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Mount one exact downstream schema backed by Portal call_read_tool."""
        server_id = self._portal_connection_for_request(routing_query)
        if not server_id:
            return None
        service = self._portal_service_for_request(server_id, routing_query)
        service_id = str(service.get("id") or "")
        if not service_id:
            return None
        discovery_query = self._portal_discovery_query(latest_query)
        find_arguments = {
            "query": discovery_query,
            "service": service_id,
            "risk": "read",
            "configuredOnly": True,
            "limit": 10,
        }
        trace_events: List[Dict[str, Any]] = []
        result = await self.call_tool(
            f"mcp__{server_id}__portal.find_tools",
            find_arguments,
            timeout_seconds=20,
            max_output_bytes=1_000_000,
        )
        trace_events.append({
            "tool": f"mcp__{server_id}__portal.find_tools",
            "arguments": find_arguments,
            "trace_id": self._portal_trace_id(result),
        })
        if result.get("exit_code") != 0:
            return None
        candidates = [
            item for item in self._cache_portal_tool_items(
                server_id, result.get("structured_content")
            )
            if item.get("service_id") == service_id and item.get("risk") == "read"
        ]
        if not candidates:
            return None
        candidates.sort(
            key=lambda item: (
                -self._portal_candidate_score(item, latest_query, discovery_query),
                str(item.get("tool_name") or ""),
            )
        )
        selected = candidates[0]
        descriptor = await self._portal_reference(server_id, selected, trace_events)
        if not descriptor:
            return None
        input_schema = self._portal_request_schema(
            descriptor.get("inputSchema") or {}, latest_query
        )
        fixed_arguments: Dict[str, Any] = {}
        properties = input_schema.get("properties") or {}
        for name, value in (context_arguments or {}).items():
            if name not in properties or not isinstance(value, (str, int, float, bool)):
                continue
            fixed_arguments[name] = value
            properties.pop(name, None)
            if isinstance(input_schema.get("required"), list):
                input_schema["required"] = [
                    item for item in input_schema["required"] if item != name
                ]
        channel_name = self._portal_named_channel(latest_query)
        if channel_name and "channel_id" in (input_schema.get("properties") or {}):
            channel_id = await self._portal_resolve_channel(
                server_id, service_id, channel_name, trace_events
            )
            if channel_id:
                fixed_arguments["channel_id"] = channel_id
                input_schema.get("properties", {}).pop("channel_id", None)
                if isinstance(input_schema.get("required"), list):
                    input_schema["required"] = [
                        name for name in input_schema["required"] if name != "channel_id"
                    ]
        route_suffix = ""
        if fixed_arguments:
            route_suffix = "." + hashlib.sha256(
                json.dumps(fixed_arguments, sort_keys=True).encode("utf-8")
            ).hexdigest()[:12]
        prefix = f"mcp__{server_id}__"
        raw_base = f"portal.read.{service_id}.{selected['tool_name']}"
        max_raw_length = max(20, 64 - len(prefix))
        if len(raw_base) + len(route_suffix) > max_raw_length:
            raw_base = raw_base[:max_raw_length - len(route_suffix)].rstrip("._-")
        raw_name = f"{raw_base}{route_suffix}"
        qualified_name = f"{prefix}{raw_name}"
        schema = {
            "type": "function",
            "function": {
                "name": qualified_name,
                "description": (
                    f"[MCP:{self._connections[server_id].get('name') or server_id}] "
                    f"Portal-relayed {service_id} read: {descriptor.get('description') or selected.get('description') or selected['tool_name']}"
                )[:1600],
                "parameters": input_schema,
            },
        }
        self._portal_proxy_tools[qualified_name] = {
            "server_id": server_id,
            "service_id": service_id,
            "tool_name": selected["tool_name"],
            "descriptor_hash": str(descriptor.get("descriptorHash") or selected.get("descriptor_hash") or ""),
            "catalog_version": str(descriptor.get("catalogVersion") or service.get("catalog_version") or ""),
            "fixed_arguments": fixed_arguments,
        }
        return {
            "qualified_name": qualified_name,
            "schema": schema,
            "service_id": service_id,
            "tool_name": selected["tool_name"],
            "descriptor_hash": self._portal_proxy_tools[qualified_name]["descriptor_hash"],
            "catalog_version": self._portal_proxy_tools[qualified_name]["catalog_version"],
            "trace_events": trace_events,
        }

    def native_tool_names_for_request(self, query: str, limit: int = 8) -> Set[str]:
        """Select a named connection's own typed discovery/read tools.

        Server identities, catalog labels, tool names, and ordered entrypoint
        references all come from the live MCP handshake/catalog.  Nothing here
        assumes a provider, channel, profile, URL, or broker tool identity.
        """
        query_tokens = _routing_tokens(query)
        if not query_tokens:
            return set()
        normalized_query = " ".join(
            re.findall(r"[a-z0-9][a-z0-9_-]*", str(query or "").lower())
        )
        query_name_tokens = {
            token for token in re.findall(r"[a-z0-9]+", str(query or "").lower())
            if token not in _ROUTING_STOPWORDS
        }
        candidates = []
        for connection_index, (server_id, tools) in enumerate(self._tools.items()):
            if self.is_extension_server(server_id) or not tools:
                continue
            conn = self._connections.get(server_id, {})
            if conn.get("status") != "connected":
                continue
            identity_values = [
                conn.get("name"),
                (conn.get("server_info") or {}).get("name"),
            ]
            identity_parts = [
                *identity_values,
                *(conn.get("catalog_terms") or []),
            ]
            identity_tokens = _routing_tokens(" ".join(str(item or "") for item in identity_parts))
            if not (query_tokens & identity_tokens):
                continue
            explicit_identity_score = 0
            for value in identity_values:
                phrase = " ".join(
                    re.findall(r"[a-z0-9][a-z0-9_-]*", str(value or "").lower())
                )
                if phrase and re.search(rf"(?:^| )({re.escape(phrase)})(?: |$)", normalized_query):
                    explicit_identity_score = max(
                        explicit_identity_score,
                        len(phrase.split()) * 100 + len(phrase),
                    )
            candidates.append((
                explicit_identity_score,
                len(query_tokens & identity_tokens),
                connection_index,
                server_id,
                tools,
                conn,
            ))

        candidates.sort(key=lambda item: (-item[0], -item[1], item[2]))
        if candidates and candidates[0][0] > 0:
            # An explicitly named connection is an operator-selected boundary;
            # do not fill the global limit from a provider that matched only a
            # shared catalog term (for example, a direct provider beside its
            # named broker).
            candidates = candidates[:1]

        selected: List[str] = []
        max_selected = max(1, min(int(limit), 20))
        for _explicit, _overlap, _index, server_id, tools, conn in candidates:
            by_name = {str(tool.get("name") or ""): tool for tool in tools}
            if self._is_portal_connection(tools):
                # Connection orientation and the service inventory are cached
                # at connect time.  Normal reads never remount welcome,
                # list_services, preview/write entrypoints, or arbitrary
                # catalog artifacts into every model turn.
                for name in (
                    "portal.find_tools",
                    "portal.get_tool_reference",
                    "portal.call_read_tool",
                ):
                    if name in by_name and mcp_tool_is_readonly(by_name[name]):
                        selected.append(f"mcp__{server_id}__{name}")
                return set(selected[:max_selected])
            referenced: List[str] = []
            # Only initialize instructions define the server-wide workflow.
            # Per-tool descriptions may cross-reference unrelated local tools.
            guidance = str(conn.get("instructions") or "")
            for name in re.findall(r"\b[a-zA-Z][\w-]*(?:\.[\w-]+)+\b", guidance):
                if name in by_name and name not in referenced:
                    referenced.append(name)

            for name in referenced:
                if not mcp_tool_is_readonly(by_name[name]):
                    continue
                selected.append(f"mcp__{server_id}__{name}")
                if len(selected) >= max_selected:
                    return set(selected)

            scored = []
            for index, tool in enumerate(tools):
                name = str(tool.get("name") or "")
                if not name or (name in referenced and mcp_tool_is_readonly(tool)):
                    continue
                haystack = f"{name} {tool.get('description') or ''}"
                overlap = len(query_tokens & _routing_tokens(haystack))
                normalized_name = " ".join(
                    re.findall(r"[a-z0-9][a-z0-9_-]*", name.lower())
                )
                directly_named = bool(normalized_name and re.search(
                    rf"(?:^| ){re.escape(normalized_name)}(?: |$)",
                    normalized_query,
                ))
                action_name = name.split(".", 1)[-1]
                action_words = re.findall(r"[a-z0-9]+", action_name.lower())
                name_tokens = {
                    token for token in action_words
                    if token not in _ROUTING_STOPWORDS
                }
                fully_name_matched = (
                    len(name_tokens) >= 2 and name_tokens <= query_name_tokens
                )
                if not mcp_tool_is_readonly(tool) and not (
                    directly_named or fully_name_matched
                ):
                    continue
                if referenced and not directly_named and not fully_name_matched:
                    continue
                negation_name = name if directly_named else " ".join(action_words)
                if (directly_named or fully_name_matched) and _tool_name_is_negated(
                    query, negation_name, allow_intervening=not directly_named
                ):
                    continue
                scored.append((not directly_named, -overlap, index, name))
            for _implicit, _overlap, _index, name in sorted(scored):
                qualified = f"mcp__{server_id}__{name}"
                if qualified not in selected:
                    selected.append(qualified)
                if len(selected) >= max_selected:
                    return set(selected)
        return set(selected)

    async def _reconnect_builtin(self, server_id: str) -> bool:
        """Tear down and reconnect a crashed builtin MCP server."""
        import sys
        from src.builtin_mcp import _BUILTIN_SERVERS, builtin_python_env

        if server_id not in _BUILTIN_SERVERS:
            return False

        script_rel, name = _BUILTIN_SERVERS[server_id]
        base_dir = get_app_root()
        script_path = os.path.join(base_dir, script_rel)

        # Clean up old connection
        await self.disconnect_server(server_id)

        try:
            ok = await self.connect_server(
                server_id=server_id,
                name=name,
                transport="stdio",
                command=sys.executable,
                args=[script_path],
                env=builtin_python_env(base_dir),
            )
            if ok:
                logger.info(f"Reconnected builtin MCP server: {name}")
            return ok
        except Exception as e:
            logger.error(f"Failed to reconnect builtin MCP server {name}: {e}")
            return False

    def get_all_openai_schemas(self, disabled_map: Optional[Dict[str, set]] = None) -> List[Dict]:
        """Return all MCP tools in OpenAI function-calling format.

        Tool names are namespaced as mcp__{server_id}__{tool_name}.
        disabled_map: optional {server_id: set_of_disabled_tool_names} to filter out.
        """
        schemas = []
        for server_id, tools in self._tools.items():
            if self.is_extension_server(server_id):
                continue
            # Skip builtin Python servers — they use the code-block tool format
            # But include NPX-based builtins (like browser) which need function calling
            if self.is_builtin(server_id) and server_id != "builtin_browser":
                continue
            conn = self._connections.get(server_id, {})
            server_name = conn.get("name", server_id)
            disabled = (disabled_map or {}).get(server_id, set())

            identity = conn.get("identity", "")
            label = f"{server_name} ({identity})" if identity else server_name

            for tool in tools:
                if tool["name"] in disabled:
                    continue
                qualified = f"mcp__{server_id}__{tool['name']}"
                schema = {
                    "type": "function",
                    "function": {
                        "name": qualified,
                        "description": f"[MCP:{label}] {tool['description']}",
                        "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
                    },
                }
                schemas.append(schema)

        return schemas

    def get_action_policies(self) -> Dict[str, Dict[str, str]]:
        """Return authority metadata only for MCP calls with a proven effect.

        Unknown MCP tools deliberately receive no declaration and continue to
        fail closed at the authority gate. Effectful tools remain approval-
        gated by the shared authority protocol at execution time.
        """
        policies: Dict[str, Dict[str, str]] = {}
        for server_id, tools in self._tools.items():
            if self.is_extension_server(server_id):
                continue
            if self.is_builtin(server_id) and server_id != "builtin_browser":
                continue
            for tool in tools:
                effect = mcp_tool_action_effect(tool)
                if effect:
                    policies[f"mcp__{server_id}__{tool['name']}"] = {
                        "action_effect": effect
                    }
        for qualified_name in self._portal_proxy_tools:
            policies[qualified_name] = {"action_effect": "read"}
        return policies

    def get_all_tools(self, disabled_map: Optional[Dict[str, set]] = None) -> List[Dict]:
        """Return a flat list of all discovered tools with server info."""
        result = []
        for server_id, tools in self._tools.items():
            if self.is_extension_server(server_id):
                continue
            conn = self._connections.get(server_id, {})
            disabled = (disabled_map or {}).get(server_id, set())
            for tool in tools:
                result.append({
                    "server_id": server_id,
                    "server_name": conn.get("name", server_id),
                    "name": tool["name"],
                    "qualified_name": f"mcp__{server_id}__{tool['name']}",
                    "description": tool.get("description", ""),
                    "input_schema": tool.get("input_schema") or {},
                    "is_disabled": tool["name"] in disabled,
                })
        return result

    def plan_mode_blocked_mcp(self) -> Tuple[Dict[str, Set[str]], Set[str]]:
        """Plan mode: block every MCP tool that isn't clearly read-only.

        Returns (disabled_map, qualified_names):
          - disabled_map: {server_id: {tool_name, ...}} to hide write tools from
            the prompt/schemas (merged into the existing mcp_disabled_map).
          - qualified_names: {"mcp__<server>__<tool>", ...} for runtime rejection
            in execute_tool_block (which matches the qualified name).
        """
        disabled_map: Dict[str, Set[str]] = {}
        qualified: Set[str] = set()
        for server_id, tools in self._tools.items():
            if self.is_extension_server(server_id):
                continue
            for tool in tools:
                if not mcp_tool_is_readonly(tool):
                    disabled_map.setdefault(server_id, set()).add(tool["name"])
                    qualified.add(f"mcp__{server_id}__{tool['name']}")
        return disabled_map, qualified

    def is_builtin(self, server_id: str) -> bool:
        """Check if a server is a built-in (auto-registered) server."""
        return server_id.startswith("builtin_") or server_id in {
            "image_gen",
            "memory",
            "rag",
            "email",
        }

    def get_server_status(self, server_id: str) -> Dict:
        """Get connection status for a server."""
        return self._connections.get(server_id, {"status": "disconnected"})

    def get_all_statuses(self) -> Dict[str, Dict]:
        """Get connection statuses for all servers."""
        return dict(self._connections)

    _cached_prompt_desc = None
    _cached_prompt_desc_key = None

    def get_tool_descriptions_for_prompt(
        self,
        disabled_map: Optional[Dict[str, set]] = None,
        allowed_names: Optional[Set[str]] = None,
    ) -> str:
        """Generate text describing the MCP tools mounted for this request.

        ``allowed_names`` contains qualified function names selected by the
        agent router.  Keeping the prose catalog aligned with that set prevents
        server-wide guidance from advertising tools that are not executable in
        the current model payload.  Callers that omit it (notably the tool
        indexer) still receive the complete connected catalog.
        """
        allowed = frozenset(allowed_names) if allowed_names is not None else None
        cache_key = (
            frozenset((k, frozenset(v)) for k, v in (disabled_map or {}).items()),
            allowed,
            len(self._tools),
            self._generation,
        )
        if self._cached_prompt_desc is not None and self._cached_prompt_desc_key == cache_key:
            return self._cached_prompt_desc
        tools = self.get_all_tools(disabled_map)
        if not tools:
            return ""

        lines = ["\n\nYou also have access to external MCP tool servers. These tools are called via native function calling:"]
        by_server = {}
        for t in tools:
            # Skip builtin Python servers — they're already in the agent prompt
            # But include NPX-based builtins (like browser) which aren't hardcoded
            if self.is_builtin(t["server_id"]) and t["server_id"] != "builtin_browser":
                continue
            if t.get("is_disabled"):
                continue
            if allowed is not None and t["qualified_name"] not in allowed:
                continue
            sn = t["server_name"]
            if sn not in by_server:
                by_server[sn] = []
            by_server[sn].append(t)

        if not by_server:
            return ""

        for server_name, server_tools in by_server.items():
            # Include identity (e.g. email address) if available
            sid = server_tools[0]["server_id"] if server_tools else ""
            identity = self._connections.get(sid, {}).get("identity", "")
            label = f"{server_name} ({identity})" if identity else server_name
            lines.append(f"\n**{label}:**")
            for t in server_tools:
                # Truncate long descriptions
                desc = t['description'][:120] + '...' if len(t['description']) > 120 else t['description']
                # Include the tool's declared inputs so the model calls it with
                # real argument names instead of guessing from the description
                # alone (issue #2509).
                args_hint = _format_mcp_params(t.get("input_schema"))
                lines.append(f"  - {t['qualified_name']}: {desc}{args_hint}")

        result = "\n".join(lines)
        self._cached_prompt_desc = result
        self._cached_prompt_desc_key = cache_key
        return result
