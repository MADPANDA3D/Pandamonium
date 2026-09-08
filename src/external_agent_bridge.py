from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import re
import socket
import stat
import threading
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpcore
import httpx

from src.webhook_manager import _HTTPCORE_TO_HTTPX_EXC, _PinnedAsyncBackend


EXTERNAL_AGENT_PROTOCOL = "pandamonium.external-agent-sidecar.v1"
EXTERNAL_DISCOVERY_SCHEMA = "pandamonium.discovery.v1"
EXTERNAL_READ_CAPABILITIES = (
    "agent.catalog",
    "task.catalog",
    "task.events",
    "task.transcript",
)
EXTERNAL_ACTION_CAPABILITIES = (
    "task.start",
    "task.steer",
    "task.reply",
    "task.cancel",
    "task.status.read",
)
EXTERNAL_AGENT_CAPABILITIES = EXTERNAL_READ_CAPABILITIES + EXTERNAL_ACTION_CAPABILITIES

_ACTION_CAPABILITY_BY_NAME = {
    "start": "task.start",
    "steer": "task.steer",
    "reply": "task.reply",
    "cancel": "task.cancel",
}
_ACTION_EFFECTS = (
    "read",
    "reversible_write",
    "destructive_or_difficult_to_recover",
    "external_publication_or_communication",
    "purchase",
    "credential_or_auth_change",
    "privilege_expansion",
    "outside_workspace_boundary",
)
_EFFECT_RANK = {effect: rank for rank, effect in enumerate(_ACTION_EFFECTS)}
_SEPARATE_GATE_EFFECTS = set(_ACTION_EFFECTS[2:])

_CONFIG_ENV_NAMES = (
    "PANDAMONIUM_EXTERNAL_AGENT_CONNECTIONS_JSON",
    "ODYSSEUS_EXTERNAL_AGENT_CONNECTIONS_JSON",
)
_MAX_CONNECTIONS = 8
_MAX_WIRE_BYTES = 65_536
_MAX_DEPTH = 16
_MAX_STRING = 12_000
_MAX_COLLECTION = 64
_MAX_CREDENTIAL_BYTES = 4_096
_MAX_CLOCK_SKEW = timedelta(minutes=5)
_CONNECTION_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_RESERVED_WORKERS = {"pc-codex", "hermes", "vps-codex"}
_WORKSPACE_ALIAS = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_REFERENCE = re.compile(r"^[a-z][a-z0-9._:-]{2,127}$")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
_CURSOR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~:-]{0,1999}$")
_SECRET_KEY = re.compile(
    r"(?:token|secret|password|credential|authorization|auth_ref|api[_-]?key|private[_-]?key)",
    re.IGNORECASE,
)
_SECRET_VALUE = re.compile(
    r"(?i)\b(?:token|secret|password|authorization|api[_-]?key)\s*[:=]\s*[^\s,;]+"
)
_RAW_SECRET_VALUE = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:Bearer\s+[A-Za-z0-9._~+/=-]{8,}|"
    r"sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9_]{8,}|"
    r"xox[baprs]-[A-Za-z0-9-]{8,})(?![A-Za-z0-9])"
)
_ABSOLUTE_PATH = re.compile(r"(?<![\w])(?:/[A-Za-z0-9._~@+-]+){2,}")
_WINDOWS_PATH = re.compile(r"(?i)\b[A-Z]:\\(?:[^\s\\]+\\)*[^\s\\]+")
_FORBIDDEN_ACTION_KEY = re.compile(
    r"(?:^|_)(?:endpoint|url|host|port|path|paths|cwd|directory|root|command|shell|"
    r"auth|auth_ref|token|secret|password|credential|api_key|private_key)(?:$|_)",
    re.IGNORECASE,
)
_STABLE_ERRORS = {
    "malformed_envelope",
    "incompatible_protocol",
    "oversized_payload",
    "stale_request",
    "replay_detected",
    "unauthorized",
    "wrong_owner",
    "wrong_workspace",
    "capability_disabled",
    "path_escape",
    "symlink_escape",
    "rate_limited",
    "timeout",
    "sidecar_unavailable",
    "internal_error",
}

Requester = Callable[
    [str, str, dict[str, str], dict[str, Any] | None, float, ipaddress._BaseAddress, int],
    Awaitable[tuple[int, dict[str, str], bytes]],
]


class ExternalAgentBridgeError(RuntimeError):
    """Stable, non-sensitive failure at the external-agent trust boundary."""

    def __init__(self, code: str):
        self.code = code if code in _STABLE_ERRORS or code in {
            "connection_configuration_invalid",
            "credential_unavailable",
            "endpoint_invalid",
            "endpoint_policy_rejected",
        } else "internal_error"
        super().__init__(self.code)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or len(value) > 40:
        raise ExternalAgentBridgeError("malformed_envelope")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExternalAgentBridgeError("malformed_envelope") from exc
    if parsed.tzinfo is None:
        raise ExternalAgentBridgeError("malformed_envelope")
    return parsed.astimezone(timezone.utc)


def _owner_ref(owner: str) -> str:
    digest = hashlib.sha256(owner.strip().encode("utf-8")).hexdigest()[:32]
    return f"owner:o{digest}"


def _scoped_ref(prefix: str, value: str) -> str:
    normalized = value.lower().replace("_", "-")
    return f"{prefix}:{normalized}"


def _stable_wire_id(prefix: str, value: object) -> str:
    digest = hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()
    return f"{prefix}:{digest}"


def _strictest_effect(*effects: str) -> str:
    if any(effect not in _EFFECT_RANK for effect in effects):
        raise ExternalAgentBridgeError("capability_disabled")
    return max(effects, key=_EFFECT_RANK.__getitem__)


def _validate_action_arguments(capability: str, arguments: dict[str, Any]) -> None:
    allowed = {
        "task.start": {"prompt", "permission_mode"},
        "task.steer": {"prompt"},
        "task.reply": {"answers"},
        "task.cancel": {"reason"},
        "task.status.read": set(),
    }
    if capability not in allowed or set(arguments) != allowed[capability]:
        raise ExternalAgentBridgeError("malformed_envelope")
    _validate_bounded_value(arguments)

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if _FORBIDDEN_ACTION_KEY.search(str(key)) or _SECRET_KEY.search(str(key)):
                    raise ExternalAgentBridgeError("malformed_envelope")
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, str):
            if "\x00" in value or _ABSOLUTE_PATH.search(value) or _WINDOWS_PATH.search(value):
                raise ExternalAgentBridgeError("path_escape")
            if _SECRET_VALUE.search(value) or _RAW_SECRET_VALUE.search(value):
                raise ExternalAgentBridgeError("unauthorized")

    visit(arguments)
    if capability in {"task.start", "task.steer"}:
        prompt = arguments.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ExternalAgentBridgeError("malformed_envelope")
        if capability == "task.start" and arguments.get("permission_mode") not in {
            "read_only", "workspace_write",
        }:
            raise ExternalAgentBridgeError("malformed_envelope")
    elif capability == "task.reply":
        answers = arguments.get("answers")
        if not isinstance(answers, dict) or not answers:
            raise ExternalAgentBridgeError("malformed_envelope")
        if any(
            not isinstance(key, str)
            or not key
            or not (
                isinstance(value, str)
                or isinstance(value, list) and value and all(isinstance(item, str) for item in value)
            )
            for key, value in answers.items()
        ):
            raise ExternalAgentBridgeError("malformed_envelope")


