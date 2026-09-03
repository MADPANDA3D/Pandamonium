"""JOS-EXT-1 adapter over Pandamonium's existing native MCP manager."""

from __future__ import annotations

import asyncio
import concurrent.futures
import ipaddress
import json
import threading
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

from src.extension_installer import ExtensionLifecycleError
from src.extension_registry import (
    ExtensionContractError,
    MANIFEST_VERSION,
    reconcile_extension_catalog,
)
from src.tool_utils import get_mcp_manager


EXTENSION_MCP_MAX_RESULT_BYTES = 64 * 1024
_MAX_ARGS = 64
_MAX_ENV_KEYS = 128
_MAX_CONFIG_TEXT = 8 * 1024


def _json_value(value: Any, expected: type, code: str):
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError) as exc:
        raise ExtensionLifecycleError(code) from exc
    if not isinstance(parsed, expected):
        raise ExtensionLifecycleError(code)
    return parsed


def _default_config_provider(reference: str) -> dict[str, Any] | None:
    """Read one admin-owned MCP config without copying it to extension state."""
    from src.database import McpServer, SessionLocal

    db = SessionLocal()
    try:
        server = db.query(McpServer).filter(McpServer.id == reference).first()
        if server is None:
            return None
        return {
            "id": server.id,
            "name": server.name,
            "transport": server.transport,
            "command": server.command,
            "args": _json_value(server.args or "[]", list, "extension_mcp_config_malformed"),
            "env": _json_value(server.env or "{}", dict, "extension_mcp_config_malformed"),
            "url": server.url,
            "is_enabled": bool(server.is_enabled),
        }
    finally:
        db.close()


def _entrypoint(install_path: Path, manifest: Mapping[str, Any]) -> Path:
    root = install_path.resolve()
    candidate = root / str((manifest.get("runtime") or {}).get("entrypoint") or "")
    try:
        current = root
        for part in candidate.relative_to(root).parts:
            current = current / part
            if current.is_symlink():
                raise ExtensionLifecycleError("extension_entrypoint_unsafe")
        resolved = candidate.resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise ExtensionLifecycleError("extension_entrypoint_unavailable") from exc
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ExtensionLifecycleError("extension_entrypoint_unavailable")
    return resolved


def _loopback_url(value: Any) -> str:
    text = str(value or "").strip()
    try:
        parsed = urlparse(text)
        port = parsed.port
    except ValueError as exc:
        raise ExtensionLifecycleError("extension_mcp_loopback_required") from exc
    host = str(parsed.hostname or "").strip().lower()
    try:
        loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if (
        parsed.scheme not in {"http", "https"}
        or not loopback
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or port is not None and not 1 <= port <= 65535
    ):
        raise ExtensionLifecycleError("extension_mcp_loopback_required")
    return text


def _runtime_config(
    install_path: Path,
    manifest: Mapping[str, Any],
    provider: Callable[[str], Mapping[str, Any] | None],
) -> dict[str, Any]:
    lifecycle = manifest.get("lifecycle") or {}
    if any(lifecycle.get(name) for name in ("install", "start", "stop", "remove")):
        raise ExtensionLifecycleError("extension_mcp_lifecycle_must_be_empty")
    descriptor = ((manifest.get("capabilities") or {}).get("descriptor") or {})
    reference = str(descriptor.get("reference") or "")
    raw = provider(reference)
    if not isinstance(raw, Mapping) or raw.get("id") != reference:
        raise ExtensionLifecycleError("extension_mcp_config_unavailable")
    if raw.get("is_enabled") is not False:
        raise ExtensionLifecycleError("extension_mcp_dual_exposure")
    transport = str(raw.get("transport") or "")
    if transport not in {"stdio", "sse", "http"}:
        raise ExtensionLifecycleError("extension_mcp_transport_unsupported")

    config = {
        "server_id": reference,
        "name": str(manifest.get("name") or manifest.get("extension_id") or "extension"),
        "transport": transport,
        "command": None,
        "args": [],
        "env": {},
        "url": None,
    }
    if transport == "stdio":
        command = str(raw.get("command") or "")
        args = _json_value(raw.get("args", []), list, "extension_mcp_config_malformed")
        env = _json_value(raw.get("env", {}), dict, "extension_mcp_config_malformed")
        if (
            not command
            or len(command) > _MAX_CONFIG_TEXT
            or len(args) > _MAX_ARGS
            or any(not isinstance(item, str) or len(item) > _MAX_CONFIG_TEXT for item in args)
            or len(env) > _MAX_ENV_KEYS
            or any(
                not isinstance(key, str)
                or not key
                or len(key) > 200
                or not isinstance(value, str)
                or len(value) > _MAX_CONFIG_TEXT
                for key, value in env.items()
            )
        ):
            raise ExtensionLifecycleError("extension_mcp_config_malformed")
        values = [command, *args]
        placeholders = sum(value.count("{entrypoint}") for value in values)
        if placeholders != 1 or any(
            "{entrypoint}" in value and value != "{entrypoint}" for value in values
        ):
            raise ExtensionLifecycleError("extension_mcp_entrypoint_unpinned")
        entrypoint = str(_entrypoint(install_path, manifest))
        config.update({
            "command": entrypoint if command == "{entrypoint}" else command,
            "args": [entrypoint if item == "{entrypoint}" else item for item in args],
            "env": dict(env),
        })
    else:
        config["url"] = _loopback_url(raw.get("url"))
    return config


