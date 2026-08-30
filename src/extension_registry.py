"""Validated JOS-EXT-1 manifests and effective capability metadata.

The registry deliberately does not fetch descriptors, install packages, run
lifecycle commands, or dispatch tools. Existing adapters resolve MCP, OpenAPI,
inline, or live catalogs and pass the result here for fail-closed reconciliation.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

from core.atomic_io import atomic_write_json
from core.constants import DATA_DIR


MANIFEST_VERSION = "jos-extension.v1"
REGISTRY_VERSION = "jos-extension-registry.v1"
REGISTRY_FILE = Path(DATA_DIR) / "extensions.json"
EXTENSION_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,127}$")
REVISION_PATTERN = re.compile(r"^(?:self|[0-9a-f]{40}|[0-9a-f]{64})$")
IMMUTABLE_REVISION_PATTERN = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
PERMISSION_MODES = frozenset(
    {"read_only", "bounded_write", "external_side_effect", "destructive", "controlled_administrative"}
)
DESCRIPTOR_TYPES = frozenset({"inline", "live_catalog", "mcp", "openapi"})

_TOP_LEVEL_FIELDS = frozenset({
    "protocol_version", "extension_id", "name", "version", "source", "runtime",
    "capabilities", "permissions", "health", "lifecycle", "data_boundaries",
    "removal", "rollback",
})
_REQUIRED_FIELDS = _TOP_LEVEL_FIELDS


class ExtensionContractError(ValueError):
    """A stable fail-closed manifest or catalog validation error."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _object(value: Any, code: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ExtensionContractError(code)
    return dict(value)


def _strict_fields(value: Mapping[str, Any], allowed: set[str] | frozenset[str], code: str) -> None:
    if set(value) - set(allowed):
        raise ExtensionContractError(code)


def _bounded_text(value: Any, code: str, *, maximum: int = 200) -> str:
    text = str(value or "").strip()
    if not text or len(text) > maximum or any(ord(char) < 32 for char in text):
        raise ExtensionContractError(code)
    return text


def _safe_relative_path(value: Any, code: str) -> str:
    text = _bounded_text(value, code, maximum=500)
    path = Path(text)
    if path.is_absolute() or ".." in path.parts:
        raise ExtensionContractError(code)
    return path.as_posix()


def _https_url(value: Any, code: str) -> str:
    text = _bounded_text(value, code, maximum=2_000)
    parsed = urlparse(text)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ExtensionContractError(code)
    return text


def _endpoint(value: Any, code: str) -> str:
    text = _bounded_text(value, code, maximum=2_000)
    if text.startswith("/") and not text.startswith("//"):
        return text
    return _https_url(text, code)


def _string_list(value: Any, code: str, *, paths: bool = False, maximum: int = 64) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ExtensionContractError(code)
    normalizer = _safe_relative_path if paths else _bounded_text
    return [normalizer(item, code) for item in value]


def _normalize_tool_schema(raw: Any) -> dict[str, Any]:
    schema = _object(raw, "extension_capability_schema_malformed")
    function = schema.get("function") if isinstance(schema.get("function"), Mapping) else schema
    name = str(function.get("name") or "").strip()
    description = function.get("description", "")
    parameters = function.get("parameters")
    if not TOOL_NAME_PATTERN.fullmatch(name):
        raise ExtensionContractError("extension_capability_name_invalid")
    if not isinstance(description, str) or len(description) > 4_000:
        raise ExtensionContractError("extension_capability_description_invalid")
    if not isinstance(parameters, Mapping) or parameters.get("type") != "object":
        raise ExtensionContractError("extension_capability_parameters_invalid")
    properties = parameters.get("properties", {})
    required = parameters.get("required", [])
    if not isinstance(properties, Mapping):
        raise ExtensionContractError("extension_capability_properties_invalid")
    if not isinstance(required, list) or len(required) != len(set(required)):
        raise ExtensionContractError("extension_capability_required_invalid")
    if any(not isinstance(item, str) or item not in properties for item in required):
        raise ExtensionContractError("extension_capability_required_invalid")
    if "additionalProperties" in parameters and not isinstance(parameters["additionalProperties"], (bool, Mapping)):
        raise ExtensionContractError("extension_capability_additional_properties_invalid")
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": dict(parameters),
        },
    }


