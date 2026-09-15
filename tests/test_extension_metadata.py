"""Package copy survives intake and is shared without eagerly mounting schemas."""

import copy
import json
from pathlib import Path

import pytest
from jsonschema import validate

from scripts.validate_cli_package import reviewed_reply
from src.extension_agent_mount import extension_catalog_rows, inspect_extension
from src.extension_metadata import package_metadata
from src.extension_plugin_view import (
    installed_plugin_detail,
    installed_plugin_rows,
    plugin_readiness,
)
from src.extension_registry import (
    ExtensionContractError,
    ExtensionRegistry,
    validate_extension_manifest,
)
from src.extension_scan import ExtensionStaticScanner
from src.marketplace_catalog import marketplace_catalog_view
from tests.test_extension_scan import SOURCE_URL, _CopyGitClient
from tests.test_extension_semantic_intake import proposal, source_tree
from tests.test_marketplace_catalog import _fixture_catalog, _resign


def test_shared_metadata_from_scan_through_registry_catalog_and_agent(tmp_path):
    source = source_tree(tmp_path)
    data = proposal()
    data.update(categories=["utilities"], icon="◈", examples=["Double the integer 4."])
    artifact = ExtensionStaticScanner(
        git_client=_CopyGitClient(source),
        staging_root=tmp_path / "staging",
        data_dir=tmp_path / "scans",
        model=lambda messages, check: reviewed_reply(data, messages),
    ).run(SOURCE_URL, "HEAD", operator_id="operator")
    manifest = artifact["draft_manifest"]
    assert artifact["plugin"]["summary"] == data["purpose"]
    assert manifest["metadata"]["examples"] == data["examples"]
    validate(
        manifest,
        json.loads(Path("specs/schemas/jos-extension-v1.schema.json").read_text()),
    )
    registry = ExtensionRegistry(tmp_path / "extensions.json")
    registry.register(
        manifest,
        None,
        source_revision=manifest["source"]["revision"],
        health_available=True,
    )
    detail = installed_plugin_detail(registry, manifest["extension_id"])
    catalog, trusted, key = _fixture_catalog()
    catalog["entries"][0]["manifest"] = manifest
    catalog["entries"][0]["configuration"] = manifest.get("configuration", [])
    _resign(catalog, key)
    catalog_row = marketplace_catalog_view(
        catalog,
        trusted_keys=trusted,
        registry_snapshot=registry.snapshot(),
        pandamonium_version="1.0.67",
        platform="linux",
        architecture="amd64",
    )["plugins"][0]
    for payload in [
        artifact["plugin"],
        detail,
        installed_plugin_rows(registry)[0],
        extension_catalog_rows(registry)[0],
        inspect_extension(registry, manifest["extension_id"]),
        catalog_row,
    ]:
        for field in [
            "summary",
            "icon",
            "categories",
            "examples",
            "requirements",
            "capability_summaries",
        ]:
            assert payload[field] == artifact["plugin"][field]
        assert "parameters" not in json.dumps(payload)
    assert detail["readiness"]["state"] == "needs_setup"  # No actual operation receipt.
    assert package_metadata(manifest)["configuration"] == []


@pytest.mark.parametrize(
    "patch",
    [
        {"readiness": "ready"},
        {"categories": ["bad category"]},
        {"icon": "\n"},
        {"skill_descriptions": {"invented": "Not a declared skill"}},
        {"examples": ["x" * 1001]},
    ],
)
def test_metadata_is_bounded_and_cannot_declare_readiness(patch):
    manifest = copy.deepcopy(proposal()["manifest"])
    manifest["metadata"] = {"summary": "Double integers.", **patch}
    with pytest.raises(ExtensionContractError):
        validate_extension_manifest(manifest)


def test_readiness_distinguishes_work_failure_and_reconfiguration(tmp_path):
    record = {"manifest": proposal()["manifest"], "enabled": False}

    def check(status):
        (tmp_path / "lifecycle.json").write_text(
            json.dumps(
                {
                    "plans": {
                        "p": {
                            "extension_id": "demo-tools",
                            "operator_id": "operator",
                            "status": status,
                            "created_at": "2026-09-15",
                        }
                    }
                }
            )
        )
        return plugin_readiness(record, owner="operator", root=tmp_path)["state"]

    assert check("executing") == "preparing"
    assert check("failed") == "failed"
    assert check("configuration_changed") == "needs_setup"
    assert check("completed") == "disabled"


def test_skill_readiness_requires_every_declared_skill_for_requesting_owner():
    record = {
        "manifest": {
            "extension_id": "bundle",
            "runtime": {"type": "skills"},
            "capabilities": {"descriptor": {"include": ["first", "second"]}},
        },
        "enabled": True,
        "admitted_skills": [
            {"id": name, "owner_scope": "alice"} for name in ["first", "second"]
        ],
    }

    def check(owner):
        return plugin_readiness(record, owner=owner, lifecycle={})["state"]

    assert check("alice") == "ready"
    assert check("bob") == "needs_setup"
    assert check(None) == "needs_setup"
    record["admitted_skills"].pop()
    assert check("alice") == "needs_setup"
