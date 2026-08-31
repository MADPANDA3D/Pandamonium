import json
from pathlib import Path

import pytest

from src.extension_registry import (
    ExtensionContractError,
    ExtensionRegistry,
    MANIFEST_VERSION,
    reconcile_extension_catalog,
    validate_extension_manifest,
)


FIXTURES = Path(__file__).parent / "fixtures" / "extensions"


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


def _catalog(manifest: dict, tools: list[dict]) -> dict:
    return {
        "protocol_version": MANIFEST_VERSION,
        "extension_id": manifest["extension_id"],
        "version": manifest["version"],
        "source_revision": manifest["source"]["revision"],
        "tools": tools,
    }


def test_one_schema_validates_oracle_and_differently_named_fixture():
    schema = json.loads(
        (Path(__file__).parents[1] / "specs" / "schemas" / "jos-extension-v1.schema.json").read_text()
    )
    oracle = validate_extension_manifest(_manifest("oracle"))
    atlas = validate_extension_manifest(_manifest("atlas"))

    assert schema["properties"]["protocol_version"]["const"] == MANIFEST_VERSION
    assert schema["additionalProperties"] is False
    assert {oracle["extension_id"], atlas["extension_id"]} == {"oracle", "atlas"}
    assert set(oracle) == set(atlas)
    assert not any("oracle" in field.lower() for field in schema["properties"])


def test_self_revision_binds_to_observed_immutable_revision():
    manifest = _manifest("atlas")
    revision = "1" * 40
    catalog = _catalog(manifest, [_tool("create_mesh")])
    catalog["source_revision"] = revision

    reconciled = reconcile_extension_catalog(
        manifest, catalog, source_revision=revision, health_available=True
    )

    assert reconciled["manifest"]["source"]["revision"] == revision


def test_standard_and_live_descriptors_are_references_not_copied_schemas():
    oracle = validate_extension_manifest(_manifest("oracle"))
    atlas = validate_extension_manifest(_manifest("atlas"))

    assert oracle["capabilities"] == {
        "descriptor": {"type": "live_catalog", "endpoint": "/api/oracle/capabilities"}
    }
    assert atlas["capabilities"] == {
        "descriptor": {"type": "openapi", "endpoint": "/openapi.json"}
    }

    mcp = _manifest("atlas")
    mcp["runtime"]["type"] = "mcp"
    mcp["capabilities"]["descriptor"] = {"type": "mcp", "reference": "configured-server-id"}
    assert validate_extension_manifest(mcp)["capabilities"]["descriptor"]["type"] == "mcp"


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (lambda manifest: manifest.update({"privileged": True}), "extension_manifest_unknown_field"),
        (lambda manifest: manifest["permissions"].update({"bypass": True}), "extension_permissions_unknown_field"),
        (lambda manifest: manifest["runtime"].update({"shell": "sh"}), "extension_runtime_unknown_field"),
    ],
)
def test_security_relevant_unknown_fields_fail_closed(mutation, code):
    manifest = _manifest("atlas")
    mutation(manifest)
    with pytest.raises(ExtensionContractError, match=code):
        validate_extension_manifest(manifest)


def test_reconciliation_fails_for_malformed_duplicates_health_and_revision():
    manifest = _manifest("oracle")
    revision = manifest["source"]["revision"]

    with pytest.raises(ExtensionContractError, match="extension_capability_name_duplicate"):
        reconcile_extension_catalog(
            manifest, _catalog(manifest, [_tool("inspect_globe"), _tool("inspect_globe")]),
            source_revision=revision, health_available=True,
        )
    malformed = _tool("inspect_globe")
    malformed["parameters"] = {"type": "array"}
    with pytest.raises(ExtensionContractError, match="extension_capability_parameters_invalid"):
        reconcile_extension_catalog(
            manifest, _catalog(manifest, [malformed]), source_revision=revision, health_available=True,
        )
    with pytest.raises(ExtensionContractError, match="extension_health_unavailable"):
        reconcile_extension_catalog(
            manifest, _catalog(manifest, [_tool("inspect_globe")]),
            source_revision=revision, health_available=False,
        )
    with pytest.raises(ExtensionContractError, match="extension_source_revision_mismatch"):
        reconcile_extension_catalog(
            manifest, _catalog(manifest, [_tool("inspect_globe")]),
            source_revision="0" * 40, health_available=True,
        )


def test_registry_stores_metadata_only_and_disable_removes_tools_and_context(tmp_path):
    registry = ExtensionRegistry(tmp_path / "extensions.json")
    oracle = _manifest("oracle")
    atlas = _manifest("atlas")
    registry.register(
        oracle,
        _catalog(oracle, [_tool("inspect_globe")]),
        source_revision=oracle["source"]["revision"],
        health_available=True,
    )
    registry.register(
        atlas,
        {**_catalog(atlas, [_tool("create_mesh")]), "source_revision": "1" * 40},
        source_revision="1" * 40,
        health_available=True,
    )

    effective = registry.effective_capabilities({"oracle", "atlas"})
    assert set(effective) == {"inspect_globe", "create_mesh"}
    assert effective["inspect_globe"]["extension_id"] == "oracle"
    assert effective["create_mesh"]["permission_mode"] == "bounded_write"
    assert set(registry.context_extensions({"oracle", "atlas"})) == {"oracle", "atlas"}
    assert not hasattr(registry, "execute")

    assert registry.disable("oracle") is True
    assert set(registry.effective_capabilities({"oracle", "atlas"})) == {"create_mesh"}
    assert set(registry.context_extensions({"oracle", "atlas"})) == {"atlas"}
    assert registry.snapshot()["extensions"]["oracle"]["effective_capabilities"] == []
    assert registry.unregister("oracle") is True
    assert registry.unregister("oracle") is False


def test_cross_extension_duplicate_capability_fails_before_registry_write(tmp_path):
    registry = ExtensionRegistry(tmp_path / "extensions.json")
    oracle = _manifest("oracle")
    atlas = _manifest("atlas")
    oracle["permissions"]["capabilities"] = {}
    atlas["permissions"]["capabilities"] = {}
    registry.register(
        oracle, _catalog(oracle, [_tool("shared_tool")]),
        source_revision=oracle["source"]["revision"], health_available=True,
    )

    with pytest.raises(ExtensionContractError, match="extension_registry_capability_conflict"):
        registry.register(
            atlas, {**_catalog(atlas, [_tool("shared_tool")]), "source_revision": "1" * 40},
            source_revision="1" * 40, health_available=True,
        )

    assert set(registry.snapshot()["extensions"]) == {"oracle"}


def test_registry_fails_closed_when_persisted_metadata_is_tampered(tmp_path):
    path = tmp_path / "extensions.json"
    registry = ExtensionRegistry(path)
    oracle = _manifest("oracle")
    registry.register(
        oracle, _catalog(oracle, [_tool("inspect_globe")]),
        source_revision=oracle["source"]["revision"], health_available=True,
    )
    state = json.loads(path.read_text(encoding="utf-8"))
    state["extensions"]["oracle"]["effective_capabilities"][0]["permission_mode"] = "root"
    path.write_text(json.dumps(state), encoding="utf-8")

    assert registry.effective_capabilities() == {}
    assert registry.context_extensions({"oracle"}) == {}