def _normalize_tools(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 256:
        raise ExtensionContractError("extension_capability_catalog_invalid")
    tools = [_normalize_tool_schema(item) for item in value]
    names = [item["function"]["name"] for item in tools]
    if len(names) != len(set(names)):
        raise ExtensionContractError("extension_capability_name_duplicate")
    return tools


def validate_extension_manifest(manifest: Any) -> dict[str, Any]:
    """Return a normalized strict v1 manifest without performing I/O."""
    value = _object(manifest, "extension_manifest_invalid")
    _strict_fields(value, _TOP_LEVEL_FIELDS, "extension_manifest_unknown_field")
    if _REQUIRED_FIELDS - set(value):
        raise ExtensionContractError("extension_manifest_required_field_missing")
    if value.get("protocol_version") != MANIFEST_VERSION:
        raise ExtensionContractError("extension_manifest_version_unsupported")

    extension_id = _bounded_text(value.get("extension_id"), "extension_id_invalid", maximum=64)
    if not EXTENSION_ID_PATTERN.fullmatch(extension_id):
        raise ExtensionContractError("extension_id_invalid")

    source = _object(value.get("source"), "extension_source_invalid")
    _strict_fields(source, {"url", "revision"}, "extension_source_unknown_field")
    revision = _bounded_text(source.get("revision"), "extension_source_revision_invalid", maximum=64)
    if not REVISION_PATTERN.fullmatch(revision):
        raise ExtensionContractError("extension_source_revision_invalid")

    runtime = _object(value.get("runtime"), "extension_runtime_invalid")
    _strict_fields(runtime, {"type", "entrypoint"}, "extension_runtime_unknown_field")
    runtime_type = str(runtime.get("type") or "")
    if runtime_type not in {"web", "service", "mcp", "openapi"}:
        raise ExtensionContractError("extension_runtime_type_invalid")

    capabilities = _object(value.get("capabilities"), "extension_capabilities_invalid")
    _strict_fields(capabilities, {"descriptor", "schemas"}, "extension_capabilities_unknown_field")
    descriptor = _object(capabilities.get("descriptor"), "extension_descriptor_invalid")
    descriptor_type = str(descriptor.get("type") or "")
    if descriptor_type not in DESCRIPTOR_TYPES:
        raise ExtensionContractError("extension_descriptor_type_invalid")
    if descriptor_type == "mcp":
        _strict_fields(descriptor, {"type", "reference"}, "extension_descriptor_unknown_field")
        descriptor["reference"] = _bounded_text(descriptor.get("reference"), "extension_descriptor_reference_invalid")
    elif descriptor_type == "inline":
        _strict_fields(descriptor, {"type"}, "extension_descriptor_unknown_field")
        if "schemas" not in capabilities:
            raise ExtensionContractError("extension_inline_schemas_required")
    else:
        _strict_fields(descriptor, {"type", "endpoint"}, "extension_descriptor_unknown_field")
        descriptor["endpoint"] = _endpoint(descriptor.get("endpoint"), "extension_descriptor_endpoint_invalid")
    if descriptor_type != "inline" and "schemas" in capabilities:
        raise ExtensionContractError("extension_descriptor_schemas_must_be_referenced")
    schemas = _normalize_tools(capabilities.get("schemas", [])) if descriptor_type == "inline" else []

    permissions = _object(value.get("permissions"), "extension_permissions_invalid")
    _strict_fields(permissions, {"default", "capabilities"}, "extension_permissions_unknown_field")
    default_permission = str(permissions.get("default") or "")
    overrides = _object(permissions.get("capabilities"), "extension_permission_overrides_invalid")
    if default_permission not in PERMISSION_MODES or any(mode not in PERMISSION_MODES for mode in overrides.values()):
        raise ExtensionContractError("extension_permission_mode_invalid")
    if any(not TOOL_NAME_PATTERN.fullmatch(str(name)) for name in overrides):
        raise ExtensionContractError("extension_permission_capability_invalid")

    health = _object(value.get("health"), "extension_health_invalid")
    health_type = str(health.get("type") or "")
    if health_type == "catalog":
        _strict_fields(health, {"type", "timeout_seconds"}, "extension_health_unknown_field")
    elif health_type == "http":
        _strict_fields(health, {"type", "endpoint", "timeout_seconds"}, "extension_health_unknown_field")
        health["endpoint"] = _endpoint(health.get("endpoint"), "extension_health_endpoint_invalid")
    else:
        raise ExtensionContractError("extension_health_type_invalid")
    timeout = health.get("timeout_seconds")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 30:
        raise ExtensionContractError("extension_health_timeout_invalid")

    lifecycle = _object(value.get("lifecycle"), "extension_lifecycle_invalid")
    _strict_fields(lifecycle, {"install", "start", "stop", "remove"}, "extension_lifecycle_unknown_field")
    lifecycle = {
        name: _string_list(lifecycle.get(name), f"extension_lifecycle_{name}_invalid", maximum=32)
        for name in ("install", "start", "stop", "remove")
    }

    boundaries = _object(value.get("data_boundaries"), "extension_data_boundaries_invalid")
    _strict_fields(boundaries, {"read", "write", "network"}, "extension_data_boundaries_unknown_field")
    boundaries = {
        "read": _string_list(boundaries.get("read"), "extension_data_read_invalid", paths=True),
        "write": _string_list(boundaries.get("write"), "extension_data_write_invalid", paths=True),
        "network": [_https_url(item, "extension_data_network_invalid") for item in _string_list(
            boundaries.get("network"), "extension_data_network_invalid"
        )],
    }

    removal = _object(value.get("removal"), "extension_removal_invalid")
    _strict_fields(removal, {"remove_paths", "preserve_paths"}, "extension_removal_unknown_field")
    removal = {
        "remove_paths": _string_list(removal.get("remove_paths"), "extension_remove_paths_invalid", paths=True),
        "preserve_paths": _string_list(removal.get("preserve_paths"), "extension_preserve_paths_invalid", paths=True),
    }

    rollback = _object(value.get("rollback"), "extension_rollback_invalid")
    _strict_fields(rollback, {"strategy", "retain_revisions"}, "extension_rollback_unknown_field")
    if rollback.get("strategy") != "pinned_revision":
        raise ExtensionContractError("extension_rollback_strategy_invalid")
    retain = rollback.get("retain_revisions")
    if not isinstance(retain, int) or isinstance(retain, bool) or not 1 <= retain <= 10:
        raise ExtensionContractError("extension_rollback_retention_invalid")

    normalized = dict(value)
    normalized.update({
        "extension_id": extension_id,
        "name": _bounded_text(value.get("name"), "extension_name_invalid"),
        "version": _bounded_text(value.get("version"), "extension_version_invalid", maximum=80),
        "source": {"url": _https_url(source.get("url"), "extension_source_url_invalid"), "revision": revision},
        "runtime": {
            "type": runtime_type,
            "entrypoint": _safe_relative_path(
                runtime.get("entrypoint"), "extension_entrypoint_invalid"
            ),
        },
        "capabilities": {"descriptor": descriptor, **({"schemas": schemas} if schemas else {})},
        "permissions": {"default": default_permission, "capabilities": dict(overrides)},
        "health": health,
        "lifecycle": lifecycle,
        "data_boundaries": boundaries,
        "removal": removal,
        "rollback": {"strategy": "pinned_revision", "retain_revisions": retain},
    })
    return normalized


def reconcile_extension_catalog(
    manifest: Any,
    resolved_catalog: Any = None,
    *,
    source_revision: str,
    health_available: bool,
) -> dict[str, Any]:
    """Reconcile a pre-resolved descriptor with pinned source and health facts."""
    normalized = validate_extension_manifest(manifest)
    if not health_available:
        raise ExtensionContractError("extension_health_unavailable")
    if not IMMUTABLE_REVISION_PATTERN.fullmatch(source_revision):
        raise ExtensionContractError("extension_source_revision_invalid")
    declared_revision = normalized["source"]["revision"]
    if declared_revision not in {"self", source_revision}:
        raise ExtensionContractError("extension_source_revision_mismatch")
    normalized["source"]["revision"] = source_revision

    descriptor_type = normalized["capabilities"]["descriptor"]["type"]
    if descriptor_type == "inline":
        tools = normalized["capabilities"]["schemas"]
        catalog_version = normalized["version"]
    else:
        catalog = _object(resolved_catalog, "extension_resolved_catalog_required")
        _strict_fields(
            catalog,
            {"protocol_version", "extension_id", "version", "source_revision", "tools"},
            "extension_catalog_unknown_field",
        )
        if catalog.get("protocol_version") != MANIFEST_VERSION:
            raise ExtensionContractError("extension_catalog_version_unsupported")
        if catalog.get("extension_id") != normalized["extension_id"]:
            raise ExtensionContractError("extension_catalog_id_mismatch")
        if catalog.get("source_revision") != source_revision:
            raise ExtensionContractError("extension_catalog_revision_mismatch")
        tools = _normalize_tools(catalog.get("tools"))
        catalog_version = _bounded_text(catalog.get("version"), "extension_catalog_release_invalid", maximum=80)

    overrides = normalized["permissions"]["capabilities"]
    names = {tool["function"]["name"] for tool in tools}
    if set(overrides) - names:
        raise ExtensionContractError("extension_permission_capability_unknown")
    effective = [
        {
            "name": tool["function"]["name"],
            "schema": tool,
            "permission_mode": overrides.get(
                tool["function"]["name"], normalized["permissions"]["default"]
            ),
        }
        for tool in tools
    ]
    return {"manifest": normalized, "catalog_version": catalog_version, "capabilities": effective}


class ExtensionRegistry:
    """Atomic validated metadata store; execution remains in native runners."""

    def __init__(self, path: Path | str = REGISTRY_FILE):
        self.path = Path(path)
        self._lock = threading.RLock()

    def _read(self) -> dict[str, Any]:
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {"schema_version": REGISTRY_VERSION, "extensions": {}}
        if state.get("schema_version") != REGISTRY_VERSION or not isinstance(state.get("extensions"), dict):
            return {"schema_version": REGISTRY_VERSION, "extensions": {}}
        extensions = {}
        for extension_id, record in state["extensions"].items():
            try:
                _strict_fields(
                    record,
                    {"enabled", "manifest", "catalog_version", "effective_capabilities"},
                    "extension_registry_record_unknown_field",
                )
                manifest = validate_extension_manifest(record["manifest"])
                if extension_id != manifest["extension_id"] or not isinstance(record["enabled"], bool):
                    raise ExtensionContractError("extension_registry_record_invalid")
                capabilities = []
                seen = set()
                for raw in record["effective_capabilities"]:
                    _strict_fields(
                        raw,
                        {"name", "schema", "permission_mode"},
                        "extension_registry_capability_unknown_field",
                    )
                    schema = _normalize_tool_schema(raw["schema"])
                    name = schema["function"]["name"]
                    if raw.get("name") != name or name in seen or raw.get("permission_mode") not in PERMISSION_MODES:
                        raise ExtensionContractError("extension_registry_capability_invalid")
                    seen.add(name)
                    capabilities.append({"name": name, "schema": schema, "permission_mode": raw["permission_mode"]})
                extensions[extension_id] = {
                    "enabled": record["enabled"],
                    "manifest": manifest,
                    "catalog_version": _bounded_text(
                        record["catalog_version"],
                        "extension_catalog_release_invalid",
                        maximum=80,
                    ),
                    "effective_capabilities": capabilities,
                }
            except (ExtensionContractError, KeyError, TypeError):
                continue
        return {"schema_version": REGISTRY_VERSION, "extensions": extensions}

    def _write(self, state: dict[str, Any]) -> None:
        atomic_write_json(str(self.path), state, indent=2)

    def register(
        self,
        manifest: Any,
        resolved_catalog: Any = None,
        *,
        source_revision: str,
        health_available: bool,
    ) -> dict[str, Any]:
        reconciled = reconcile_extension_catalog(
            manifest,
            resolved_catalog,
            source_revision=source_revision,
            health_available=health_available,
        )
        extension_id = reconciled["manifest"]["extension_id"]
        with self._lock:
            state = self._read()
            existing_names = {
                capability["name"]
                for key, record in state["extensions"].items()
                if key != extension_id and record.get("enabled")
                for capability in record.get("effective_capabilities", [])
            }
            incoming_names = {item["name"] for item in reconciled["capabilities"]}
            if existing_names & incoming_names:
                raise ExtensionContractError("extension_registry_capability_conflict")
            record = {
                "enabled": True,
                "manifest": reconciled["manifest"],
                "catalog_version": reconciled["catalog_version"],
                "effective_capabilities": reconciled["capabilities"],
            }
            state["extensions"][extension_id] = record
            self._write(state)
            return json.loads(json.dumps(record))

    def disable(self, extension_id: str) -> bool:
        with self._lock:
            state = self._read()
            record = state["extensions"].get(extension_id)
            if not record:
                return False
            record["enabled"] = False
            record["effective_capabilities"] = []
            self._write(state)
            return True

    def unregister(self, extension_id: str) -> bool:
        with self._lock:
            state = self._read()
            if extension_id not in state["extensions"]:
                return False
            del state["extensions"][extension_id]
            self._write(state)
            return True

    def effective_capabilities(self, engaged_ids: Iterable[str] | None = None) -> dict[str, dict[str, Any]]:
        engaged = set(engaged_ids) if engaged_ids is not None else None
        result: dict[str, dict[str, Any]] = {}
        for extension_id, record in self._read()["extensions"].items():
            if not record.get("enabled") or (engaged is not None and extension_id not in engaged):
                continue
            for capability in record.get("effective_capabilities", []):
                result[capability["name"]] = {
                    "extension_id": extension_id,
                    "permission_mode": capability["permission_mode"],
                    "schema": capability["schema"],
                }
        return result

    def context_extensions(self, engaged_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        engaged = set(engaged_ids)
        return {
            extension_id: {
                "engaged": True,
                "state_mounted": True,
                "tool_count": len(record.get("effective_capabilities", [])),
            }
            for extension_id, record in self._read()["extensions"].items()
            if extension_id in engaged and record.get("enabled")
        }

    def snapshot(self) -> dict[str, Any]:
        return json.loads(json.dumps(self._read()))