def _safe_text(value: object, *, maximum: int = _MAX_STRING) -> str:
    text = " ".join(str(value or "").split())[:maximum]
    text = _SECRET_VALUE.sub("[redacted]", text)
    text = _RAW_SECRET_VALUE.sub("[redacted]", text)
    text = _ABSOLUTE_PATH.sub("[redacted]", text)
    return _WINDOWS_PATH.sub("[redacted]", text)


def _safe_value(value: Any, *, depth: int = 0) -> Any:
    if depth > _MAX_DEPTH:
        raise ExternalAgentBridgeError("malformed_envelope")
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _safe_text(value)
    if isinstance(value, list):
        if len(value) > _MAX_COLLECTION:
            raise ExternalAgentBridgeError("malformed_envelope")
        return [_safe_value(item, depth=depth + 1) for item in value]
    if isinstance(value, dict):
        if len(value) > _MAX_COLLECTION:
            raise ExternalAgentBridgeError("malformed_envelope")
        result = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 100 or _SECRET_KEY.search(key):
                continue
            result[key] = _safe_value(item, depth=depth + 1)
        return result
    raise ExternalAgentBridgeError("malformed_envelope")


def _json_depth(raw: bytes) -> int:
    depth = maximum = 0
    quoted = escaped = False
    for byte in raw:
        char = chr(byte)
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            maximum = max(maximum, depth)
        elif char in "]}":
            depth -= 1
            if depth < 0:
                raise ExternalAgentBridgeError("malformed_envelope")
    if quoted or depth != 0:
        raise ExternalAgentBridgeError("malformed_envelope")
    return maximum


def _bounded_json(raw: bytes) -> dict[str, Any]:
    if len(raw) > _MAX_WIRE_BYTES:
        raise ExternalAgentBridgeError("oversized_payload")
    if _json_depth(raw) > _MAX_DEPTH:
        raise ExternalAgentBridgeError("malformed_envelope")
    try:
        payload = _strict_json_loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ExternalAgentBridgeError("malformed_envelope") from exc
    if not isinstance(payload, dict):
        raise ExternalAgentBridgeError("malformed_envelope")
    _validate_bounded_value(payload)
    return payload


def _validate_bounded_value(value: Any, *, depth: int = 0) -> None:
    if depth > _MAX_DEPTH:
        raise ExternalAgentBridgeError("malformed_envelope")
    if value is None or isinstance(value, (bool, int, float)):
        return
    if isinstance(value, str):
        if len(value) > _MAX_STRING:
            raise ExternalAgentBridgeError("malformed_envelope")
        return
    if isinstance(value, list):
        if len(value) > _MAX_COLLECTION:
            raise ExternalAgentBridgeError("malformed_envelope")
        for item in value:
            _validate_bounded_value(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > _MAX_COLLECTION:
            raise ExternalAgentBridgeError("malformed_envelope")
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 100:
                raise ExternalAgentBridgeError("malformed_envelope")
            _validate_bounded_value(item, depth=depth + 1)
        return
    raise ExternalAgentBridgeError("malformed_envelope")


def _strict_json_loads(raw: str | bytes) -> Any:
    def object_pairs(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate JSON property")
            value[key] = item
        return value

    def invalid_constant(_value):
        raise ValueError("invalid JSON number")

    return json.loads(
        raw,
        object_pairs_hook=object_pairs,
        parse_constant=invalid_constant,
    )


def _default_resolver(host: str) -> list[str]:
    try:
        return list({row[4][0] for row in socket.getaddrinfo(host, None)})
    except OSError:
        return []


def _address_class(address: ipaddress._BaseAddress) -> str:
    if address.is_loopback:
        return "loopback"
    if address.is_link_local or address.is_multicast or address.is_unspecified or address.is_reserved:
        return "rejected"
    if address.is_private:
        return "private"
    if address.is_global:
        return "public"
    return "rejected"


def _validate_endpoint(endpoint: object, policy: object) -> str:
    if (
        not isinstance(endpoint, str)
        or not 1 <= len(endpoint) <= 2_048
        or endpoint.strip() != endpoint
        or any(ord(character) < 0x21 or ord(character) > 0x7E for character in endpoint)
    ):
        raise ExternalAgentBridgeError("endpoint_invalid")
    if policy not in {"public", "private", "loopback"}:
        raise ExternalAgentBridgeError("connection_configuration_invalid")
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError as exc:
        raise ExternalAgentBridgeError("endpoint_invalid") from exc
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.scheme not in {"http", "https"}
        or parsed.path.rstrip("/") == ""
        or not parsed.path.rstrip("/").endswith("/v1")
        or (parsed.scheme == "http" and policy != "loopback")
        or (port is not None and not 1 <= port <= 65_535)
    ):
        raise ExternalAgentBridgeError("endpoint_invalid")
    return endpoint.rstrip("/")


def _validated_external_agent_ips(
    endpoint: str,
    policy: str,
    *,
    resolver: Callable[[str], Iterable[str | ipaddress._BaseAddress]] = _default_resolver,
) -> list[ipaddress._BaseAddress]:
    parsed = urlsplit(endpoint)
    host = parsed.hostname or ""
    try:
        literal = ipaddress.ip_address(host)
        values: Iterable[str | ipaddress._BaseAddress] = [literal]
    except ValueError:
        try:
            values = resolver(host)
        except Exception as exc:
            raise ExternalAgentBridgeError("endpoint_policy_rejected") from exc
    addresses = []
    try:
        for value in values:
            address = value if isinstance(value, ipaddress._BaseAddress) else ipaddress.ip_address(value)
            addresses.append(address)
    except ValueError as exc:
        raise ExternalAgentBridgeError("endpoint_policy_rejected") from exc
    if not addresses or any(_address_class(address) != policy for address in addresses):
        raise ExternalAgentBridgeError("endpoint_policy_rejected")
    return list(dict.fromkeys(addresses))


def configured_external_agent_connections(raw: str | None = None) -> list[dict[str, Any]]:
    if raw is None:
        raw = next((os.getenv(name, "").strip() for name in _CONFIG_ENV_NAMES if os.getenv(name, "").strip()), "")
    if not raw:
        return []
    try:
        values = _strict_json_loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ExternalAgentBridgeError("connection_configuration_invalid") from exc
    if not isinstance(values, list) or len(values) > _MAX_CONNECTIONS:
        raise ExternalAgentBridgeError("connection_configuration_invalid")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    allowed = {
        "enabled", "protocol_version", "id", "label", "endpoint", "auth_ref",
        "network_policy", "workspaces", "capabilities", "timeout_seconds",
    }
    for value in values:
        if not isinstance(value, dict) or set(value) - allowed or not isinstance(value.get("enabled"), bool):
            raise ExternalAgentBridgeError("connection_configuration_invalid")
        if value["enabled"] is False:
            continue
        connection_id = value.get("id")
        workspaces = value.get("workspaces")
        capabilities = value.get("capabilities")
        timeout = value.get("timeout_seconds", 10)
        if (
            value.get("protocol_version") != EXTERNAL_AGENT_PROTOCOL
            or not isinstance(connection_id, str)
            or not _CONNECTION_ID.fullmatch(connection_id)
            or connection_id in _RESERVED_WORKERS
            or connection_id in seen
            or not isinstance(value.get("label"), str)
            or not 1 <= len(" ".join(value["label"].split())) <= 80
            or not isinstance(value.get("auth_ref"), str)
            or not value["auth_ref"].startswith("file:/")
            or value["auth_ref"].startswith("file://")
            or len(value["auth_ref"]) > 2_048
            or any(character in value["auth_ref"] for character in "\x00\r\n")
            or not isinstance(workspaces, list)
            or not 1 <= len(workspaces) <= 32
            or any(not isinstance(item, str) or not _WORKSPACE_ALIAS.fullmatch(item) for item in workspaces)
            or len(set(workspaces)) != len(workspaces)
            or not isinstance(capabilities, list)
            or not capabilities
            or len(capabilities) > len(EXTERNAL_AGENT_CAPABILITIES)
            or isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not 0.25 <= float(timeout) <= 30
        ):
            raise ExternalAgentBridgeError("connection_configuration_invalid")
        if not isinstance(value.get("endpoint"), str) or not isinstance(value.get("network_policy"), str):
            raise ExternalAgentBridgeError("connection_configuration_invalid")
        normalized = dict(value)
        normalized_capabilities: dict[str, str] = {}
        for item in capabilities:
            if isinstance(item, str):
                if item not in EXTERNAL_READ_CAPABILITIES:
                    raise ExternalAgentBridgeError("connection_configuration_invalid")
                name, effect = item, "read"
            elif isinstance(item, dict) and set(item) == {"name", "effect"}:
                name, effect = item.get("name"), item.get("effect")
                if (
                    name not in EXTERNAL_ACTION_CAPABILITIES
                    or effect not in _ACTION_EFFECTS
                    or (name == "task.status.read" and effect != "read")
                    or (name != "task.status.read" and _EFFECT_RANK[effect] < _EFFECT_RANK["reversible_write"])
                ):
                    raise ExternalAgentBridgeError("connection_configuration_invalid")
            else:
                raise ExternalAgentBridgeError("connection_configuration_invalid")
            if name in normalized_capabilities:
                raise ExternalAgentBridgeError("connection_configuration_invalid")
            normalized_capabilities[name] = effect
        if set(normalized_capabilities) & set(EXTERNAL_ACTION_CAPABILITIES) and not {
            "task.start", "task.status.read", "task.events",
        }.issubset(normalized_capabilities):
            raise ExternalAgentBridgeError("connection_configuration_invalid")
        normalized["label"] = " ".join(value["label"].split())
        normalized["endpoint"] = _validate_endpoint(value.get("endpoint"), value.get("network_policy"))
        normalized["capabilities"] = normalized_capabilities
        normalized["timeout_seconds"] = float(timeout)
        result.append(normalized)
        seen.add(connection_id)
    return result


def _read_credential(reference: str) -> str:
    path = Path(reference.removeprefix("file:"))
    if not path.is_absolute():
        raise ExternalAgentBridgeError("credential_unavailable")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_mode & 0o077
                or not 0 < metadata.st_size <= _MAX_CREDENTIAL_BYTES
            ):
                raise ExternalAgentBridgeError("credential_unavailable")
            token = os.read(descriptor, _MAX_CREDENTIAL_BYTES + 1).decode("utf-8").strip()
        finally:
            os.close(descriptor)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ExternalAgentBridgeError("credential_unavailable") from exc
    if (
        not token
        or len(token.encode("utf-8")) > _MAX_CREDENTIAL_BYTES
        or any(ord(character) < 0x21 or ord(character) > 0x7E for character in token)
    ):
        raise ExternalAgentBridgeError("credential_unavailable")
    return token


