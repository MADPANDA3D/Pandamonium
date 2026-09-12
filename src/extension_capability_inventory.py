"""Revision-bound capability inventory and static scan contracts (MAD-911).

The capability inventory is advisory, revision-bound metadata that stays
readable while an extension is disabled. It is never execution authority: only
the registry's enabled ``effective_capabilities`` can mount or dispatch tools.
The scan contract describes a bounded, static repository assessment that never
executes repository-provided build, install, or lifecycle commands.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

from src.extension_registry import (
    DESCRIPTOR_TYPES,
    EXTENSION_ID_PATTERN,
    IMMUTABLE_REVISION_PATTERN,
    PERMISSION_MODES,
    SKILL_ID_PATTERN,
    TOOL_NAME_PATTERN,
    ExtensionContractError,
    normalize_tool_schema,
    validate_extension_manifest,
)

INVENTORY_VERSION = "jos-extension-capability-inventory.v1"
SCAN_VERSION = "jos-extension-scan.v1"

DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
CAPABILITY_KINDS = frozenset({"tool", "skill", "endpoint"})
SCAN_STAGES = ("fetch", "classify", "extract", "audit", "report")
SCAN_STAGE_PROGRESS = {"fetch": 10, "classify": 30, "extract": 55, "audit": 80, "report": 100}
REPO_CLASSES = frozenset(
    {"skill_bundle", "mcp_server", "python_cli", "node_cli", "web_app", "openapi", "unknown"}
)
FINDING_SEVERITIES = frozenset({"info", "low", "medium", "high", "critical"})
FINDING_CATEGORIES = frozenset(
    {
        "secret",
        "dangerous_pattern",
        "postinstall",
        "license",
        "dependency",
        "oversized_blob",
        "network_egress",
        "privilege",
    }
)

MAX_CAPABILITIES = 256
MAX_DEPENDENCIES = 256
MAX_FINDINGS = 256
MAX_LICENSES = 32
MAX_EVIDENCE_CHARS = 200
MAX_SCAN_FILES = 50_000
MAX_SCAN_BYTES = 512 * 1024 * 1024
MAX_SCAN_DURATION_MS = 10 * 60 * 1000
MAX_MOUNT_CAPABILITIES = 32

INVENTORY_FIELDS = frozenset(
    {
        "inventory_version",
        "extension_id",
        "extension_version",
        "source_revision",
        "manifest_digest",
        "descriptor",
        "capabilities",
        "inventory_digest",
    }
)
CAPABILITY_FIELDS = frozenset(
    {"name", "kind", "permission_mode", "descriptor", "schema", "skill"}
)
SKILL_FIELDS = frozenset({"source_path", "owner_scope", "platforms", "requires_toolsets"})
SCAN_FIELDS = frozenset(
    {
        "scan_version",
        "source_url",
        "source_revision",
        "stage",
        "repo_class",
        "capabilities",
        "dependencies",
        "licenses",
        "findings",
        "draft_manifest",
        "bounds",
        "executed_repo_commands",
        "artifact_digest",
    }
)
SCAN_CAPABILITY_FIELDS = frozenset(
    {"name", "kind", "descriptor", "permission_mode", "evidence_path"}
)
DEPENDENCY_FIELDS = frozenset({"ecosystem", "name", "version"})
FINDING_FIELDS = frozenset({"id", "severity", "category", "title", "evidence"})
BOUNDS_FIELDS = frozenset({"files_scanned", "bytes_scanned", "duration_ms"})

FINDING_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
ECOSYSTEM_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


def _object(value: Any, code: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ExtensionContractError(code)
    return dict(value)


def _strict(value: Mapping[str, Any], allowed: frozenset[str], code: str) -> None:
    if set(value) - set(allowed):
        raise ExtensionContractError(code)


def _bounded_text(value: Any, code: str, *, maximum: int = 200) -> str:
    text = str(value or "").strip()
    if not text or len(text) > maximum or any(ord(char) < 32 for char in text):
        raise ExtensionContractError(code)
    return text


def _relative_path(value: Any, code: str) -> str:
    text = _bounded_text(value, code, maximum=500)
    path = Path(text)
    if path.is_absolute() or ".." in path.parts:
        raise ExtensionContractError(code)
    return path.as_posix()


def _digest_text(value: Any, code: str) -> str:
    text = _bounded_text(value, code, maximum=80)
    if not DIGEST_PATTERN.fullmatch(text):
        raise ExtensionContractError(code)
    return text


def _string_list(
    value: Any, code: str, *, maximum: int = 64, item_maximum: int = 200
) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ExtensionContractError(code)
    return [_bounded_text(item, code, maximum=item_maximum) for item in value]


def canonical_json(value: Any) -> str:
    """Deterministic JSON used for every contract digest."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def content_digest(value: Mapping[str, Any], *, excluded: str) -> str:
    """Digest of a mapping with one self-referential field removed."""
    payload = {key: item for key, item in value.items() if key != excluded}
    return "sha256:" + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def manifest_digest(manifest: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest()


def inventory_digest(inventory: Mapping[str, Any]) -> str:
    return content_digest(inventory, excluded="inventory_digest")


def scan_artifact_digest(artifact: Mapping[str, Any]) -> str:
    return content_digest(artifact, excluded="artifact_digest")


# ---------------------------------------------------------------------------
# Capability inventory
# ---------------------------------------------------------------------------


def build_capability_inventory(reconciled: Any, *, source_revision: str) -> dict[str, Any]:
    """Build a revision-bound advisory inventory from reconciled adapter output.

    ``reconciled`` is the output of ``reconcile_extension_catalog`` for the
    installed revision. The result carries the exact validated schemas so a
    later lazy mount never needs to re-run an adapter, but the registry's
    enabled state remains the only execution authority.
    """
    value = _object(reconciled, "extension_inventory_source_invalid")
    manifest = _object(value.get("manifest"), "extension_inventory_source_invalid")

    extension_id = _bounded_text(manifest.get("extension_id"), "extension_id_invalid", maximum=64)
    if not EXTENSION_ID_PATTERN.fullmatch(extension_id):
        raise ExtensionContractError("extension_id_invalid")

    revision = _bounded_text(source_revision, "extension_source_revision_invalid", maximum=64)
    if not IMMUTABLE_REVISION_PATTERN.fullmatch(revision):
        raise ExtensionContractError("extension_source_revision_invalid")

    capabilities_block = _object(manifest.get("capabilities"), "extension_inventory_source_invalid")
    descriptor = _object(capabilities_block.get("descriptor"), "extension_inventory_source_invalid")
    descriptor_type = str(descriptor.get("type") or "")
    if descriptor_type not in DESCRIPTOR_TYPES:
        raise ExtensionContractError("extension_descriptor_type_invalid")

    capabilities: list[dict[str, Any]] = []
    raw_tools = value.get("capabilities", [])
    raw_skills = value.get("admitted_skills", [])
    if not isinstance(raw_tools, list) or not isinstance(raw_skills, list):
        raise ExtensionContractError("extension_inventory_source_invalid")
    for raw in raw_tools:
        item = _object(raw, "extension_inventory_capability_invalid")
        schema = normalize_tool_schema(item.get("schema"))
        mode = item.get("permission_mode")
        if mode not in PERMISSION_MODES:
            raise ExtensionContractError("extension_inventory_permission_invalid")
        capabilities.append(
            {
                "name": schema["function"]["name"],
                "kind": "tool",
                "permission_mode": mode,
                "descriptor": descriptor_type,
                "schema": schema,
            }
        )

    for raw in raw_skills:
        item = _object(raw, "extension_inventory_skill_invalid")
        skill_id = _bounded_text(item.get("id"), "extension_skill_id_invalid", maximum=64)
        mode = item.get("permission_mode")
        if not SKILL_ID_PATTERN.fullmatch(skill_id) or mode not in PERMISSION_MODES:
            raise ExtensionContractError("extension_inventory_skill_invalid")
        capabilities.append(
            {
                "name": skill_id,
                "kind": "skill",
                "permission_mode": mode,
                "descriptor": "skill_bundle",
                "skill": {
                    "source_path": _relative_path(
                        item.get("source_path"), "extension_skill_source_path_invalid"
                    ),
                    "owner_scope": _bounded_text(
                        item.get("owner_scope"), "extension_skill_owner_scope_invalid", maximum=200
                    ),
                    "platforms": _string_list(
                        item.get("platforms"), "extension_skill_platforms_invalid", maximum=32
                    ),
                    "requires_toolsets": _string_list(
                        item.get("requires_toolsets"), "extension_skill_toolsets_invalid", maximum=32
                    ),
                },
            }
        )

    names = [item["name"] for item in capabilities]
    if len(names) != len(set(names)):
        raise ExtensionContractError("extension_inventory_capability_duplicate")

    envelope = {
        "inventory_version": INVENTORY_VERSION,
        "extension_id": extension_id,
        "extension_version": _bounded_text(
            manifest.get("version"), "extension_version_invalid", maximum=80
        ),
        "source_revision": revision,
        "manifest_digest": manifest_digest(manifest),
        "descriptor": descriptor_type,
        "capabilities": sorted(capabilities, key=lambda item: item["name"]),
    }
    envelope["inventory_digest"] = inventory_digest(envelope)
    return envelope


def _validate_inventory_capability(raw: Any) -> dict[str, Any]:
    item = _object(raw, "extension_inventory_capability_invalid")
    _strict(item, CAPABILITY_FIELDS, "extension_inventory_capability_unknown_field")
    name = _bounded_text(item.get("name"), "extension_capability_name_invalid", maximum=128)
    if not TOOL_NAME_PATTERN.fullmatch(name):
        raise ExtensionContractError("extension_capability_name_invalid")
    kind = str(item.get("kind") or "")
    if kind not in CAPABILITY_KINDS:
        raise ExtensionContractError("extension_inventory_kind_invalid")
    mode = item.get("permission_mode")
    if mode not in PERMISSION_MODES:
        raise ExtensionContractError("extension_inventory_permission_invalid")
    descriptor = str(item.get("descriptor") or "")
    if descriptor not in DESCRIPTOR_TYPES:
        raise ExtensionContractError("extension_inventory_descriptor_invalid")

    normalized: dict[str, Any] = {
        "name": name,
        "kind": kind,
        "permission_mode": mode,
        "descriptor": descriptor,
    }
    if kind == "skill":
        if not SKILL_ID_PATTERN.fullmatch(name) or descriptor != "skill_bundle":
            raise ExtensionContractError("extension_inventory_skill_invalid")
        skill = _object(item.get("skill"), "extension_inventory_skill_invalid")
        _strict(skill, SKILL_FIELDS, "extension_inventory_skill_unknown_field")
        if SKILL_FIELDS - set(skill):
            raise ExtensionContractError("extension_inventory_skill_invalid")
        if "schema" in item:
            raise ExtensionContractError("extension_inventory_skill_invalid")
        normalized["skill"] = {
            "source_path": _relative_path(skill.get("source_path"), "extension_skill_source_path_invalid"),
            "owner_scope": _bounded_text(
                skill.get("owner_scope"), "extension_skill_owner_scope_invalid", maximum=200
            ),
            "platforms": _string_list(skill.get("platforms"), "extension_skill_platforms_invalid", maximum=32),
            "requires_toolsets": _string_list(
                skill.get("requires_toolsets"), "extension_skill_toolsets_invalid", maximum=32
            ),
        }
    elif kind == "tool":
        if "schema" not in item or "skill" in item:
            raise ExtensionContractError("extension_inventory_tool_invalid")
        normalized["schema"] = normalize_tool_schema(item.get("schema"))
        if normalized["schema"]["function"]["name"] != name:
            raise ExtensionContractError("extension_capability_name_mismatch")
    else:
        if "schema" in item or "skill" in item:
            raise ExtensionContractError("extension_inventory_endpoint_invalid")
    return normalized


def validate_capability_inventory(value: Any) -> dict[str, Any]:
    """Return a normalized strict inventory or fail closed."""
    inventory = _object(value, "extension_inventory_invalid")
    _strict(inventory, INVENTORY_FIELDS, "extension_inventory_unknown_field")
    if INVENTORY_FIELDS - set(inventory):
        raise ExtensionContractError("extension_inventory_required_field_missing")
    if inventory.get("inventory_version") != INVENTORY_VERSION:
        raise ExtensionContractError("extension_inventory_version_unsupported")

    extension_id = _bounded_text(inventory.get("extension_id"), "extension_id_invalid", maximum=64)
    if not EXTENSION_ID_PATTERN.fullmatch(extension_id):
        raise ExtensionContractError("extension_id_invalid")
    revision = _bounded_text(
        inventory.get("source_revision"), "extension_source_revision_invalid", maximum=64
    )
    if not IMMUTABLE_REVISION_PATTERN.fullmatch(revision):
        raise ExtensionContractError("extension_source_revision_invalid")
    descriptor = str(inventory.get("descriptor") or "")
    if descriptor not in DESCRIPTOR_TYPES:
        raise ExtensionContractError("extension_inventory_descriptor_invalid")

    raw_capabilities = inventory.get("capabilities")
    if not isinstance(raw_capabilities, list) or len(raw_capabilities) > MAX_CAPABILITIES:
        raise ExtensionContractError("extension_inventory_capabilities_invalid")
    capabilities = [_validate_inventory_capability(raw) for raw in raw_capabilities]
    names = [item["name"] for item in capabilities]
    if len(names) != len(set(names)):
        raise ExtensionContractError("extension_inventory_capability_duplicate")

    normalized = {
        "inventory_version": INVENTORY_VERSION,
        "extension_id": extension_id,
        "extension_version": _bounded_text(
            inventory.get("extension_version"), "extension_version_invalid", maximum=80
        ),
        "source_revision": revision,
        "manifest_digest": _digest_text(
            inventory.get("manifest_digest"), "extension_inventory_manifest_digest_invalid"
        ),
        "descriptor": descriptor,
        "capabilities": capabilities,
    }
    declared_digest = _digest_text(
        inventory.get("inventory_digest"), "extension_inventory_digest_invalid"
    )
    if declared_digest != inventory_digest(normalized):
        raise ExtensionContractError("extension_inventory_digest_mismatch")
    normalized["inventory_digest"] = declared_digest
    return normalized


def inventory_is_current(
    inventory: Any, *, source_revision: str, manifest_digest_value: str
) -> bool:
    """True only when a valid inventory matches the installed revision and manifest."""
    try:
        normalized = validate_capability_inventory(inventory)
    except ExtensionContractError:
        return False
    return (
        normalized["source_revision"] == source_revision
        and normalized["manifest_digest"] == manifest_digest_value
    )


def advisory_capability_items(inventory: Any) -> list[dict[str, Any]]:
    """Sanitized inventory projection: names and metadata only, never schemas."""
    normalized = validate_capability_inventory(inventory)
    return [
        {
            "name": item["name"],
            "kind": item["kind"],
            "permission_mode": item["permission_mode"],
            "descriptor": item["descriptor"],
        }
        for item in normalized["capabilities"]
    ]


def resolve_mount_schemas(inventory: Any, names: Iterable[str]) -> list[dict[str, Any]]:
    """Return validated schemas for the named tool capabilities.

    Unknown names, non-tool capabilities, or an oversized/duplicate request fail
    closed. Returning a schema grants no authority by itself; the caller still
    requires an enabled registry record and the existing executor allowlist.
    """
    normalized = validate_capability_inventory(inventory)
    requested = [str(name).strip() for name in names]
    if (
        not requested
        or len(requested) > MAX_MOUNT_CAPABILITIES
        or len(requested) != len(set(requested))
    ):
        raise ExtensionContractError("extension_capability_mount_invalid")
    by_name = {item["name"]: item for item in normalized["capabilities"]}
    schemas: list[dict[str, Any]] = []
    for name in requested:
        item = by_name.get(name)
        if item is None:
            raise ExtensionContractError("extension_capability_unknown")
        if item["kind"] != "tool" or "schema" not in item:
            raise ExtensionContractError("extension_capability_not_mountable")
        schemas.append(item["schema"])
    return schemas


# ---------------------------------------------------------------------------
# Static scan contract
# ---------------------------------------------------------------------------


def scan_stage_progress(stage: str) -> int:
    """Percent for one scan stage; unknown stages fail closed."""
    if stage not in SCAN_STAGE_PROGRESS:
        raise ExtensionContractError("extension_scan_stage_invalid")
    return SCAN_STAGE_PROGRESS[stage]


def redact_scan_evidence(text: Any, *, maximum: int = MAX_EVIDENCE_CHARS) -> str:
    """Redact secret-like evidence before it can reach an artifact or UI."""
    value = str(text or "")
    value = re.sub(
        r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]+", r"\1 <redacted>", value
    )
    value = re.sub(
        r"(?i)\b(token|password|passwd|secret|api[_-]?key|authorization)\b\s*[:=]\s*[^\s,;'\"]+",
        r"\1=<redacted>",
        value,
    )
    value = re.sub(r"\b(?:sk|pk|ghp|gho|ghs|xox[baprs])[-_][A-Za-z0-9_-]{8,}\b", "<redacted>", value)
    value = re.sub(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*", "<redacted-private-key>", value, flags=re.S
    )
    value = value.replace("\x00", "").strip()
    if len(value) > maximum:
        value = value[:maximum] + "..."
    return value