def _resolved_catalog(
    manifest: Mapping[str, Any], source_revision: str, manager: Any, server_id: str
) -> dict[str, Any]:
    status = manager.get_server_status(server_id)
    if not isinstance(status, Mapping) or status.get("status") != "connected":
        raise ExtensionLifecycleError("extension_mcp_unavailable")
    server_info = status.get("server_info")
    if (
        not isinstance(server_info, Mapping)
        or server_info.get("name") != manifest.get("extension_id")
        or server_info.get("version") != source_revision
    ):
        raise ExtensionLifecycleError("extension_mcp_identity_mismatch")
    raw_tools = manager.get_server_tools(server_id)
    if not isinstance(raw_tools, list) or len(raw_tools) > 256:
        raise ExtensionLifecycleError("extension_mcp_catalog_malformed")
    tools = []
    for raw in raw_tools:
        if not isinstance(raw, Mapping):
            raise ExtensionLifecycleError("extension_mcp_catalog_malformed")
        tools.append({
            "type": "function",
            "function": {
                "name": raw.get("name"),
                "description": raw.get("description", ""),
                "parameters": raw.get("input_schema"),
            },
        })
    catalog = {
        "protocol_version": MANIFEST_VERSION,
        "extension_id": manifest.get("extension_id"),
        "version": manifest.get("version"),
        "source_revision": source_revision,
        "tools": tools,
    }
    try:
        reconcile_extension_catalog(
            manifest, catalog, source_revision=source_revision, health_available=True
        )
    except ExtensionContractError as exc:
        raise ExtensionLifecycleError(
            "extension_mcp_duplicate_tools"
            if exc.code == "extension_capability_name_duplicate"
            else "extension_mcp_catalog_malformed"
        ) from exc
    return catalog


def _catalog_matches_record(record: Mapping[str, Any], manager: Any) -> bool:
    if not record.get("enabled"):
        return False
    manifest = record.get("manifest")
    if not isinstance(manifest, Mapping):
        return False
    descriptor = ((manifest.get("capabilities") or {}).get("descriptor") or {})
    reference = str(descriptor.get("reference") or "")
    revision = str((manifest.get("source") or {}).get("revision") or "")
    if not reference or not manager.is_extension_server(reference):
        return False
    try:
        catalog = _resolved_catalog(manifest, revision, manager, reference)
        reconciled = reconcile_extension_catalog(
            manifest, catalog, source_revision=revision, health_available=True
        )
    except (ExtensionContractError, ExtensionLifecycleError, TypeError, ValueError):
        return False
    return (
        reconciled["catalog_version"] == record.get("catalog_version")
        and reconciled["capabilities"] == record.get("effective_capabilities")
    )


def mcp_extension_tool_specs(
    record: Mapping[str, Any], *, manager: Any | None = None
) -> list[dict[str, Any]]:
    """Expose an exact live MCP catalog through the extension path only."""
    manager = manager or get_mcp_manager()
    if manager is None or not _catalog_matches_record(record, manager):
        return []
    manifest = record["manifest"]
    reference = manifest["capabilities"]["descriptor"]["reference"]
    timeout = manifest["health"]["timeout_seconds"]
    return [
        {
            "type": "function",
            "name": item["name"],
            "description": item["schema"]["function"].get("description", ""),
            "parameters": item["schema"]["function"]["parameters"],
            "extension_id": manifest["extension_id"],
            "permission_mode": item["permission_mode"],
            "mcp_qualified_name": f"mcp__{reference}__{item['name']}",
            "timeout_seconds": timeout,
            "max_output_bytes": EXTENSION_MCP_MAX_RESULT_BYTES,
        }
        for item in record.get("effective_capabilities", [])
    ]


