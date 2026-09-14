"""MAD-916/MAD-945: scan -> approved install -> persisted inventory readback."""

import json
import shutil
from pathlib import Path

import pytest

from services.memory.skills import SkillsManager
from src.authority_protocol import AuthorityStore
from src.extension_installer import (
    ExtensionLifecycleError,
    ExtensionLifecycleManager,
    InlineWebAdapter,
)
from src.extension_registry import ExtensionRegistry, validate_extension_manifest
from src.extension_scan import ExtensionStaticScanner
from src.extension_skill_adapter import SkillBundleAdapter


SOURCE_URL = "https://github.com/example/demo-tools.git"
REVISION = "b" * 40


class _CopyGitClient:
    def __init__(self, source_dir: Path, revision: str = REVISION):
        self.source_dir = source_dir
        self.revision = revision

    def resolve_revision(self, source_url, requested_ref):
        return source_url, requested_ref or "HEAD", self.revision

    def checkout(self, source, ref, revision, destination):
        shutil.copytree(self.source_dir, destination)


def _manifest() -> dict:
    return {
        "protocol_version": "jos-extension.v1",
        "extension_id": "demo-tools",
        "name": "Demo Tools",
        "version": "1.0.0",
        "source": {"url": SOURCE_URL, "revision": "self"},
        "runtime": {"type": "web", "entrypoint": "index.html"},
        "capabilities": {
            "descriptor": {"type": "inline"},
            "schemas": [
                {
                    "name": "run_demo",
                    "description": "Run the demo",
                    "parameters": {
                        "type": "object",
                        "properties": {"target": {"type": "string"}},
                        "required": ["target"],
                        "additionalProperties": False,
                    },
                }
            ],
        },
        "permissions": {"default": "read_only", "capabilities": {}},
        "health": {"type": "catalog", "timeout_seconds": 3},
        "lifecycle": {"install": [], "start": [], "stop": [], "remove": []},
        "data_boundaries": {"read": [], "write": [], "network": []},
        "removal": {"remove_paths": [], "preserve_paths": []},
        "rollback": {"strategy": "pinned_revision", "retain_revisions": 2},
    }


def test_scan_then_approved_install_populates_inventory(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "jarvis-extension.json").write_text(json.dumps(_manifest()), encoding="utf-8")
    (source / "index.html").write_text("<h1>Demo</h1>", encoding="utf-8")
    (source / "LICENSE").write_text("MIT License\n\nPermission is hereby granted...", encoding="utf-8")

    client = _CopyGitClient(source)
    scanner = ExtensionStaticScanner(
        git_client=client,
        staging_root=tmp_path / "managed",
        data_dir=tmp_path / "scans",
    )
    artifact = scanner.run(SOURCE_URL, "HEAD", operator_id="operator")

    assert artifact["repo_class"] == "web_app"
    assert artifact["executed_repo_commands"] == []
    assert artifact["draft_manifest"] is not None
    validate_extension_manifest(artifact["draft_manifest"])

    authority = AuthorityStore(tmp_path / "authority.json")
    registry = ExtensionRegistry(tmp_path / "registry.json")
    manager = ExtensionLifecycleManager(
        root=tmp_path / "extension-root",
        registry=registry,
        authority=authority,
        git_client=client,
        adapters=[InlineWebAdapter()],
    )

    plan = manager.preview_source("install", SOURCE_URL, REVISION, operator_id="operator")
    decision = plan["authority_decision"]
    assert decision["decision"] == "approval_required"
    authority.resolve(
        decision["decision_id"], operator_id="operator", choice="approve", scope="once"
    )
    result = manager.execute_plan(plan["plan_id"], operator_id="operator")

    assert result["result"]["status"] == "succeeded"
    inventory = registry.capability_inventory("demo-tools")
    assert inventory is not None
    assert [item["name"] for item in inventory["capabilities"]] == ["run_demo"]
    assert set(registry.effective_capabilities()) == {"run_demo"}

    snapshot = registry.snapshot()["extensions"]["demo-tools"]
    assert snapshot["enabled"] is True
    assert snapshot["manifest"]["source"]["revision"] == REVISION


def _skill(name: str) -> str:
    return (
        "---\n"
        f"name: {name}\n"
        f"description: Run the {name} workflow\n"
        "---\n\n"
        "## Procedure\n\n1. Follow the reviewed procedure.\n"
    )


def _skill_bundle_checkout(tmp_path: Path) -> Path:
    source = tmp_path / "skill-source"
    (source / ".codex-plugin").mkdir(parents=True)
    (source / ".codex-plugin" / "plugin.json").write_text(
        json.dumps({"name": "demo-tools", "skills": "./skills/"}),
        encoding="utf-8",
    )
    for name in ("brainstorming", "test-driven-development"):
        skill_dir = source / "skills" / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(_skill(name), encoding="utf-8")
    return source


def _draft_install_manager(tmp_path: Path, source: Path):
    client = _CopyGitClient(source)
    scanner = ExtensionStaticScanner(
        git_client=client,
        staging_root=tmp_path / "managed",
        data_dir=tmp_path / "scans",
    )
    artifact = scanner.run(SOURCE_URL, "HEAD", operator_id="operator")
    skills = SkillsManager(str(tmp_path / "skills-data"))
    manager = ExtensionLifecycleManager(
        root=tmp_path / "extension-root",
        registry=ExtensionRegistry(tmp_path / "registry.json"),
        authority=AuthorityStore(tmp_path / "authority.json"),
        git_client=client,
        adapters=[SkillBundleAdapter(skills)],
    )
    return manager, artifact, skills