def _validate_scan_capability(raw: Any) -> dict[str, Any]:
    item = _object(raw, "extension_scan_capability_invalid")
    _strict(item, SCAN_CAPABILITY_FIELDS, "extension_scan_capability_unknown_field")
    name = _bounded_text(item.get("name"), "extension_capability_name_invalid", maximum=128)
    if not TOOL_NAME_PATTERN.fullmatch(name):
        raise ExtensionContractError("extension_capability_name_invalid")
    kind = str(item.get("kind") or "")
    if kind not in CAPABILITY_KINDS:
        raise ExtensionContractError("extension_scan_kind_invalid")
    descriptor = str(item.get("descriptor") or "")
    if descriptor not in DESCRIPTOR_TYPES:
        raise ExtensionContractError("extension_scan_descriptor_invalid")
    normalized: dict[str, Any] = {
        "name": name,
        "kind": kind,
        "descriptor": descriptor,
        "evidence_path": _relative_path(item.get("evidence_path"), "extension_scan_evidence_path_invalid"),
    }
    if "permission_mode" in item:
        if item["permission_mode"] not in PERMISSION_MODES:
            raise ExtensionContractError("extension_scan_permission_invalid")
        normalized["permission_mode"] = item["permission_mode"]
    return normalized


def _validate_scan_finding(raw: Any) -> dict[str, Any]:
    item = _object(raw, "extension_scan_finding_invalid")
    _strict(item, FINDING_FIELDS, "extension_scan_finding_unknown_field")
    finding_id = _bounded_text(item.get("id"), "extension_scan_finding_id_invalid", maximum=64)
    if not FINDING_ID_PATTERN.fullmatch(finding_id):
        raise ExtensionContractError("extension_scan_finding_id_invalid")
    severity = str(item.get("severity") or "")
    if severity not in FINDING_SEVERITIES:
        raise ExtensionContractError("extension_scan_finding_severity_invalid")
    category = str(item.get("category") or "")
    if category not in FINDING_CATEGORIES:
        raise ExtensionContractError("extension_scan_finding_category_invalid")
    normalized = {
        "id": finding_id,
        "severity": severity,
        "category": category,
        "title": _bounded_text(item.get("title"), "extension_scan_finding_title_invalid", maximum=200),
    }
    if "evidence" in item:
        evidence = str(item.get("evidence") or "")
        if not evidence or redact_scan_evidence(evidence) != evidence:
            raise ExtensionContractError("extension_scan_evidence_not_redacted")
        normalized["evidence"] = evidence
    return normalized


