"""Contract tests for the MAD-911 capability inventory, lazy mount, and scan."""

import json
from pathlib import Path

import pytest

from src.extension_capability_inventory import (
    INVENTORY_VERSION,
    SCAN_STAGES,
    SCAN_VERSION,
    advisory_capability_items,
    build_capability_inventory,
    inventory_digest,
    inventory_is_current,
    manifest_digest,
    redact_scan_evidence,
    resolve_mount_schemas,
    scan_artifact_digest,
    scan_stage_progress,
    validate_capability_inventory,
    validate_scan_artifact,
)
from src.extension_registry import (
    MANIFEST_VERSION,
    ExtensionContractError,
    reconcile_extension_catalog,
    validate_extension_manifest,
)


FIXTURES = Path(__file__).parent / "fixtures" / "extensions"
SCHEMAS = Path(__file__).parents[1] / "specs" / "schemas"


def _manifest(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.manifest.json").read_text(encoding="utf-8"))


def _tool(name: str) -> dict:
    return {
        "name": name,
        "description": f"Run {name}",
        "parameters": {
            "type": "object",
            "properties": {"target": {"type": "string"}},
            "required": ["target"],
            "additionalProperties": False,
        },
    }


def _reconciled_with_tool(manifest_name: str, tool_name: str, revision: str) -> tuple[dict, str]:
    manifest = _manifest(manifest_name)
    catalog = {
        "protocol_version": MANIFEST_VERSION,
        "extension_id": manifest["extension_id"],
        "version": manifest["version"],
        "source_revision": revision,
        "tools": [_tool(tool_name)],
    }
    reconciled = reconcile_extension_catalog(
        manifest, catalog, source_revision=revision, health_available=True
    )
    return reconciled, revision


def _scan_artifact(**overrides) -> dict:
    artifact = {
        "scan_version": SCAN_VERSION,
        "source_url": "https://github.com/MADPANDA3D/example-tools.git",
        "source_revision": "a" * 40,
        "stage": "report",
        "repo_class": "python_cli",
        "capabilities": [
            {
                "name": "run_job",
                "kind": "tool",
                "descriptor": "inline",
                "evidence_path": "pyproject.toml",
            }
        ],
        "dependencies": [{"ecosystem": "pypi", "name": "httpx", "version": "0.27.0"}],
        "licenses": ["MIT"],
        "findings": [
            {
                "id": "postinstall-script",
                "severity": "medium",
                "category": "postinstall",
                "title": "postinstall script detected",
                "evidence": "scripts.postinstall = node install.js",
            }
        ],
        "draft_manifest": None,
        "bounds": {"files_scanned": 120, "bytes_scanned": 4096, "duration_ms": 800},
        "executed_repo_commands": [],
    }
    artifact.update(overrides)
    artifact["artifact_digest"] = scan_artifact_digest(artifact)
    return artifact


def test_contract_schemas_are_strict_and_versioned():
    inventory_schema = json.loads(
        (SCHEMAS / "extension-capability-inventory-v1.schema.json").read_text(encoding="utf-8")
    )
    scan_schema = json.loads(
        (SCHEMAS / "extension-scan-v1.schema.json").read_text(encoding="utf-8")
    )

    assert inventory_schema["additionalProperties"] is False
    assert inventory_schema["properties"]["inventory_version"]["const"] == INVENTORY_VERSION
    assert inventory_schema["$defs"]["capability"]["additionalProperties"] is False
    assert scan_schema["additionalProperties"] is False
    assert scan_schema["properties"]["scan_version"]["const"] == SCAN_VERSION
    assert scan_schema["properties"]["executed_repo_commands"] == {"type": "array", "maxItems": 0}
    assert scan_schema["$defs"]["finding"]["additionalProperties"] is False
    assert SCAN_STAGES == ("fetch", "classify", "extract", "audit", "report")


def test_inventory_is_revision_bound_and_advisory_items_hide_schemas():
    manifest = _manifest("oracle")
    reconciled, revision = _reconciled_with_tool(
        "oracle", "inspect_globe", manifest["source"]["revision"]
    )

    inventory = build_capability_inventory(reconciled, source_revision=revision)

    assert inventory["inventory_version"] == INVENTORY_VERSION
    assert inventory["source_revision"] == revision
    assert inventory["manifest_digest"] == manifest_digest(reconciled["manifest"])
    assert [item["name"] for item in inventory["capabilities"]] == ["inspect_globe"]
    assert inventory["inventory_digest"] == inventory_digest(inventory)
    assert validate_capability_inventory(inventory) == inventory

    advisory = advisory_capability_items(inventory)
    assert advisory == [
        {
            "name": "inspect_globe",
            "kind": "tool",
            "permission_mode": "read_only",
            "descriptor": "live_catalog",
        }
    ]
    assert all("schema" not in item for item in advisory)


def test_inventory_tamper_and_staleness_fail_closed():
    reconciled, revision = _reconciled_with_tool("atlas", "create_mesh", "2" * 40)
    inventory = build_capability_inventory(reconciled, source_revision=revision)
    digest = manifest_digest(reconciled["manifest"])

    tampered = json.loads(json.dumps(inventory))
    tampered["capabilities"][0]["permission_mode"] = "destructive"
    with pytest.raises(ExtensionContractError, match="extension_inventory_digest_mismatch"):
        validate_capability_inventory(tampered)

    assert inventory_is_current(inventory, source_revision=revision, manifest_digest_value=digest)
    assert not inventory_is_current(inventory, source_revision="3" * 40, manifest_digest_value=digest)
    assert not inventory_is_current(tampered, source_revision=revision, manifest_digest_value=digest)


def test_skill_bundle_inventory_and_mount_fail_closed():
    manifest = _manifest("atlas")
    manifest["runtime"] = {"type": "skills", "entrypoint": "skills"}
    manifest["capabilities"] = {
        "descriptor": {"type": "skill_bundle", "format": "agent_skill", "include": ["pdf-tools"]}
    }
    manifest["permissions"] = {"default": "read_only", "capabilities": {"pdf-tools": "read_only"}}
    revision = "4" * 40
    catalog = {
        "protocol_version": MANIFEST_VERSION,
        "extension_id": manifest["extension_id"],
        "version": manifest["version"],
        "source_revision": revision,
        "skills": [
            {
                "id": "pdf-tools",
                "source_path": "skills/pdf-tools/SKILL.md",
                "owner_scope": "extension:atlas",
                "platforms": ["linux"],
                "requires_toolsets": [],
            }
        ],
    }
    reconciled = reconcile_extension_catalog(
        manifest, catalog, source_revision=revision, health_available=True
    )
    inventory = build_capability_inventory(reconciled, source_revision=revision)

    skill = inventory["capabilities"][0]
    assert skill["kind"] == "skill"
    assert skill["descriptor"] == "skill_bundle"
    assert skill["skill"]["source_path"] == "skills/pdf-tools/SKILL.md"

    with pytest.raises(ExtensionContractError, match="extension_capability_not_mountable"):
        resolve_mount_schemas(inventory, ["pdf-tools"])
    with pytest.raises(ExtensionContractError, match="extension_capability_unknown"):
        resolve_mount_schemas(inventory, ["missing"])
    with pytest.raises(ExtensionContractError, match="extension_capability_mount_invalid"):
        resolve_mount_schemas(inventory, [])

    oracle = _manifest("oracle")
    tool_reconciled, tool_revision = _reconciled_with_tool(
        "oracle", "inspect_globe", oracle["source"]["revision"]
    )
    tool_inventory = build_capability_inventory(tool_reconciled, source_revision=tool_revision)
    schemas = resolve_mount_schemas(tool_inventory, ["inspect_globe"])
    assert schemas[0]["function"]["name"] == "inspect_globe"


def test_scan_stage_progress_is_monotonic_and_unknown_fails():
    values = [scan_stage_progress(stage) for stage in SCAN_STAGES]
    assert values == sorted(values)
    assert values[-1] == 100
    with pytest.raises(ExtensionContractError, match="extension_scan_stage_invalid"):
        scan_stage_progress("deploy")


def test_scan_artifact_validates_and_forbids_repository_execution():
    artifact = _scan_artifact()
    validated = validate_scan_artifact(artifact, require_complete=True)
    assert validated["artifact_digest"] == artifact["artifact_digest"]
    assert validated["executed_repo_commands"] == []

    executing = _scan_artifact(executed_repo_commands=["make install"])
    with pytest.raises(ExtensionContractError, match="extension_scan_execution_forbidden"):
        validate_scan_artifact(executing)

    incomplete = _scan_artifact(stage="audit")
    with pytest.raises(ExtensionContractError, match="extension_scan_not_complete"):
        validate_scan_artifact(incomplete, require_complete=True)

    tampered = _scan_artifact()
    tampered["licenses"] = ["GPL-3.0"]
    with pytest.raises(ExtensionContractError, match="extension_scan_artifact_digest_mismatch"):
        validate_scan_artifact(tampered)

    over_bounds = _scan_artifact(
        bounds={"files_scanned": 60000, "bytes_scanned": 4096, "duration_ms": 800}
    )
    with pytest.raises(ExtensionContractError, match="extension_scan_bounds_exceeded"):
        validate_scan_artifact(over_bounds)


def test_scan_draft_manifest_must_pass_existing_strict_validator():
    draft = validate_extension_manifest(_manifest("atlas"))
    validated = validate_scan_artifact(_scan_artifact(draft_manifest=draft), require_complete=True)
    assert validated["draft_manifest"]["extension_id"] == "atlas"

    broken = _manifest("atlas")
    broken["runtime"] = {"type": "shell", "entrypoint": "run.sh"}
    with pytest.raises(ExtensionContractError, match="extension_runtime_type_invalid"):
        validate_scan_artifact(_scan_artifact(draft_manifest=broken))


def test_scan_evidence_is_redacted_and_unredacted_evidence_fails_closed():
    raw = (
        "token=super-secret-value Authorization: Bearer abc.def.ghi "
        "password=hunter2 sk-1234567890abcdef"
    )
    redacted = redact_scan_evidence(raw)
    for secret in ("super-secret-value", "abc.def.ghi", "hunter2", "1234567890abcdef"):
        assert secret not in redacted
    assert "token=<redacted>" in redacted

    unredacted = _scan_artifact(
        findings=[
            {
                "id": "secret-scan",
                "severity": "critical",
                "category": "secret",
                "title": "possible secret",
                "evidence": raw,
            }
        ]
    )
    with pytest.raises(ExtensionContractError, match="extension_scan_evidence_not_redacted"):
        validate_scan_artifact(unredacted)

    safe = _scan_artifact(
        findings=[
            {
                "id": "secret-scan",
                "severity": "critical",
                "category": "secret",
                "title": "possible secret",
                "evidence": redacted,
            }
        ]
    )
    validated = validate_scan_artifact(safe, require_complete=True)
    assert "hunter2" not in json.dumps(validated)