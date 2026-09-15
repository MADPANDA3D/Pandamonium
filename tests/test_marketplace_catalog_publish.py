"""MAD-941: marketplace catalog publishing tooling."""

import importlib.util
import json
import shutil
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.marketplace_catalog import (
    MarketplaceCatalogError,
    validate_published_catalog,
    verify_catalog_artifact,
)
from services.memory.skills import SkillsManager
from src.authority_protocol import AuthorityStore
from src.extension_installer import ExtensionLifecycleManager
from src.extension_package import PackageError
from src.extension_registry import ExtensionRegistry
from src.extension_skill_adapter import SkillBundleAdapter


SCRIPT = Path(__file__).parents[1] / "scripts" / "build_marketplace_catalog.py"
_module_spec = importlib.util.spec_from_file_location("build_marketplace_catalog", SCRIPT)
publisher = importlib.util.module_from_spec(_module_spec)
_module_spec.loader.exec_module(publisher)

SOURCE_URL = "https://github.com/example/demo-tools.git"
REVISION = "b" * 40


def _write_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "jarvis-extension.json").write_text(
        json.dumps({
            "protocol_version": "jos-extension.v1",
            "extension_id": "demo-tools",
            "name": "Demo Tools",
            "version": "1.0.0",
            "source": {"url": SOURCE_URL, "revision": "self"},
            "runtime": {"type": "web", "entrypoint": "index.html"},
            "capabilities": {"descriptor": {"type": "inline"}, "schemas": [{
                "name": "run_demo",
                "description": "Run the demo",
                "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
            }]},
            "permissions": {"default": "read_only", "capabilities": {}},
            "health": {"type": "catalog", "timeout_seconds": 5},
            "lifecycle": {"install": [], "start": [], "stop": [], "remove": []},
            "data_boundaries": {"read": [], "write": [], "network": []},
            "removal": {"remove_paths": [], "preserve_paths": []},
            "rollback": {"strategy": "pinned_revision", "retain_revisions": 1},
            "configuration": [{"key": "DEMO_TOKEN", "description": "Owner token", "required": True, "secret": True}],
        }),
        encoding="utf-8",
    )
    (repo / "index.html").write_text("<h1>demo</h1>", encoding="utf-8")
    return repo


def _fake_git(repo: Path, revision: str):
    def _fake(argv, *, cwd=None):
        if argv[:2] == ["git", "clone"]:
            shutil.copytree(repo, Path(argv[-1]))
            return ""
        if argv[:3] == ["git", "rev-parse", "HEAD"]:
            return revision
        if argv[:2] == ["git", "archive"]:
            output = Path(argv[argv.index("-o") + 1])
            with tarfile.open(output, "w:gz") as archive:
                archive.add(repo, arcname="demo-tools-1.0.0")
            return ""
        raise AssertionError(argv)
    return _fake


def _spec() -> dict:
    return {
        "source_url": SOURCE_URL,
        "ref": "v1.0.0",
        "summary": "Demo tools package",
        "categories": ["developer-tools"],
        "license": "MIT",
        "publisher": {"id": "madpanda3d", "name": "MADPANDA3D", "url": "https://github.com/MADPANDA3D"},
        "compatibility": {
            "pandamonium_min": "1.0.0",
            "pandamonium_max": "1.99.99",
            "platforms": ["linux"],
            "architectures": ["amd64"],
        },
    }


def _key(tmp_path: Path) -> tuple[Ed25519PrivateKey, Path]:
    key = Ed25519PrivateKey.generate()
    path = tmp_path / "signing.pem"
    path.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    return key, path