def _approve_execute(manager, authority, plan):
    decision = plan["authority_decision"]
    assert decision["decision"] == "approval_required"
    authority.resolve(
        decision["decision_id"], operator_id="operator", choice="approve", scope="once"
    )
    return manager.execute_plan(plan["plan_id"], operator_id="operator")


def test_draft_install_without_manifest_installs_and_lifecycles(tmp_path):
    source = _skill_bundle_checkout(tmp_path)
    manager, artifact, skills = _draft_install_manager(tmp_path, source)
    assert artifact["draft_manifest"] is not None
    authority = manager.authority

    plan = manager.preview_source(
        "install",
        SOURCE_URL,
        artifact["source_revision"],
        operator_id="operator",
        scan_id="scan-1",
        scan_revision=artifact["source_revision"],
        draft_manifest=artifact["draft_manifest"],
    )
    assert plan["manifest_origin"] == "scan_draft"
    assert plan["extension_id"] == "demo-tools"
    assert {item["id"] for item in plan["admitted_skills"]} == {
        "brainstorming",
        "test-driven-development",
    }

    result = _approve_execute(manager, authority, plan)
    assert result["result"]["status"] == "succeeded"

    installed_manifest = (
        manager.root
        / "installed"
        / "demo-tools"
        / "revisions"
        / REVISION
        / "jarvis-extension.json"
    )
    assert installed_manifest.is_file()
    assert {row["name"] for row in skills.load("operator")} == {
        "brainstorming",
        "test-driven-development",
    }
    snapshot = manager.registry.snapshot()["extensions"]["demo-tools"]
    assert snapshot["enabled"] is True
    assert snapshot["manifest"]["source"]["revision"] == REVISION

    for operation in ("disable", "enable", "uninstall"):
        lifecycle = manager.preview_lifecycle(
            operation, "demo-tools", operator_id="operator"
        )
        _approve_execute(manager, authority, lifecycle)
        if operation == "disable":
            assert skills.load("operator") == []
        elif operation == "enable":
            assert {row["name"] for row in skills.load("operator")} == {
                "brainstorming",
                "test-driven-development",
            }
    assert skills.load("operator") == []
    assert manager.registry.snapshot()["extensions"] == {}


def test_draft_binding_failures_fail_closed(tmp_path):
    source = _skill_bundle_checkout(tmp_path)
    manager, artifact, _skills = _draft_install_manager(tmp_path, source)

    with pytest.raises(ExtensionLifecycleError) as excinfo:
        manager.preview_source(
            "install",
            SOURCE_URL,
            artifact["source_revision"],
            operator_id="operator",
            scan_id="scan-1",
            scan_revision="c" * 40,
            draft_manifest=artifact["draft_manifest"],
        )
    assert excinfo.value.code == "extension_scan_revision_mismatch"

    with pytest.raises(ExtensionLifecycleError) as excinfo:
        manager.preview_source(
            "install",
            SOURCE_URL,
            artifact["source_revision"],
            operator_id="operator",
            draft_manifest=artifact["draft_manifest"],
        )
    assert excinfo.value.code == "extension_scan_binding_invalid"

    with pytest.raises(ExtensionLifecycleError) as excinfo:
        manager.preview_source(
            "install",
            SOURCE_URL,
            artifact["source_revision"],
            operator_id="operator",
            scan_id="scan-1",
            scan_revision=artifact["source_revision"],
            draft_manifest=artifact["draft_manifest"],
            expected_manifest=artifact["draft_manifest"],
        )
    assert excinfo.value.code == "extension_scan_draft_not_allowed"


def test_committed_repository_manifest_wins_over_scan_draft(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "jarvis-extension.json").write_text(json.dumps(_manifest()), encoding="utf-8")
    (source / "index.html").write_text("<h1>Demo</h1>", encoding="utf-8")

    client = _CopyGitClient(source)
    artifact = ExtensionStaticScanner(
        git_client=client,
        staging_root=tmp_path / "managed",
        data_dir=tmp_path / "scans",
    ).run(SOURCE_URL, "HEAD", operator_id="operator")
    assert artifact["draft_manifest"] is not None

    authority = AuthorityStore(tmp_path / "authority.json")
    manager = ExtensionLifecycleManager(
        root=tmp_path / "extension-root",
        registry=ExtensionRegistry(tmp_path / "registry.json"),
        authority=authority,
        git_client=client,
        adapters=[InlineWebAdapter()],
    )
    plan = manager.preview_source(
        "install",
        SOURCE_URL,
        REVISION,
        operator_id="operator",
        scan_id="scan-1",
        scan_revision=artifact["source_revision"],
        draft_manifest=artifact["draft_manifest"],
    )
    assert plan["manifest_origin"] == "repository"
    assert plan["manifest"]["version"] == "1.0.0"
    assert plan["manifest"]["capabilities"]["descriptor"]["type"] == "inline"

    result = _approve_execute(manager, authority, plan)
    assert result["result"]["status"] == "succeeded"
    snapshot = manager.registry.snapshot()["extensions"]["demo-tools"]
    assert snapshot["manifest"]["version"] == "1.0.0"