async def execute_mcp_extension_tool(
    record: Mapping[str, Any],
    name: str,
    arguments: Mapping[str, Any],
    *,
    manager: Any | None = None,
) -> dict[str, Any]:
    """Reconcile immediately before one bounded call through native MCP."""
    manager = manager or get_mcp_manager()
    specs = {
        item["name"]: item for item in mcp_extension_tool_specs(record, manager=manager)
    }
    spec = specs.get(name)
    if spec is None or not isinstance(arguments, Mapping):
        return {"error": "extension_mcp_capability_unavailable", "exit_code": 1}
    return await manager.call_tool(
        spec["mcp_qualified_name"],
        dict(arguments),
        timeout_seconds=spec["timeout_seconds"],
        max_output_bytes=spec["max_output_bytes"],
    )


class McpExtensionAdapter:
    """Small lifecycle shim; transport and process ownership stay in McpManager."""

    def __init__(
        self,
        *,
        manager_provider: Callable[[], Any] = get_mcp_manager,
        config_provider: Callable[[str], Mapping[str, Any] | None] = _default_config_provider,
    ):
        self._manager_provider = manager_provider
        self._config_provider = config_provider
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.RLock()
        self._validated: dict[tuple[str, str], tuple[str, dict[str, Any]]] = {}
        self._active: dict[str, tuple[Path, dict[str, Any], str, dict[str, Any]]] = {}
        self._transactions: dict[
            str, tuple[Path, dict[str, Any], str, dict[str, Any]] | None
        ] = {}

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def supports(self, manifest: Mapping[str, Any]) -> bool:
        lifecycle = manifest.get("lifecycle") or {}
        descriptor = ((manifest.get("capabilities") or {}).get("descriptor") or {})
        return (
            (manifest.get("runtime") or {}).get("type") == "mcp"
            and descriptor.get("type") == "mcp"
            and all(not lifecycle.get(name) for name in ("install", "start", "stop", "remove"))
        )

    def _manager(self):
        manager = self._manager_provider()
        if manager is None:
            raise ExtensionLifecycleError("extension_mcp_manager_unavailable")
        return manager

    def _run(self, coroutine, timeout: float):
        loop = self._loop
        if loop is None or loop.is_closed():
            coroutine.close()
            raise ExtensionLifecycleError("extension_mcp_event_loop_unavailable")
        future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise ExtensionLifecycleError("extension_mcp_timeout") from exc

    async def _connect(
        self,
        manager: Any,
        server_id: str,
        config: Mapping[str, Any],
        timeout: float,
    ) -> bool:
        try:
            return bool(await asyncio.wait_for(
                manager.connect_server(
                    server_id=server_id,
                    name=config["name"],
                    transport=config["transport"],
                    command=config["command"],
                    args=config["args"],
                    env=config["env"],
                    url=config["url"],
                    identity_from_env=False,
                ),
                timeout=timeout,
            ))
        except asyncio.TimeoutError:
            return False

    async def _probe_async(
        self,
        install_path: Path,
        manifest: Mapping[str, Any],
        revision: str,
    ) -> dict[str, Any]:
        manager = self._manager()
        config = _runtime_config(install_path, manifest, self._config_provider)
        probe_id = f"jos_probe_{uuid.uuid4().hex}"
        manager.reserve_extension_server(probe_id)
        try:
            connected = await self._connect(
                manager, probe_id, config, manifest["health"]["timeout_seconds"]
            )
            if not connected:
                raise ExtensionLifecycleError("extension_mcp_unavailable")
            return _resolved_catalog(manifest, revision, manager, probe_id)
        finally:
            await manager.disconnect_server(probe_id)
            manager.release_extension_server(probe_id)

    def validate(
        self, install_path: Path, manifest: Mapping[str, Any], source_revision: str
    ) -> tuple[Mapping[str, Any], bool]:
        _runtime_config(install_path, manifest, self._config_provider)
        timeout = float(manifest["health"]["timeout_seconds"]) + 1.0
        catalog = self._run(
            self._probe_async(install_path, manifest, source_revision), timeout
        )
        with self._lock:
            self._validated[(str(manifest["extension_id"]), str(install_path.resolve()))] = (
                source_revision,
                catalog,
            )
        return catalog, True

    async def _connect_active_async(
        self,
        install_path: Path,
        manifest: Mapping[str, Any],
        revision: str,
        expected: Mapping[str, Any],
    ) -> None:
        manager = self._manager()
        config = _runtime_config(install_path, manifest, self._config_provider)
        server_id = config["server_id"]
        manager.reserve_extension_server(server_id)
        await manager.disconnect_server(server_id)
        connected = await self._connect(
            manager, server_id, config, manifest["health"]["timeout_seconds"]
        )
        if not connected:
            manager.release_extension_server(server_id)
            raise ExtensionLifecycleError("extension_mcp_unavailable")
        try:
            catalog = _resolved_catalog(manifest, revision, manager, server_id)
            if catalog != expected:
                raise ExtensionLifecycleError("extension_mcp_catalog_changed")
        except Exception:
            await manager.disconnect_server(server_id)
            manager.release_extension_server(server_id)
            raise

    async def _disconnect_async(self, manifest: Mapping[str, Any]) -> None:
        manager = self._manager()
        reference = manifest["capabilities"]["descriptor"]["reference"]
        await manager.disconnect_server(reference)
        manager.release_extension_server(reference)

    def _restore(self, previous) -> None:
        if previous is None:
            return
        path, manifest, revision, catalog = previous
        timeout = float(manifest["health"]["timeout_seconds"]) + 1.0
        self._run(
            self._connect_active_async(path, manifest, revision, catalog), timeout
        )
        self._active[manifest["extension_id"]] = previous

    def activate(self, install_path: Path, manifest: Mapping[str, Any]) -> None:
        extension_id = str(manifest["extension_id"])
        key = (extension_id, str(install_path.resolve()))
        with self._lock:
            validated = self._validated.get(key)
            if validated is None:
                raise ExtensionLifecycleError("extension_mcp_validation_required")
            revision, catalog = validated
            previous = self._active.get(extension_id)
            timeout = float(manifest["health"]["timeout_seconds"]) + 1.0
            try:
                self._run(
                    self._connect_active_async(
                        install_path, manifest, revision, catalog
                    ),
                    timeout,
                )
            except Exception:
                self._restore(previous)
                raise
            current = (install_path.resolve(), dict(manifest), revision, catalog)
            self._transactions[extension_id] = previous
            self._active[extension_id] = current

    def rollback_activation(self, manifest: Mapping[str, Any]) -> None:
        extension_id = str(manifest["extension_id"])
        with self._lock:
            timeout = float(manifest["health"]["timeout_seconds"]) + 1.0
            self._run(self._disconnect_async(manifest), timeout)
            self._active.pop(extension_id, None)
            self._restore(self._transactions.pop(extension_id, None))

    def commit_activation(self, manifest: Mapping[str, Any]) -> None:
        with self._lock:
            self._transactions.pop(str(manifest["extension_id"]), None)

    def deactivate(self, install_path: Path, manifest: Mapping[str, Any]) -> None:
        extension_id = str(manifest["extension_id"])
        with self._lock:
            timeout = float(manifest["health"]["timeout_seconds"]) + 1.0
            self._run(self._disconnect_async(manifest), timeout)
            self._active.pop(extension_id, None)
            self._transactions.pop(extension_id, None)

    async def restore_enabled(self, *, registry, root: Path) -> dict[str, bool]:
        """Rehydrate enabled pinned runtimes; any drift remains disconnected."""
        results: dict[str, bool] = {}
        for extension_id, record in registry.snapshot().get("extensions", {}).items():
            manifest = record.get("manifest") or {}
            if not record.get("enabled") or not self.supports(manifest):
                continue
            revision = str((manifest.get("source") or {}).get("revision") or "")
            path = Path(root) / "installed" / extension_id / "revisions" / revision
            expected = {
                "protocol_version": MANIFEST_VERSION,
                "extension_id": extension_id,
                "version": record.get("catalog_version"),
                "source_revision": revision,
                "tools": [item["schema"] for item in record.get("effective_capabilities", [])],
            }
            try:
                await self._connect_active_async(path, manifest, revision, expected)
                if not _catalog_matches_record(record, self._manager()):
                    raise ExtensionLifecycleError("extension_mcp_catalog_changed")
                self._active[extension_id] = (path.resolve(), dict(manifest), revision, expected)
                self._validated[(extension_id, str(path.resolve()))] = (revision, expected)
                results[extension_id] = True
            except Exception:
                try:
                    await self._disconnect_async(manifest)
                except Exception:
                    pass
                results[extension_id] = False
        return results


mcp_extension_adapter = McpExtensionAdapter()