class _CoreResponseStream(httpx.AsyncByteStream):
    def __init__(self, response: httpcore.Response):
        self._response = response

    async def __aiter__(self):
        async for chunk in self._response.aiter_stream():
            yield chunk

    async def aclose(self) -> None:
        await self._response.aclose()


class _StreamingPinnedTransport(httpx.AsyncBaseTransport):
    def __init__(self, address: ipaddress._BaseAddress):
        self._pool = httpcore.AsyncConnectionPool(
            network_backend=_PinnedAsyncBackend(address),
            http1=True,
            http2=False,
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        core_request = httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=request.stream,
            extensions=request.extensions,
        )
        try:
            response = await self._pool.handle_async_request(core_request)
        except Exception as exc:
            mapped = _HTTPCORE_TO_HTTPX_EXC.get(type(exc))
            if mapped is not None:
                raise mapped(str(exc)) from exc
            raise
        return httpx.Response(
            status_code=response.status,
            headers=response.headers,
            stream=_CoreResponseStream(response),
            extensions=response.extensions,
        )

    async def aclose(self) -> None:
        await self._pool.aclose()


async def _default_requester(
    method: str,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any] | None,
    timeout: float,
    pinned_ip: ipaddress._BaseAddress,
    max_bytes: int,
) -> tuple[int, dict[str, str], bytes]:
    transport = _StreamingPinnedTransport(pinned_ip)
    try:
        async with httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            async with client.stream(method, url, headers=headers, json=body if body is not None else None) as response:
                declared = response.headers.get("content-length")
                if declared:
                    try:
                        declared_bytes = int(declared)
                        if declared_bytes < 0:
                            raise ExternalAgentBridgeError("malformed_envelope")
                        if declared_bytes > max_bytes:
                            raise ExternalAgentBridgeError("oversized_payload")
                    except ValueError as exc:
                        raise ExternalAgentBridgeError("malformed_envelope") from exc
                chunks = []
                received = 0
                async for chunk in response.aiter_bytes():
                    received += len(chunk)
                    if received > max_bytes:
                        raise ExternalAgentBridgeError("oversized_payload")
                    chunks.append(chunk)
                return response.status_code, dict(response.headers), b"".join(chunks)
    finally:
        await transport.aclose()


