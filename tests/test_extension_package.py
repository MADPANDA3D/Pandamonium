"""Exact-artifact installation, including generated files and same-source upgrades."""

import hashlib
import gzip
import io
import json
import tarfile
from pathlib import Path

import pytest

from src.authority_protocol import AuthorityStore
from src.extension_installer import ExtensionLifecycleManager, InlineWebAdapter
from src.extension_package import PackageError, extract_package
from src import extension_package
from src.extension_registry import ExtensionRegistry


REVISION = "1" * 40
SOURCE = "https://github.com/example/atlas-lab.git"


def archive(files):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as bundle:
        for name, value in files.items():
            info = tarfile.TarInfo(name)
            if isinstance(value, tarfile.TarInfo):
                bundle.addfile(value)
            else:
                value = value.encode()
                info.size = len(value)
                bundle.addfile(info, io.BytesIO(value))
    return output.getvalue()


def package(version="1.0.0"):
    manifest = json.loads((Path(__file__).parent / "fixtures/extensions/atlas.manifest.json").read_text())
    manifest["source"] = {"url": SOURCE, "revision": REVISION}
    manifest["version"] = version
    manifest["runtime"] = {"type": "web", "entrypoint": "index.html"}
    manifest["capabilities"] = {"descriptor": {"type": "inline"}, "schemas": [{
        "name": "inspect_fixture", "description": "Inspect the fixture",
        "parameters": {"type": "object", "properties": {}},
    }]}
    manifest["health"] = {"type": "catalog", "timeout_seconds": 3}
    manifest["permissions"]["capabilities"] = {}
    content = archive({
        "bundle/jarvis-extension.json": json.dumps(manifest),
        "bundle/index.html": "generated " + version,
    })
    return manifest, content


class NoGit:
    def resolve_revision(self, *args):
        raise AssertionError("Signed package must never resolve or clone Git")


def manager(tmp_path):
    return ExtensionLifecycleManager(
        root=tmp_path / "managed", registry=ExtensionRegistry(tmp_path / "registry.json"),
        authority=AuthorityStore(tmp_path / "authority.json"),
        git_client=NoGit(), adapters=[InlineWebAdapter()],
    )


def preview(manager, version="1.0.0", operation="install"):
    manifest, content = package(version)
    return manager.preview_source(
        operation, SOURCE, REVISION, operator_id="operator", expected_manifest=manifest,
        distribution={"artifact": {"sha256": hashlib.sha256(content).hexdigest(), "size_bytes": len(content)}},
        artifact_content=content,
    )


def execute(manager, plan):
    manager.authority.resolve(plan["authority_decision"]["decision_id"], operator_id="operator", choice="approve", scope="once")
    return manager.execute_plan(plan["plan_id"], operator_id="operator")


def test_generated_files_survive_install_upgrade_same_source_and_rollback(tmp_path):
    instance = manager(tmp_path)
    execute(instance, preview(instance))
    first = instance.snapshot()["extensions"]["atlas"]["active_revision"]
    execute(instance, preview(instance, "2.0.0", "upgrade"))
    second = instance.snapshot()["extensions"]["atlas"]["active_revision"]
    assert first != second
    assert (instance._revision_path("atlas", second) / "index.html").read_text() == "generated 2.0.0"
    execute(instance, instance.preview_lifecycle("rollback", "atlas", operator_id="operator"))
    assert instance.snapshot()["extensions"]["atlas"]["active_revision"] == first
    assert instance.registry.snapshot()["extensions"]["atlas"]["manifest"]["source"]["revision"] == REVISION
    execute(instance, instance.preview_lifecycle("disable", "atlas", operator_id="operator"))
    instance = manager(tmp_path)
    execute(instance, instance.preview_lifecycle("enable", "atlas", operator_id="operator"))
    assert instance.registry.snapshot()["extensions"]["atlas"]["manifest"]["version"] == "1.0.0"


def test_staged_package_tampering_is_rejected_before_activation(tmp_path):
    instance = manager(tmp_path)
    plan = preview(instance)
    state = instance._read_state()["plans"][plan["plan_id"]]
    (Path(state["staging_path"]) / "index.html").write_text("changed after preview")
    with pytest.raises(PackageError, match="extension_package_content_changed"):
        execute(instance, plan)
    assert not instance.registry.snapshot()["extensions"]


@pytest.mark.parametrize("name", ["../escape", "/absolute", "bundle/../../escape", "C:/escape", "bundle\\escape", "bundle/.git/config"])
def test_archive_paths_are_confined(tmp_path, name):
    content = archive({"bundle/jarvis-extension.json": "{}", name: "bad"})
    with pytest.raises(PackageError):
        extract_package(content, tmp_path / "out")


def test_archive_links_are_rejected(tmp_path):
    link = tarfile.TarInfo("bundle/link")
    link.type = tarfile.SYMTYPE
    link.linkname = "/tmp"
    with pytest.raises(PackageError):
        extract_package(archive({"bundle/jarvis-extension.json": "{}", "link": link}), tmp_path / "out")


def test_expansion_is_bounded_before_tar_metadata_is_parsed(tmp_path, monkeypatch):
    monkeypatch.setattr(extension_package, "MAX_PACKAGE_BYTES", 16_384)
    content = gzip.compress(b"x" * 100_000)
    with pytest.raises(PackageError, match="extension_package_size_invalid"):
        extract_package(content, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_archive_member_count_and_duplicate_paths_are_bounded(tmp_path, monkeypatch):
    content = archive({"jarvis-extension.json": "{}", "file": "a", "FILE": "b"})
    with pytest.raises(PackageError, match="extension_package_path_duplicate"):
        extract_package(content, tmp_path / "duplicate")
    monkeypatch.setattr(extension_package, "MAX_PACKAGE_FILES", 1)
    with pytest.raises(PackageError, match="extension_package_size_invalid"):
        extract_package(content, tmp_path / "too-many")