def _validate_scan_bounds(raw: Any) -> dict[str, int]:
    bounds = _object(raw, "extension_scan_bounds_invalid")
    _strict(bounds, BOUNDS_FIELDS, "extension_scan_bounds_unknown_field")
    if BOUNDS_FIELDS - set(bounds):
        raise ExtensionContractError("extension_scan_bounds_invalid")
    limits = {
        "files_scanned": MAX_SCAN_FILES,
        "bytes_scanned": MAX_SCAN_BYTES,
        "duration_ms": MAX_SCAN_DURATION_MS,
    }
    normalized: dict[str, int] = {}
    for name, limit in limits.items():
        value = bounds.get(name)
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= limit:
            raise ExtensionContractError("extension_scan_bounds_exceeded")
        normalized[name] = value
    return normalized


def validate_scan_artifact(value: Any, *, require_complete: bool = False) -> dict[str, Any]:
    """Return a normalized strict scan artifact or fail closed.

    ``executed_repo_commands`` must be empty: static scan never executes
    repository-provided build, install, or lifecycle commands. The pinned Git
    fetch is intake, not a repository command, and is not represented here.
    """
    artifact = _object(value, "extension_scan_invalid")
    _strict(artifact, SCAN_FIELDS, "extension_scan_unknown_field")
    if SCAN_FIELDS - set(artifact):
        raise ExtensionContractError("extension_scan_required_field_missing")
    if artifact.get("scan_version") != SCAN_VERSION:
        raise ExtensionContractError("extension_scan_version_unsupported")

    declared_digest = _digest_text(
        artifact.get("artifact_digest"), "extension_scan_artifact_digest_invalid"
    )
    if declared_digest != scan_artifact_digest(artifact):
        raise ExtensionContractError("extension_scan_artifact_digest_mismatch")

    stage = str(artifact.get("stage") or "")
    if stage not in SCAN_STAGES:
        raise ExtensionContractError("extension_scan_stage_invalid")
    if require_complete and stage != "report":
        raise ExtensionContractError("extension_scan_not_complete")

    source_url = _bounded_text(artifact.get("source_url"), "extension_scan_source_url_invalid", maximum=2_000)
    parsed = urlparse(source_url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ExtensionContractError("extension_scan_source_url_invalid")

    revision = _bounded_text(
        artifact.get("source_revision"), "extension_scan_revision_invalid", maximum=64
    )
    if not IMMUTABLE_REVISION_PATTERN.fullmatch(revision):
        raise ExtensionContractError("extension_scan_revision_invalid")

    repo_class = str(artifact.get("repo_class") or "")
    if repo_class not in REPO_CLASSES:
        raise ExtensionContractError("extension_scan_repo_class_invalid")

    raw_capabilities = artifact.get("capabilities")
    if not isinstance(raw_capabilities, list) or len(raw_capabilities) > MAX_CAPABILITIES:
        raise ExtensionContractError("extension_scan_capabilities_invalid")
    capabilities = [_validate_scan_capability(raw) for raw in raw_capabilities]
    names = [item["name"] for item in capabilities]
    if len(names) != len(set(names)):
        raise ExtensionContractError("extension_scan_capability_duplicate")

    raw_dependencies = artifact.get("dependencies")
    if not isinstance(raw_dependencies, list) or len(raw_dependencies) > MAX_DEPENDENCIES:
        raise ExtensionContractError("extension_scan_dependencies_invalid")
    dependencies = []
    for raw in raw_dependencies:
        item = _object(raw, "extension_scan_dependency_invalid")
        _strict(item, DEPENDENCY_FIELDS, "extension_scan_dependency_unknown_field")
        ecosystem = _bounded_text(item.get("ecosystem"), "extension_scan_ecosystem_invalid", maximum=32)
        if not ECOSYSTEM_PATTERN.fullmatch(ecosystem):
            raise ExtensionContractError("extension_scan_ecosystem_invalid")
        dependency = {
            "ecosystem": ecosystem,
            "name": _bounded_text(item.get("name"), "extension_scan_dependency_name_invalid"),
        }
        if "version" in item:
            dependency["version"] = _bounded_text(
                item.get("version"), "extension_scan_dependency_version_invalid", maximum=80
            )
        dependencies.append(dependency)

    licenses = _string_list(artifact.get("licenses"), "extension_scan_licenses_invalid", maximum=MAX_LICENSES)

    raw_findings = artifact.get("findings")
    if not isinstance(raw_findings, list) or len(raw_findings) > MAX_FINDINGS:
        raise ExtensionContractError("extension_scan_findings_invalid")
    findings = [_validate_scan_finding(raw) for raw in raw_findings]

    draft_manifest = artifact.get("draft_manifest")
    if draft_manifest is not None:
        draft_manifest = validate_extension_manifest(draft_manifest)

    executed = artifact.get("executed_repo_commands")
    if not isinstance(executed, list) or executed:
        raise ExtensionContractError("extension_scan_execution_forbidden")

    normalized: dict[str, Any] = {
        "scan_version": SCAN_VERSION,
        "source_url": source_url,
        "source_revision": revision,
        "stage": stage,
        "repo_class": repo_class,
        "capabilities": capabilities,
        "dependencies": dependencies,
        "licenses": licenses,
        "findings": findings,
        "draft_manifest": draft_manifest,
        "bounds": _validate_scan_bounds(artifact.get("bounds")),
        "executed_repo_commands": [],
    }
    normalized["artifact_digest"] = declared_digest
    return normalized