def _build(tmp_path, monkeypatch, specs=None):
    repo = _write_repo(tmp_path)
    monkeypatch.setattr(publisher, "_run", _fake_git(repo, REVISION))
    _key_obj, pem = _key(tmp_path)
    private_key = publisher.load_private_key(pem)
    now = datetime(2026, 9, 13, tzinfo=timezone.utc)
    workdir = tmp_path / "work"
    workdir.mkdir()
    catalog, trusted, artifacts = publisher.build_from_specs(
        specs or [_spec()],
        private_key=private_key,
        key_id="test-key",
        artifact_base_url="https://example.com/marketplace",
        catalog_id="test-catalog",
        generated_at=now.isoformat(),
        expires_at=(now + timedelta(days=90)).isoformat(),
        workdir=workdir,
    )
    out_dir = tmp_path / "out"
    publisher.write_outputs(out_dir, catalog, trusted)
    for artifact in artifacts:
        shutil.copy2(artifact["path"], out_dir / artifact["filename"])
    return catalog, trusted, artifacts, out_dir


def test_catalog_builds_validates_and_verifies(tmp_path, monkeypatch):
    catalog, trusted, artifacts, out_dir = _build(tmp_path, monkeypatch)

    assert (out_dir / "catalog.json").is_file()
    assert (out_dir / "trusted_keys.json").is_file()
    validated = validate_published_catalog(
        catalog, trusted_keys=trusted, now=datetime(2026, 9, 14, tzinfo=timezone.utc)
    )
    assert len(validated["entries"]) == 1
    entry = validated["entries"][0]
    assert entry["manifest"]["extension_id"] == "demo-tools"
    assert entry["artifact"]["sha256"] == artifacts[0]["sha256"]
    assert entry["artifact"]["size_bytes"] == artifacts[0]["size_bytes"]
    assert entry["artifact"]["url"].endswith(artifacts[0]["filename"])
    assert entry["configuration"] == [
        {"key": "DEMO_TOKEN", "description": "Owner token", "required": True, "secret": True}
    ]
    assert entry["publisher"]["key_id"] == "test-key"

    content = (out_dir / artifacts[0]["filename"]).read_bytes()
    assert verify_catalog_artifact(entry["artifact"], content) is True


def test_prepared_skill_publishes_and_installs_without_git(tmp_path, monkeypatch):
    repo = _write_repo(tmp_path)
    path = repo / "jarvis-extension.json"
    manifest = json.loads(path.read_text())
    manifest["source"]["revision"] = REVISION
    manifest["runtime"] = {"type": "skills", "entrypoint": "SKILL.md"}
    manifest["capabilities"] = {"descriptor": {
        "type": "skill_bundle", "format": "agent_skill", "include": ["demo-skill"],
    }}
    path.write_text(json.dumps(manifest))
    (repo / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: Inspect the generated integration\nversion: 1.0.0\n---\n\n"
        "Use the packaged reference to inspect the integration.\n"
    )
    (repo / "references").mkdir()
    (repo / "references" / "guide.md").write_text("Generated reference preserved")
    (repo / "run.sh").write_text("#!/bin/sh\nprintf 'generated wrapper'\n")
    (repo / "run.sh").chmod(0o755)

    def no_git(*args, **kwargs):
        raise AssertionError("Prepared publication and artifact install must not clone Git")

    monkeypatch.setattr(publisher, "_run", no_git)
    spec = {**_spec(), "prepared_path": str(repo), "ref": REVISION}
    workdir = tmp_path / "work"
    workdir.mkdir()
    key, _ = _key(tmp_path)
    now = datetime.now(timezone.utc)
    catalog, trusted, artifacts = publisher.build_from_specs(
        [spec], private_key=key, key_id="test-key", artifact_base_url="https://example.com/marketplace",
        catalog_id="test-catalog", generated_at=now.isoformat(),
        expires_at=(now + timedelta(days=90)).isoformat(), workdir=workdir,
    )
    entry = validate_published_catalog(catalog, trusted_keys=trusted, now=now)["entries"][0]
    content = artifacts[0]["path"].read_bytes()
    # Rebuilding the same prepared tree produces the same exact signed content.
    assert publisher.build_artifact(spec, workdir)["sha256"] == entry["artifact"]["sha256"]
    skills = SkillsManager(str(tmp_path / "data"))
    manager = ExtensionLifecycleManager(
        root=tmp_path / "managed", registry=ExtensionRegistry(tmp_path / "registry.json"),
        authority=AuthorityStore(tmp_path / "authority.json"), adapters=[SkillBundleAdapter(skills)],
    )
    monkeypatch.setattr(manager.git, "resolve_revision", no_git)
    plan = manager.preview_source(
        "install", SOURCE_URL, REVISION, operator_id="operator", expected_manifest=entry["manifest"],
        distribution={"artifact": entry["artifact"]}, artifact_content=content,
    )
    assert skills.load("operator") == []  # Preview must not admit skills.
    manager.authority.resolve(plan["authority_decision"]["decision_id"], operator_id="operator", choice="approve", scope="once")
    manager.execute_plan(plan["plan_id"], operator_id="operator")
    installed = manager._revision_path("demo-tools", entry["artifact"]["sha256"])
    assert (installed / "run.sh").stat().st_mode & 0o111 == 0o111
    assert [skill["name"] for skill in skills.index_for("operator")] == ["demo-skill"]
    assert skills.read_skill_reference("demo-skill", "references/guide.md", "operator") == "Generated reference preserved"
    assert manager.registry.snapshot()["extensions"]["demo-tools"]["admitted_skills"][0]["id"] == "demo-skill"
    assert skills.load("different-owner") == []

    (repo / ".env").write_text("EXAMPLE_TOKEN=fixture-secret")
    with pytest.raises(PackageError, match="extension_package_private_file"):
        publisher.build_artifact(spec, workdir)


