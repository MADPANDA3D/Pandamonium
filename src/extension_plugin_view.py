"""MAD-940: sanitized installed/configured plugin projections for the operator UI.

Read-only: builds list and detail payloads from the extension registry and the
configured (non-registry) surfaces. Never includes secret values, absolute
paths, private endpoints, or owner data.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from src.extension_capability_inventory import advisory_capability_items
from src.extension_cli_adapter import is_cli
from src.extension_registry import ExtensionRegistry

MAX_DESCRIPTION_CHARS = 300
MAX_NAME_CHARS = 200
MAX_VERSION_CHARS = 80

BROWSER_SURFACE_NOTE = (
    "Browser-surface extension: its tools become available when the surface is engaged."
)
CONFIGURED_SURFACE_NOTE = (
    "Configured surface, not installed through the plugin registry. Its live capability "
    "catalog is resolved when the session engages it."
)


def _text(value: Any, maximum: int) -> str:
    return str(value if value is not None else "").strip()[:maximum]


def _capability_description(inventory: Mapping[str, Any], name: str) -> str:
    for item in inventory.get("capabilities") or []:
        if not isinstance(item, Mapping) or item.get("name") != name or item.get("kind") != "tool":
            continue
        schema = item.get("schema") if isinstance(item.get("schema"), Mapping) else {}
        function = schema.get("function") if isinstance(schema.get("function"), Mapping) else {}
        description = _text(function.get("description"), MAX_DESCRIPTION_CHARS)
        if description:
            return description
    return ""


def _registry_rows(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for extension_id, record in sorted(snapshot.items()):
        if not isinstance(record, Mapping):
            continue
        manifest = record.get("manifest") if isinstance(record.get("manifest"), Mapping) else {}
        inventory = record.get("capability_inventory")
        items = advisory_capability_items(inventory) if inventory else []
        runtime = manifest.get("runtime") if isinstance(manifest.get("runtime"), Mapping) else {}
        descriptor = (
            manifest.get("capabilities", {}).get("descriptor")
            if isinstance(manifest.get("capabilities"), Mapping)
            else {}
        )
        rows.append({
            "id": extension_id,
            "name": _text(manifest.get("name") or extension_id, MAX_NAME_CHARS),
            "version": _text(manifest.get("version"), MAX_VERSION_CHARS),
            "state": "enabled" if record.get("enabled") else "disabled",
            "runtime": _text(runtime.get("type"), 40),
            "descriptor": _text((descriptor or {}).get("type"), 40),
            "origin": "registry",
            "capability_count": len(items),
        })
    return rows


def installed_plugin_rows(
    registry: ExtensionRegistry,
    *,
    configured_surfaces: Sequence[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    """List installed registry extensions plus configured surfaces."""
    snapshot = registry.snapshot().get("extensions", {})
    rows = _registry_rows(snapshot)
    for surface in configured_surfaces:
        surface_id = _text(surface.get("id"), 64)
        if not surface_id or surface_id in snapshot:
            continue
        rows.append({
            "id": surface_id,
            "name": _text(surface.get("name") or surface_id, MAX_NAME_CHARS),
            "version": "",
            "state": "configured",
            "runtime": _text(surface.get("runtime") or "web", 40),
            "descriptor": "live_catalog",
            "origin": "configured",
            "capability_count": 0,
        })
    rows.sort(key=lambda row: (str(row["name"]).lower(), str(row["id"])))
    return rows


def _registry_detail(extension_id: str, record: Mapping[str, Any]) -> dict[str, Any]:
    manifest = record.get("manifest") if isinstance(record.get("manifest"), Mapping) else {}
    inventory = record.get("capability_inventory")
    items = advisory_capability_items(inventory) if inventory else []
    capabilities: list[dict[str, Any]] = []
    for item in items:
        capability = dict(item)
        description = _capability_description(inventory, str(item.get("name") or ""))
        if description:
            capability["description"] = description
        capabilities.append(capability)
    permissions = manifest.get("permissions") if isinstance(manifest.get("permissions"), Mapping) else {}
    boundaries = (
        manifest.get("data_boundaries")
        if isinstance(manifest.get("data_boundaries"), Mapping)
        else {}
    )
    runtime = manifest.get("runtime") if isinstance(manifest.get("runtime"), Mapping) else {}
    descriptor_block = (
        manifest.get("capabilities", {}).get("descriptor")
        if isinstance(manifest.get("capabilities"), Mapping)
        else {}
    )
    descriptor = _text((descriptor_block or {}).get("type"), 40)
    notes: list[str] = []
    if is_cli(manifest):
        notes.append("Native CLI tools: discover and mount these capabilities through manage_extensions.")
    elif str(runtime.get("type") or "") == "web" or descriptor in {"live_catalog", "inline"}:
        notes.append(BROWSER_SURFACE_NOTE)
    return {
        "id": extension_id,
        "name": _text(manifest.get("name") or extension_id, MAX_NAME_CHARS),
        "version": _text(manifest.get("version"), MAX_VERSION_CHARS),
        "state": "enabled" if record.get("enabled") else "disabled",
        "runtime": _text(runtime.get("type"), 40),
        "descriptor": descriptor,
        "origin": "registry",
        "source_revision": _text(
            (manifest.get("source") or {}).get("revision")
            if isinstance(manifest.get("source"), Mapping)
            else "",
            64,
        ),
        "permissions": {
            "default": _text(permissions.get("default"), 40) or "read_only",
            "capabilities": {
                _text(name, 128): _text(mode, 40)
                for name, mode in (permissions.get("capabilities") or {}).items()
            },
        },
        "data_boundaries": {
            "read": [_text(item, 200) for item in (boundaries.get("read") or [])][:64],
            "write": [_text(item, 200) for item in (boundaries.get("write") or [])][:64],
            "network": [_text(item, 300) for item in (boundaries.get("network") or [])][:64],
        },
        "capabilities": capabilities,
        "configuration": [
            {
                "key": _text(item.get("key"), 64),
                "description": _text(item.get("description"), 200),
                "required": bool(item.get("required")),
                "secret": bool(item.get("secret")),
            }
            for item in (manifest.get("configuration") or [])[:32]
            if isinstance(item, Mapping)
        ],
        "notes": notes,
    }


def _configured_detail(
    surface: Mapping[str, Any],
) -> dict[str, Any]:
    surface_id = _text(surface.get("id"), 64)
    return {
        "id": surface_id,
        "name": _text(surface.get("name") or surface_id, MAX_NAME_CHARS),
        "version": "",
        "state": "configured",
        "runtime": _text(surface.get("runtime") or "web", 40),
        "descriptor": "live_catalog",
        "origin": "configured",
        "source_revision": "",
        "permissions": {"default": "read_only", "capabilities": {}},
        "data_boundaries": {"read": [], "write": [], "network": []},
        "capabilities": [],
        "configuration": [],
        "notes": [CONFIGURED_SURFACE_NOTE],
    }


def installed_plugin_detail(
    registry: ExtensionRegistry,
    extension_id: str,
    *,
    configured_surfaces: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any] | None:
    """Detail for one installed extension or configured surface, or None."""
    extension_id = _text(extension_id, 64)
    if not extension_id:
        return None
    snapshot = registry.snapshot().get("extensions", {})
    record = snapshot.get(extension_id)
    if isinstance(record, Mapping):
        return _registry_detail(extension_id, record)
    for surface in configured_surfaces:
        if _text(surface.get("id"), 64) == extension_id:
            return _configured_detail(surface)
    return None