class ExternalAgentReadOnlyAdapter:
    adapter_name = "external-agent-sidecar"
    enabled = True
    machine = "External sidecar"
    catalog_capabilities = ["read_only_inspection"]

    def __init__(
        self,
        configuration: dict[str, Any],
        *,
        requester: Requester = _default_requester,
        resolver: Callable[[str], Iterable[str | ipaddress._BaseAddress]] = _default_resolver,
        clock: Callable[[], datetime] = _utcnow,
        event_poll_seconds: float = 1.0,
    ):
        self.worker = configuration["id"]
        self.label = configuration["label"]
        self._endpoint = configuration["endpoint"]
        self._auth_ref = configuration["auth_ref"]
        self._network_policy = configuration["network_policy"]
        self.configured_workspaces = list(configuration["workspaces"])
        configured_capabilities = configuration["capabilities"]
        if isinstance(configured_capabilities, dict):
            self._configured_capabilities = dict(configured_capabilities)
        else:
            self._configured_capabilities = {
                (item["name"] if isinstance(item, dict) else item): (
                    item["effect"] if isinstance(item, dict) else "read"
                )
                for item in configured_capabilities
            }
        self._timeout = float(configuration["timeout_seconds"])
        self._requester = requester
        self._resolver = resolver
        self._clock = clock
        self._event_poll_seconds = max(0.0, float(event_poll_seconds))
        self._seen_messages: set[str] = set()
        self._live_capabilities: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
        self._sidecar_versions: dict[tuple[str, str], str] = {}
        self._event_records: dict[str, str] = {}
        self._event_sequences: dict[str, int] = {}
        self._action_results: dict[str, tuple[str, dict[str, Any]]] = {}
        if set(self._configured_capabilities) & set(EXTERNAL_ACTION_CAPABILITIES):
            self.catalog_capabilities = [
                "read_only_inspection",
                "governed_task_actions",
                *(
                    capability
                    for capability in ("task.start", "task.steer")
                    if capability in self._configured_capabilities
                ),
            ]

    @property
    def connection_ref(self) -> str:
        return _scoped_ref("connection", self.worker)

    @property
    def worker_ref(self) -> str:
        return _scoped_ref("worker", self.worker)

    @property
    def connection_version(self) -> str:
        material = json.dumps(
            {
                "endpoint": self._endpoint,
                "auth_ref": self._auth_ref,
                "network_policy": self._network_policy,
                "workspaces": self.configured_workspaces,
                "capabilities": self._configured_capabilities,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {_read_credential(self._auth_ref)}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _scope(self, owner: str, workspace: str) -> dict[str, str]:
        if workspace not in self.configured_workspaces:
            raise ExternalAgentBridgeError("wrong_workspace")
        if not isinstance(owner, str) or not owner.strip():
            raise ExternalAgentBridgeError("wrong_owner")
        return {
            "owner_ref": _owner_ref(owner),
            "connection_id": self.connection_ref,
            "worker_ref": self.worker_ref,
            "workspace_alias": workspace,
        }

    def _request_envelope(
        self,
        capability: str,
        *,
        owner: str,
        workspace: str,
        arguments: dict[str, Any],
        effect: str = "read",
        task_ref: str | None = None,
        agent_ref: str | None = None,
        stable_id: object | None = None,
    ) -> dict[str, Any]:
        if capability not in self._configured_capabilities and capability != "capabilities.read":
            raise ExternalAgentBridgeError("capability_disabled")
        now = self._clock()
        request_id = (
            _stable_wire_id("request", stable_id)
            if stable_id is not None
            else f"request:{uuid.uuid4()}"
        )
        envelope = {
            "protocol_version": EXTERNAL_AGENT_PROTOCOL,
            "envelope": "request",
            "message_id": f"message:{uuid.uuid4()}",
            "request_id": request_id,
            "issued_at": _timestamp(now),
            **self._scope(owner, workspace),
            "expires_at": _timestamp(now + timedelta(seconds=min(self._timeout, 30))),
            "nonce": (
                _stable_wire_id("nonce", stable_id).replace(":", "-", 1)
                if stable_id is not None
                else uuid.uuid4().hex
            ),
            "capability": capability,
            "effect": effect,
            "arguments": arguments,
        }
        if task_ref is not None:
            envelope["task_ref"] = task_ref
        if agent_ref is not None:
            envelope["agent_ref"] = agent_ref
        return envelope

    def _cancel_envelope(
        self,
        *,
        owner: str,
        workspace: str,
        task_ref: str,
        target_request_id: str,
        stable_id: object,
    ) -> dict[str, Any]:
        now = self._clock()
        return {
            "protocol_version": EXTERNAL_AGENT_PROTOCOL,
            "envelope": "cancel",
            "message_id": f"message:{uuid.uuid4()}",
            "request_id": _stable_wire_id("request", stable_id),
            "issued_at": _timestamp(now),
            **self._scope(owner, workspace),
            "task_ref": task_ref,
            "target_request_id": target_request_id,
            "reason": "operator_request",
        }

    async def _transport(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str],
        body: dict[str, Any] | None,
    ) -> dict[str, Any]:
        addresses = _validated_external_agent_ips(
            self._endpoint,
            self._network_policy,
            resolver=self._resolver,
        )
        try:
            status, response_headers, raw = await asyncio.wait_for(
                self._requester(
                    method,
                    f"{self._endpoint}{path}",
                    headers,
                    body,
                    self._timeout,
                    addresses[0],
                    _MAX_WIRE_BYTES,
                ),
                timeout=self._timeout,
            )
        except ExternalAgentBridgeError:
            raise
        except (asyncio.TimeoutError, httpx.TimeoutException) as exc:
            raise ExternalAgentBridgeError("timeout") from exc
        except (httpx.NetworkError, OSError) as exc:
            raise ExternalAgentBridgeError("sidecar_unavailable") from exc
        if 300 <= status < 400:
            raise ExternalAgentBridgeError("sidecar_unavailable")
        if status in {401, 403}:
            raise ExternalAgentBridgeError("unauthorized")
        if status == 429:
            raise ExternalAgentBridgeError("rate_limited")
        if status < 200 or status >= 300:
            raise ExternalAgentBridgeError("sidecar_unavailable")
        content_type = {
            str(key).lower(): str(value) for key, value in response_headers.items()
        }.get("content-type", "").lower()
        if "application/json" not in content_type:
            raise ExternalAgentBridgeError("malformed_envelope")
        return _bounded_json(raw)

    def _validate_base(self, payload: dict[str, Any], expected: str) -> None:
        if not isinstance(payload.get("protocol_version"), str):
            raise ExternalAgentBridgeError("malformed_envelope")
        if payload["protocol_version"] != EXTERNAL_AGENT_PROTOCOL:
            raise ExternalAgentBridgeError("incompatible_protocol")
        required = ("envelope", "message_id", "request_id", "issued_at")
        if any(not isinstance(payload.get(key), str) or not payload[key] for key in required):
            raise ExternalAgentBridgeError("malformed_envelope")
        if not _REQUEST_ID.fullmatch(payload["message_id"]) or not _REQUEST_ID.fullmatch(payload["request_id"]):
            raise ExternalAgentBridgeError("malformed_envelope")
        if payload["envelope"] != expected:
            raise ExternalAgentBridgeError("malformed_envelope")
        issued_at = _parse_timestamp(payload["issued_at"])
        if abs(self._clock() - issued_at) > _MAX_CLOCK_SKEW:
            raise ExternalAgentBridgeError("stale_request")
        message_id = payload["message_id"]
        if message_id in self._seen_messages:
            raise ExternalAgentBridgeError("replay_detected")
        self._seen_messages.add(message_id)

        allowed = {
            "health": {
                "protocol_version", "envelope", "message_id", "request_id", "issued_at",
                "sidecar_id", "sidecar_version", "protocol_compatible", "status", "checked_at",
            },
            "capabilities": {
                "protocol_version", "envelope", "message_id", "request_id", "issued_at",
                "owner_ref", "connection_id", "worker_ref", "workspace_alias", "agent_ref",
                "sidecar_id", "sidecar_version", "capabilities",
            },
            "response": {
                "protocol_version", "envelope", "message_id", "request_id", "issued_at",
                "owner_ref", "connection_id", "worker_ref", "workspace_alias", "agent_ref",
                "status", "task_ref", "result",
            },
            "error": {
                "protocol_version", "envelope", "message_id", "request_id", "issued_at",
                "code", "retryable", "detail",
            },
            "event": {
                "protocol_version", "envelope", "message_id", "request_id", "issued_at",
                "owner_ref", "connection_id", "worker_ref", "workspace_alias", "agent_ref",
                "task_ref", "event_id", "sequence", "event_type", "text", "metadata",
            },
        }
        if set(payload) - allowed[expected]:
            raise ExternalAgentBridgeError("malformed_envelope")

    def _validate_scope(self, payload: dict[str, Any], request: dict[str, Any]) -> None:
        if payload.get("request_id") != request["request_id"]:
            raise ExternalAgentBridgeError("malformed_envelope")
        if payload.get("owner_ref") != request["owner_ref"]:
            raise ExternalAgentBridgeError("wrong_owner")
        if payload.get("workspace_alias") != request["workspace_alias"]:
            raise ExternalAgentBridgeError("wrong_workspace")
        if (
            payload.get("connection_id") != request["connection_id"]
            or payload.get("worker_ref") != request["worker_ref"]
            or ("agent_ref" in payload and payload.get("agent_ref") != request.get("agent_ref"))
        ):
            raise ExternalAgentBridgeError("malformed_envelope")

    def _validate_error(self, payload: dict[str, Any], request: dict[str, Any] | None) -> None:
        self._validate_base(payload, "error")
        if request is not None and payload.get("request_id") != request["request_id"]:
            raise ExternalAgentBridgeError("malformed_envelope")
        code = payload.get("code")
        if code not in _STABLE_ERRORS or not isinstance(payload.get("retryable"), bool):
            raise ExternalAgentBridgeError("malformed_envelope")
        if not isinstance(payload.get("detail"), str) or not 1 <= len(payload["detail"]) <= 500:
            raise ExternalAgentBridgeError("malformed_envelope")
        raise ExternalAgentBridgeError(code)

    async def _capabilities(self, *, owner: str, workspace: str) -> dict[str, dict[str, Any]]:
        scope_key = (_owner_ref(owner), workspace)
        if scope_key in self._live_capabilities:
            return self._live_capabilities[scope_key]
        request = self._request_envelope(
            "capabilities.read",
            owner=owner,
            workspace=workspace,
            arguments={},
        )
        payload = await self._transport("POST", "/read", headers=self._headers(), body=request)
        if payload.get("envelope") == "error":
            self._validate_error(payload, request)
        self._validate_base(payload, "capabilities")
        self._validate_scope(payload, request)
        declarations = payload.get("capabilities")
        if (
            not isinstance(payload.get("sidecar_id"), str)
            or not _REFERENCE.fullmatch(payload["sidecar_id"])
            or not isinstance(payload.get("sidecar_version"), str)
            or not 1 <= len(payload["sidecar_version"]) <= 80
            or not isinstance(declarations, list)
            or len(declarations) > _MAX_COLLECTION
        ):
            raise ExternalAgentBridgeError("malformed_envelope")
        live: dict[str, dict[str, Any]] = {}
        declared_names: set[str] = set()
        for declaration in declarations:
            if not isinstance(declaration, dict) or set(declaration) != {
                "name", "effect", "authorization", "reversible", "enabled",
            }:
                raise ExternalAgentBridgeError("malformed_envelope")
            name = declaration.get("name")
            if (
                not isinstance(name, str)
                or not _REFERENCE.fullmatch(name)
                or declaration.get("effect") not in {
                    "read", "reversible_write", "destructive_or_difficult_to_recover",
                    "external_publication_or_communication", "purchase",
                    "credential_or_auth_change", "privilege_expansion",
                    "outside_workspace_boundary",
                }
                or declaration.get("authorization") not in {
                    "explicit_request", "separate_gate", "denied",
                }
                or not isinstance(declaration.get("reversible"), bool)
                or not isinstance(declaration.get("enabled"), bool)
                or name in declared_names
            ):
                raise ExternalAgentBridgeError("malformed_envelope")
            declared_names.add(name)
            if name not in self._configured_capabilities or declaration.get("enabled") is not True:
                continue
            if (
                name in EXTERNAL_READ_CAPABILITIES or name == "task.status.read"
            ) and declaration.get("effect") != "read":
                continue
            effective_effect = _strictest_effect(
                self._configured_capabilities[name],
                declaration["effect"],
                "read" if name in EXTERNAL_READ_CAPABILITIES or name == "task.status.read" else "reversible_write",
            )
            required_authorization = (
                "separate_gate" if effective_effect in _SEPARATE_GATE_EFFECTS else "explicit_request"
            )
            if (
                declaration.get("authorization") != required_authorization
                or declaration.get("reversible") is not True
            ):
                continue
            live[name] = {
                "name": name,
                "effect": effective_effect,
                "authorization": required_authorization,
                "reversible": True,
            }
        effective = {
            name: live[name]
            for name in self._configured_capabilities
            if name in live
        }
        self._live_capabilities[scope_key] = effective
        self._sidecar_versions[scope_key] = payload["sidecar_version"]
        return effective

    async def action_policy(
        self,
        action: str,
        *,
        owner: str,
        workspace: str,
    ) -> dict[str, Any]:
        capability = _ACTION_CAPABILITY_BY_NAME.get(action)
        if capability is None:
            raise ExternalAgentBridgeError("capability_disabled")
        return await self._capability_policy(
            capability,
            owner=owner,
            workspace=workspace,
        )

    def validate_action_arguments(self, action: str, arguments: dict[str, Any]) -> None:
        capability = _ACTION_CAPABILITY_BY_NAME.get(action)
        if capability is None:
            raise ExternalAgentBridgeError("capability_disabled")
        _validate_action_arguments(capability, arguments)

    async def _capability_policy(
        self,
        capability: str,
        *,
        owner: str,
        workspace: str,
    ) -> dict[str, Any]:
        effective = await self._capabilities(owner=owner, workspace=workspace)
        if capability not in effective:
            raise ExternalAgentBridgeError("capability_disabled")
        scope_key = (_owner_ref(owner), workspace)
        return {
            **effective[capability],
            "sidecar_version": self._sidecar_versions[scope_key],
            "connection_version": self.connection_version,
            "connection_id": self.connection_ref,
            "worker_ref": self.worker_ref,
        }

    async def _authorized_action(
        self,
        capability: str,
        task: dict[str, Any],
        arguments: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        _validate_action_arguments(capability, arguments)
        owner = str(task.get("owner") or "")
        workspace = str(task.get("workspace") or "")
        self._scope(owner, workspace)
        policy = await self._capability_policy(
            capability,
            owner=owner,
            workspace=workspace,
        )
        binding = {
            "effect": str(task.get("_action_effect") or task.get("action_effect") or ""),
            "capability": str(
                task.get("_action_capability") or task.get("action_capability") or ""
            ),
            "sidecar_version": str(
                task.get("_action_sidecar_version")
                or task.get("external_sidecar_version")
                or ""
            ),
            "connection_version": str(
                task.get("_action_connection_version")
                or task.get("external_connection_version")
                or ""
            ),
            "authority_ref": str(task.get("_action_authority_ref") or task.get("authority_ref") or ""),
            "request_id": str(task.get("_action_request_id") or task.get("request_id") or ""),
            "call_id": str(task.get("_action_call_id") or task.get("call_id") or ""),
        }
        if not all(binding.values()):
            raise ExternalAgentBridgeError("unauthorized")
        if (
            binding["effect"] != policy["effect"]
            or binding["capability"] != capability
            or binding["sidecar_version"] != policy["sidecar_version"]
            or binding["connection_version"] != policy["connection_version"]
        ):
            raise ExternalAgentBridgeError("stale_request")
        return policy, binding

    async def _action_request(
        self,
        capability: str,
        task: dict[str, Any],
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        policy, binding = await self._authorized_action(capability, task, arguments)
        task_ref = str(task.get("remote_task_id") or "") or None
        if capability != "task.start" and (
            task_ref is None
            or not task_ref.startswith("task:")
            or not _REFERENCE.fullmatch(task_ref)
        ):
            raise ExternalAgentBridgeError("malformed_envelope")
        request = self._request_envelope(
            capability,
            owner=str(task["owner"]),
            workspace=str(task["workspace"]),
            arguments=arguments,
            effect=str(policy["effect"]),
            task_ref=task_ref,
            stable_id=f"{binding['request_id']}:{capability}:{task_ref or 'new'}",
        )
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "request_id": request["request_id"],
                    "owner_ref": request["owner_ref"],
                    "connection_id": request["connection_id"],
                    "worker_ref": request["worker_ref"],
                    "workspace_alias": request["workspace_alias"],
                    "capability": capability,
                    "effect": policy["effect"],
                    "task_ref": task_ref,
                    "arguments": arguments,
                    "sidecar_version": policy["sidecar_version"],
                    "connection_version": policy["connection_version"],
                    "authority_ref": binding["authority_ref"],
                    "canonical_request_id": binding["request_id"],
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        cached = self._action_results.get(request["request_id"])
        if cached:
            if cached[0] != fingerprint:
                raise ExternalAgentBridgeError("replay_detected")
            return dict(cached[1])
        payload = await self._transport(
            "POST", "/actions", headers=self._headers(), body=request
        )
        if payload.get("envelope") == "error":
            self._validate_error(payload, request)
        self._validate_base(payload, "response")
        self._validate_scope(payload, request)
        if payload.get("status") not in {"accepted", "succeeded"}:
            raise ExternalAgentBridgeError("malformed_envelope")
        response_task_ref = payload.get("task_ref")
        if capability == "task.start":
            if (
                not isinstance(response_task_ref, str)
                or not response_task_ref.startswith("task:")
                or not _REFERENCE.fullmatch(response_task_ref)
            ):
                raise ExternalAgentBridgeError("malformed_envelope")
        elif response_task_ref is not None and response_task_ref != task_ref:
            raise ExternalAgentBridgeError("malformed_envelope")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise ExternalAgentBridgeError("malformed_envelope")
        normalized = {
            "request_id": request["request_id"],
            "task_ref": response_task_ref or task_ref,
            "status": payload["status"],
            "result": _safe_value(result),
        }
        self._action_results[request["request_id"]] = (fingerprint, normalized)
        return dict(normalized)

    async def start(self, task: dict[str, Any]) -> dict[str, Any]:
        response = await self._action_request(
            "task.start",
            task,
            {
                "prompt": str(task.get("prompt") or ""),
                "permission_mode": str(task.get("permission_mode") or ""),
            },
        )
        status = str(response["result"].get("status") or "queued")
        if status not in {"queued", "running", "waiting", "waiting_approval"}:
            raise ExternalAgentBridgeError("malformed_envelope")
        return {
            "remote_task_id": response["task_ref"],
            "external_start_request_id": response["request_id"],
            "status": status,
        }

    async def status(self, task: dict[str, Any]) -> dict[str, Any]:
        action_task = dict(task)
        action_task.update(
            _action_effect="read",
            _action_capability="task.status.read",
            _action_sidecar_version=task.get("external_sidecar_version"),
            _action_connection_version=task.get("external_connection_version"),
            _action_authority_ref=task.get("authority_ref"),
            _action_request_id=f"status:{uuid.uuid4()}",
            _action_call_id=_stable_wire_id(
                "status", f"{task.get('task_id')}:{uuid.uuid4()}"
            ),
        )
        response = await self._action_request("task.status.read", action_task, {})
        result = dict(response["result"])
        if result.get("status") not in {
            "queued", "running", "waiting", "waiting_approval",
            "completed", "failed", "cancelled",
        }:
            raise ExternalAgentBridgeError("malformed_envelope")
        return result

    async def events(self, task: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        owner = str(task.get("owner") or "")
        workspace = str(task.get("workspace") or "")
        policy = await self._capability_policy(
            "task.events", owner=owner, workspace=workspace
        )
        if (
            str(task.get("external_sidecar_version") or "") != policy["sidecar_version"]
            or str(task.get("external_connection_version") or "") != policy["connection_version"]
        ):
            raise ExternalAgentBridgeError("stale_request")
        remote_task_id = str(task.get("remote_task_id") or "")
        while True:
            cursor: str | None = None
            seen_cursors: set[str] = set()
            terminal_event = False
            for _page_number in range(_MAX_COLLECTION):
                page = await self.task_events(
                    remote_task_id,
                    owner=owner,
                    workspace=workspace,
                    cursor=cursor,
                    limit=_MAX_COLLECTION,
                )
                for event in page["items"]:
                    metadata = dict(event.get("metadata") or {})
                    metadata.update(
                        remote_event_id=event["event_id"],
                        remote_sequence=event["sequence"],
                    )
                    terminal_event = event["event_type"] in {"result", "error", "cancelled"}
                    yield {
                        "event_id": event["event_id"],
                        "type": event["event_type"],
                        "text": event["text"],
                        "metadata": metadata,
                    }
                    if terminal_event:
                        return
                next_cursor = page.get("next_cursor")
                if next_cursor is None:
                    break
                if next_cursor == cursor or next_cursor in seen_cursors:
                    raise ExternalAgentBridgeError("replay_detected")
                seen_cursors.add(next_cursor)
                cursor = next_cursor
            else:
                raise ExternalAgentBridgeError("rate_limited")

            status = await self.status(task)
            state = str(status.get("status") or "")
            if state in {"completed", "failed", "cancelled"}:
                event_type = {
                    "completed": "result",
                    "failed": "error",
                    "cancelled": "cancelled",
                }[state]
                detail = status.get("result") or status.get("error") or state
                yield {
                    "event_id": _stable_wire_id(
                        "event", f"{remote_task_id}:terminal:{state}"
                    ),
                    "type": event_type,
                    "text": _safe_text(detail),
                    "metadata": {"remote_status": state, "reconciled": True},
                }
                return
            if self._event_poll_seconds:
                await asyncio.sleep(self._event_poll_seconds)

    async def steer(self, task: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        return await self._action_request(
            "task.steer", task, {"prompt": payload.get("prompt")}
        )

    async def reply(self, task: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        return await self._action_request(
            "task.reply", task, {"answers": payload.get("answers")}
        )

    async def approve(self, task: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        del task, payload
        raise ExternalAgentBridgeError("capability_disabled")

    async def cancel(self, task: dict[str, Any]) -> dict[str, Any]:
        policy, binding = await self._authorized_action("task.cancel", task, {"reason": "operator_request"})
        task_ref = str(task.get("remote_task_id") or "")
        target_request_id = str(task.get("external_start_request_id") or "")
        if (
            not task_ref.startswith("task:")
            or not _REFERENCE.fullmatch(task_ref)
            or not _REQUEST_ID.fullmatch(target_request_id)
        ):
            raise ExternalAgentBridgeError("malformed_envelope")
        request = self._cancel_envelope(
            owner=str(task["owner"]),
            workspace=str(task["workspace"]),
            task_ref=task_ref,
            target_request_id=target_request_id,
            stable_id=f"{binding['request_id']}:task.cancel:{task_ref}",
        )
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "request_id": request["request_id"],
                    "owner_ref": request["owner_ref"],
                    "connection_id": request["connection_id"],
                    "worker_ref": request["worker_ref"],
                    "workspace_alias": request["workspace_alias"],
                    "task_ref": request["task_ref"],
                    "target_request_id": request["target_request_id"],
                    "reason": request["reason"],
                    "effect": policy["effect"],
                    "sidecar_version": policy["sidecar_version"],
                    "connection_version": policy["connection_version"],
                    "authority_ref": binding["authority_ref"],
                    "canonical_request_id": binding["request_id"],
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        cached = self._action_results.get(request["request_id"])
        if cached:
            if cached[0] != fingerprint:
                raise ExternalAgentBridgeError("replay_detected")
            return dict(cached[1])
        payload = await self._transport("POST", "/actions", headers=self._headers(), body=request)
        if payload.get("envelope") == "error":
            self._validate_error(payload, request)
        self._validate_base(payload, "response")
        self._validate_scope(payload, request)
        if payload.get("status") not in {"accepted", "succeeded"}:
            raise ExternalAgentBridgeError("malformed_envelope")
        if payload.get("task_ref") not in {None, task_ref} or not isinstance(payload.get("result"), dict):
            raise ExternalAgentBridgeError("malformed_envelope")
        normalized = {
            "request_id": request["request_id"],
            "task_ref": task_ref,
            "status": payload["status"],
            "result": _safe_value(payload["result"]),
        }
        self._action_results[request["request_id"]] = (fingerprint, normalized)
        return dict(normalized)

    async def _read(
        self,
        capability: str,
        *,
        owner: str,
        workspace: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if capability not in await self._capabilities(owner=owner, workspace=workspace):
            raise ExternalAgentBridgeError("capability_disabled")
        request = self._request_envelope(
            capability,
            owner=owner,
            workspace=workspace,
            arguments=arguments,
        )
        payload = await self._transport("POST", "/read", headers=self._headers(), body=request)
        if payload.get("envelope") == "error":
            self._validate_error(payload, request)
        self._validate_base(payload, "response")
        self._validate_scope(payload, request)
        if payload.get("status") != "succeeded" or "result" not in payload:
            raise ExternalAgentBridgeError("malformed_envelope")
        result = payload["result"]
        if not isinstance(result, dict):
            raise ExternalAgentBridgeError("malformed_envelope")
        if capability == "task.events":
            events = result.get("items")
            if not isinstance(events, list):
                raise ExternalAgentBridgeError("malformed_envelope")
            fresh_events = []
            for event in events:
                if not isinstance(event, dict):
                    raise ExternalAgentBridgeError("malformed_envelope")
                self._validate_base(event, "event")
                self._validate_scope(event, request)
                task_ref = event.get("task_ref")
                event_id = event.get("event_id")
                sequence = event.get("sequence")
                if (
                    not isinstance(task_ref, str)
                    or not task_ref.startswith("task:")
                    or not _REFERENCE.fullmatch(task_ref)
                    or not isinstance(event_id, str)
                    or not _REQUEST_ID.fullmatch(event_id)
                    or isinstance(sequence, bool)
                    or not isinstance(sequence, int)
                    or sequence < 0
                    or event.get("event_type") not in {
                        "accepted", "progress", "tool_activity", "question",
                        "approval_required", "result", "error", "cancelled",
                    }
                    or not isinstance(event.get("text"), str)
                    or not isinstance(event.get("metadata"), dict)
                ):
                    raise ExternalAgentBridgeError("malformed_envelope")
                fingerprint = hashlib.sha256(
                    json.dumps(
                        {
                            "task_ref": task_ref,
                            "event_id": event_id,
                            "sequence": sequence,
                            "event_type": event["event_type"],
                            "text": event["text"],
                            "metadata": event["metadata"],
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
                previous = self._event_records.get(event_id)
                if previous is not None:
                    if previous != fingerprint:
                        raise ExternalAgentBridgeError("replay_detected")
                    continue
                if sequence <= self._event_sequences.get(task_ref, -1):
                    raise ExternalAgentBridgeError("replay_detected")
                self._event_records[event_id] = fingerprint
                self._event_sequences[task_ref] = sequence
                fresh_events.append(event)
            result = dict(result)
            result["items"] = fresh_events
        return result

    async def health(self, owner: str | None = None) -> dict[str, Any]:
        del owner
        try:
            payload = await self._transport("GET", "/health", headers={"Accept": "application/json"}, body=None)
            if payload.get("envelope") == "error":
                self._validate_error(payload, None)
            self._validate_base(payload, "health")
            if (
                not isinstance(payload.get("sidecar_id"), str)
                or not isinstance(payload.get("sidecar_version"), str)
                or not isinstance(payload.get("protocol_compatible"), bool)
                or not isinstance(payload.get("checked_at"), str)
            ):
                raise ExternalAgentBridgeError("malformed_envelope")
            _parse_timestamp(payload["checked_at"])
            if payload.get("protocol_compatible") is not True:
                return {"state": "incompatible", "reason": "incompatible_protocol"}
            status = payload.get("status")
            if status not in {"healthy", "degraded", "unavailable"}:
                raise ExternalAgentBridgeError("malformed_envelope")
            state = "connected" if status == "healthy" else status
            return {
                "state": state,
                "protocol": EXTERNAL_AGENT_PROTOCOL,
                "protocol_ready": status == "healthy",
                "display_name": self.label,
                "installation_capabilities": [
                    "external_agent",
                    "governed_task_actions"
                    if set(self._configured_capabilities) & set(EXTERNAL_ACTION_CAPABILITIES)
                    else "read_only",
                    *(
                        capability
                        for capability in ("task.start", "task.steer")
                        if capability in self._configured_capabilities
                    ),
                ],
            }
        except ExternalAgentBridgeError as exc:
            if exc.code == "sidecar_unavailable":
                return {"state": "unreachable", "reason": exc.code}
            raise

    async def discovery(self, *, owner: str, workspace: str) -> dict[str, Any]:
        self._scope(owner, workspace)
        status = await self.health(owner=owner)
        state = str(status.get("state") or "unreachable")
        canonical_health = {
            "state": {
                "connected": "healthy",
                "degraded": "degraded",
            }.get(state, "unavailable"),
        }
        if state not in {"connected", "degraded"}:
            canonical_health["reason"] = _safe_text(
                status.get("reason") or "sidecar_unavailable",
                maximum=500,
            )
        effective: dict[str, dict[str, Any]] = {}
        agents = {"items": [], "next_cursor": None}
        if state in {"connected", "degraded"}:
            effective = await self._capabilities(owner=owner, workspace=workspace)
            agents = await self.catalog_agents(
                owner=owner,
                workspace=workspace,
                limit=_MAX_COLLECTION,
            )
        generated_at = _timestamp(self._clock())
        canonical_health["checked_at"] = generated_at

        def entity(
            kind: str,
            reference: str,
            label: str,
            *,
            ownership_scope: str,
            ownership_id: str,
            source_type: str,
            source_ref: str,
            actions: list[dict[str, Any]] | None = None,
        ) -> dict[str, Any]:
            return {
                "kind": kind,
                "id": reference,
                "display_name": label,
                "availability": "available" if canonical_health["state"] != "unavailable" else "unavailable",
                "ownership": {"scope": ownership_scope, "id": ownership_id},
                "health": dict(canonical_health),
                "permissions": {
                    "requires_authenticated_request": True,
                    "configured_scopes": [f"workspace:{workspace}"],
                    "delegation": "narrower_only",
                },
                "source": {"type": source_type, "ref": source_ref},
                "actions": actions or [],
            }

        actions = [dict(policy) for policy in effective.values()]
        entities = [
            entity(
                "connection", self.connection_ref, self.label,
                ownership_scope="installation", ownership_id="installation:pandamonium",
                source_type="configuration", source_ref=f"external-agent/{self.worker}",
            ),
            entity(
                "worker", self.worker_ref, self.label,
                ownership_scope="connection", ownership_id=self.connection_ref,
                source_type="connection", source_ref=f"external-agent/{self.worker}",
                actions=actions,
            ),
            entity(
                "workspace", f"workspace:{workspace}", workspace,
                ownership_scope="worker", ownership_id=self.worker_ref,
                source_type="configuration", source_ref=f"external-agent/{self.worker}",
            ),
        ]
        for value in agents["items"]:
            entities.append(entity(
                "agent", value["agent_ref"], value["display_name"],
                ownership_scope="worker", ownership_id=self.worker_ref,
                source_type="runtime_discovery", source_ref=f"external-agent/{self.worker}",
                actions=actions,
            ))
        return {
            "schema_version": EXTERNAL_DISCOVERY_SCHEMA,
            "generated_at": generated_at,
            "entities": entities,
        }

    @staticmethod
    def _page(result: dict[str, Any], *, limit: int, kind: str) -> dict[str, Any]:
        items = result.get("items")
        cursor = result.get("next_cursor")
        if (
            not isinstance(items, list)
            or len(items) > min(limit, _MAX_COLLECTION)
            or (cursor is not None and (not isinstance(cursor, str) or not _CURSOR.fullmatch(cursor)))
        ):
            raise ExternalAgentBridgeError("malformed_envelope")
        normalized = []
        fields = {
            "agent": ("agent_ref", "display_name", "status"),
            "task": ("task_ref", "agent_ref", "title", "status", "updated_at"),
            "event": ("task_ref", "event_id", "sequence", "event_type", "text", "metadata"),
            "transcript": ("message_id", "role", "text", "created_at"),
        }
        for item in items:
            if not isinstance(item, dict):
                raise ExternalAgentBridgeError("malformed_envelope")
            value = _safe_value({key: item[key] for key in fields[kind] if key in item})
            if kind == "agent":
                required = ("agent_ref", "display_name", "status")
            elif kind == "task":
                required = ("task_ref", "title", "status", "updated_at")
            elif kind == "event":
                required = ("event_id", "sequence", "event_type", "text", "metadata")
            else:
                required = ("message_id", "role", "text", "created_at")
            if any(key not in value for key in required):
                raise ExternalAgentBridgeError("malformed_envelope")
            if kind == "agent" and (
                not isinstance(value["agent_ref"], str)
                or not value["agent_ref"].startswith("agent:")
                or not _REFERENCE.fullmatch(value["agent_ref"])
                or not isinstance(value["display_name"], str)
                or not value["display_name"]
                or not isinstance(value["status"], str)
            ):
                raise ExternalAgentBridgeError("malformed_envelope")
            if kind == "task" and (
                not isinstance(value["task_ref"], str)
                or not value["task_ref"].startswith("task:")
                or not _REFERENCE.fullmatch(value["task_ref"])
                or not isinstance(value["title"], str)
                or not isinstance(value["status"], str)
                or not isinstance(value["updated_at"], str)
            ):
                raise ExternalAgentBridgeError("malformed_envelope")
            if kind == "task":
                _parse_timestamp(value["updated_at"])
            if kind == "transcript" and (
                not isinstance(value["message_id"], str)
                or not _REQUEST_ID.fullmatch(value["message_id"])
                or value["role"] not in {"system", "user", "assistant", "tool"}
                or not isinstance(value["text"], str)
                or not isinstance(value["created_at"], str)
            ):
                raise ExternalAgentBridgeError("malformed_envelope")
            if kind == "transcript":
                _parse_timestamp(value["created_at"])
            normalized.append(value)
        return {"items": normalized, "next_cursor": cursor}

    async def catalog_agents(
        self, *, owner: str, workspace: str, query: str = "", cursor: str | None = None, limit: int = 20,
    ) -> dict[str, Any]:
        limit = _bounded_limit(limit)
        result = await self._read(
            "agent.catalog", owner=owner, workspace=workspace,
            arguments=_catalog_arguments(query, cursor, limit),
        )
        return self._page(result, limit=limit, kind="agent")

    async def catalog_tasks(
        self, *, owner: str, workspace: str, query: str = "", cursor: str | None = None, limit: int = 20,
    ) -> dict[str, Any]:
        limit = _bounded_limit(limit)
        result = await self._read(
            "task.catalog", owner=owner, workspace=workspace,
            arguments=_catalog_arguments(query, cursor, limit),
        )
        return self._page(result, limit=limit, kind="task")

    async def task_events(
        self, task_ref: str, *, owner: str, workspace: str, cursor: str | None = None, limit: int = 20,
    ) -> dict[str, Any]:
        return await self._task_page(
            "task.events", task_ref, owner=owner, workspace=workspace,
            cursor=cursor, limit=limit, kind="event",
        )

    async def task_transcript(
        self, task_ref: str, *, owner: str, workspace: str, cursor: str | None = None, limit: int = 20,
    ) -> dict[str, Any]:
        return await self._task_page(
            "task.transcript", task_ref, owner=owner, workspace=workspace,
            cursor=cursor, limit=limit, kind="transcript",
        )

    async def _task_page(
        self,
        capability: str,
        task_ref: str,
        *,
        owner: str,
        workspace: str,
        cursor: str | None,
        limit: int,
        kind: str,
    ) -> dict[str, Any]:
        if not isinstance(task_ref, str) or not task_ref.startswith("task:") or not _REFERENCE.fullmatch(task_ref):
            raise ExternalAgentBridgeError("malformed_envelope")
        limit = _bounded_limit(limit)
        arguments = {"task_ref": task_ref, "cursor": _bounded_cursor(cursor), "limit": limit}
        result = await self._read(capability, owner=owner, workspace=workspace, arguments=arguments)
        page = self._page(result, limit=limit, kind=kind)
        if kind == "event":
            for event in page["items"]:
                if event.get("task_ref") != task_ref:
                    raise ExternalAgentBridgeError("malformed_envelope")
        return page


def _bounded_limit(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= _MAX_COLLECTION:
        raise ExternalAgentBridgeError("malformed_envelope")
    return limit


def _bounded_cursor(cursor: str | None) -> str | None:
    if cursor is not None and (not isinstance(cursor, str) or not _CURSOR.fullmatch(cursor)):
        raise ExternalAgentBridgeError("malformed_envelope")
    return cursor


def _catalog_arguments(query: str, cursor: str | None, limit: int) -> dict[str, Any]:
    if not isinstance(query, str) or len(query) > 200:
        raise ExternalAgentBridgeError("malformed_envelope")
    return {"query": query, "cursor": _bounded_cursor(cursor), "limit": limit}


_EXTERNAL_ADAPTERS_LOCK = threading.RLock()
_EXTERNAL_ADAPTER_CACHE: dict[
    str, tuple[str, ExternalAgentReadOnlyAdapter]
] = {}


def external_agent_adapters() -> dict[str, ExternalAgentReadOnlyAdapter]:
    configurations = configured_external_agent_connections()
    configured_ids = {configuration["id"] for configuration in configurations}
    with _EXTERNAL_ADAPTERS_LOCK:
        for worker in set(_EXTERNAL_ADAPTER_CACHE) - configured_ids:
            _EXTERNAL_ADAPTER_CACHE.pop(worker, None)
        registry = {}
        for configuration in configurations:
            candidate = ExternalAgentReadOnlyAdapter(configuration)
            cached = _EXTERNAL_ADAPTER_CACHE.get(candidate.worker)
            if cached is None or cached[0] != candidate.connection_version:
                cached = (candidate.connection_version, candidate)
                _EXTERNAL_ADAPTER_CACHE[candidate.worker] = cached
            registry[candidate.worker] = cached[1]
        return registry