@pytest.mark.parametrize("change", [
    {"ref": "main"}, {"source_url": "https://github.com/example/different.git"},
    {"artifact_filename": "../escape.tar.gz"},
])
def test_prepared_package_rejects_unbound_source_and_unsafe_output(tmp_path, change):
    repo = _write_repo(tmp_path)
    path = repo / "jarvis-extension.json"
    manifest = json.loads(path.read_text())
    manifest["source"]["revision"] = REVISION
    path.write_text(json.dumps(manifest))
    with pytest.raises(publisher.CatalogPublishError):
        publisher.build_artifact({**_spec(), "prepared_path": str(repo), "ref": REVISION, **change}, tmp_path)


def test_tampered_catalog_and_artifact_fail_closed(tmp_path, monkeypatch):
    catalog, trusted, artifacts, out_dir = _build(tmp_path, monkeypatch)
    entry = catalog["entries"][0]

    tampered = json.loads(json.dumps(catalog))
    tampered["entries"][0]["summary"] = "tampered"
    with pytest.raises(MarketplaceCatalogError):
        validate_published_catalog(
            tampered, trusted_keys=trusted, now=datetime(2026, 9, 14, tzinfo=timezone.utc)
        )

    assert verify_catalog_artifact(
        entry["artifact"], (out_dir / artifacts[0]["filename"]).read_bytes()
    ) is True
    with pytest.raises(MarketplaceCatalogError):
        verify_catalog_artifact(entry["artifact"], b"not the artifact")


@pytest.mark.parametrize(
    "specs",
    [
        [],
        [{"source_url": "http://insecure.example/repo.git"}],
        [{"source_url": SOURCE_URL}],
    ],
)
def test_bad_package_specs_fail_closed(specs):
    with pytest.raises(SystemExit):
        publisher.validate_package_specs(specs)


def test_duplicate_package_ids_fail(tmp_path, monkeypatch):
    first = _spec()
    second = _spec()
    second["ref"] = "v1.0.1"
    with pytest.raises(SystemExit):
        publisher.validate_package_specs([{**first, "extension_id": "demo-tools"}, {**second, "extension_id": "demo-tools"}])
