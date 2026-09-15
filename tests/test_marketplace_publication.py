"""Developer publication, concurrent catalog CAS, recovery and pinned bootstrap."""

import copy
import json
import threading
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scripts.build_marketplace_catalog import build_catalog, public_key_b64
from src import marketplace_channel as channel
from src import marketplace_publish as publisher
from src.extension_package import build_prepared_package
from src.marketplace_catalog import validate_published_catalog
from tests.test_marketplace_catalog_publish import REVISION, _write_repo


def signed(entries, key):
    now = datetime.now(timezone.utc)
    return build_catalog(
        entries,
        private_key=key,
        key_id=publisher.KEY_ID,
        catalog_id="pandamonium-community",
        generated_at=now.isoformat(),
        expires_at=(now + timedelta(days=90)).isoformat(),
    )


@pytest.fixture
def publication(tmp_path, monkeypatch):
    key = Ed25519PrivateKey.generate()
    keys = {publisher.KEY_ID: public_key_b64(key)}
    monkeypatch.setattr(publisher, "_keys", lambda: keys)
    monkeypatch.setattr(publisher, "_key", lambda: key)
    monkeypatch.setenv(
        "PANDAMONIUM_MARKETPLACE_SIGNING_KEY_FILE", "/publisher-only/key"
    )
    repo = _write_repo(tmp_path)
    manifest_path = repo / "jarvis-extension.json"
    manifest = json.loads(manifest_path.read_text())
    manifest.update(
        runtime={"type": "skills", "entrypoint": "SKILL.md"}, configuration=[]
    )
    manifest["source"]["revision"] = REVISION
    manifest["capabilities"] = {
        "descriptor": {
            "type": "skill_bundle",
            "format": "agent_skill",
            "include": ["demo-skill"],
        }
    }
    manifest_path.write_text(json.dumps(manifest))
    (repo / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: Explain the demo\n---\nUse the packaged reference.\n"
    )
    (repo / "LICENSE").write_text(
        "MIT License\nPermission is hereby granted, free of charge, to any person obtaining a copy"
    )
    archive = tmp_path / "package.tar.gz"
    build_prepared_package(repo, archive)
    remote = {"catalog": signed([], key), "sha": "0", "uploads": 0, "conflict": False}
    monkeypatch.setattr(
        publisher,
        "read_remote_catalog",
        lambda: (copy.deepcopy(remote["catalog"]), remote["sha"]),
    )

    def cas(*args, payload=None):
        if remote["conflict"]:
            remote["conflict"] = False
            remote["sha"] += "1"
            raise publisher.PublicationError("conflict")
        assert payload["sha"] == remote["sha"]
        import base64

        remote["catalog"] = json.loads(base64.b64decode(payload["content"]))
        remote["sha"] += "1"
        return {}

    monkeypatch.setattr(publisher, "_json", cas)

    def upload(path, manifest, digest, proof):
        assert path.read_bytes() == archive.read_bytes()
        assert proof == {"kind": "native_skills", "count": 1}
        remote["uploads"] += 1
        return "https://github.com/MADPANDA3D/Pandamonium/releases/download/test"

    monkeypatch.setattr(publisher, "_upload", upload)
    return archive, remote, key, keys


def test_real_isolated_validation_publish_retry_conflict_and_rollback(publication):
    archive, remote, key, keys = publication
    original = copy.deepcopy(remote["catalog"])
    remote["conflict"] = True
    stages = []
    first = publisher.publish_package(
        archive.read_bytes(), owner="disposable", progress=stages.append
    )
    assert first["state"] == "published"
    assert stages[-1] == "Publishing catalog and verifying readback"
    assert (
        len(validate_published_catalog(remote["catalog"], trusted_keys=keys)["entries"])
        == 1
    )
    assert (
        publisher.publish_package(
            archive.read_bytes(), owner="disposable", progress=lambda _: None
        )["digest"]
        == first["digest"]
    )
    assert len(remote["catalog"]["entries"]) == 1
    modified = copy.deepcopy(remote["catalog"]["entries"][0])
    modified["artifact"]["sha256"] = "f" * 64
    with pytest.raises(publisher.PublicationError, match="version_conflict"):
        publisher.update_catalog(modified, key)
    publisher.update_catalog(None, key, rollback=original)
    assert remote["catalog"]["entries"] == []
    assert remote["catalog"]["generated_at"] > original["generated_at"]


def test_failure_does_not_publish_and_retry_reconciles(publication, monkeypatch):
    archive, remote, _key, _keys = publication
    original = copy.deepcopy(remote["catalog"])
    real_upload = publisher._upload
    monkeypatch.setattr(
        publisher,
        "_upload",
        lambda *a: (_ for _ in ()).throw(publisher.PublicationError("upload_failed")),
    )
    with pytest.raises(publisher.PublicationError, match="upload_failed"):
        publisher.publish_package(
            archive.read_bytes(), owner="test", progress=lambda _: None
        )
    assert remote["catalog"] == original
    monkeypatch.setattr(publisher, "_upload", real_upload)
    assert (
        publisher.publish_package(
            archive.read_bytes(), owner="test", progress=lambda _: None
        )["state"]
        == "published"
    )


def test_bootstrap_pinned_trust_lkg_and_tampering(tmp_path, monkeypatch):
    key = Ed25519PrivateKey.generate()
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    keys = {publisher.KEY_ID: public_key_b64(key)}
    (bundled / "trusted_keys.json").write_text(json.dumps(keys))
    original = signed([], key)
    (bundled / "catalog.json").write_text(json.dumps(original))
    monkeypatch.setattr(channel, "BUNDLED", bundled)
    monkeypatch.setattr(channel, "fetch_catalog", lambda: signed([], key))
    cache = tmp_path / "fresh"
    first, observed_keys = channel.load_channel(cache, refresh=True)
    assert observed_keys == keys
    assert (cache / "catalog.json").is_file()
    assert not (cache / "trusted_keys.json").exists()
    attacker = Ed25519PrivateKey.generate()
    monkeypatch.setattr(channel, "fetch_catalog", lambda: signed([], attacker))
    assert channel.load_channel(cache, refresh=True)[0] == first
    (cache / "catalog.json").write_text("{}")
    assert channel.load_channel(cache, refresh=True)[0] == original
    (bundled / "catalog.json").write_text("{}")
    with pytest.raises(Exception, match="marketplace_catalog_offline"):
        channel.load_channel(cache, refresh=True)


def test_jobs_single_flight_owner_isolation_failure_retry_and_restart(
    tmp_path, monkeypatch
):
    gate = threading.Event()
    entered = threading.Event()

    def publish(content, *, owner, progress, source_revision=None, version=None):
        assert version == "2.0.0"
        entered.set()
        gate.wait(5)
        raise publisher.PublicationError("expected_failure")

    monkeypatch.setattr(publisher, "publish_package", publish)
    jobs = publisher.PublicationJobs(tmp_path)
    first = jobs.start(b"package", "owner", version="2.0.0")
    assert entered.wait(2)
    assert jobs.start(b"package", "owner", version="2.0.0")["id"] == first["id"]
    with pytest.raises(publisher.PublicationError, match="busy"):
        jobs.start(b"different", "owner")
    with pytest.raises(publisher.PublicationError, match="not_found"):
        jobs.get(first["id"], "other-owner")
    gate.set()
    jobs.executor.shutdown(wait=True)
    assert jobs.get(first["id"], "owner")["message"] == "expected_failure"
    restarted = publisher.PublicationJobs(tmp_path)
    monkeypatch.setattr(
        publisher, "publish_package", lambda *a, **k: {"state": "published"}
    )
    assert restarted.start(b"package", "owner", version="2.0.0")["id"] == first["id"]
    restarted.executor.shutdown(wait=True)
    assert restarted.get(first["id"], "owner")["state"] == "published"


def test_draft_provenance_and_version_are_finalized_before_testing(
    publication, monkeypatch, tmp_path
):
    archive, remote, _key, _keys = publication
    from src.extension_package import extract_package

    tree = tmp_path / "draft"
    extract_package(archive.read_bytes(), tree)
    manifest = json.loads((tree / "jarvis-extension.json").read_text())
    manifest["version"] = "0.0.0-draft"
    manifest["source"]["revision"] = "self"
    (tree / "jarvis-extension.json").write_text(json.dumps(manifest))
    draft = tmp_path / "draft.tar.gz"
    build_prepared_package(tree, draft)

    def upload(path, manifest, digest, proof):
        extracted = tmp_path / "published"
        extract_package(path.read_bytes(), extracted)
        packaged = json.loads((extracted / "jarvis-extension.json").read_text())
        assert packaged == manifest
        assert packaged["source"]["revision"] == REVISION
        assert packaged["version"] == "2.3.4"
        assert proof == {"kind": "native_skills", "count": 1}
        return "https://github.com/MADPANDA3D/Pandamonium/releases/download/final"

    monkeypatch.setattr(publisher, "_upload", upload)
    result = publisher.publish_package(
        draft.read_bytes(),
        owner="disposable",
        progress=lambda _: None,
        source_revision=REVISION,
        version="2.3.4",
    )
    assert result["input_digest"] != result["digest"]
    assert remote["catalog"]["entries"][0]["artifact"]["sha256"] == result["digest"]


def test_publish_route_requires_the_scan_owner_before_reading_package(
    tmp_path, monkeypatch
):
    import asyncio

    from fastapi import HTTPException

    from routes import extension_routes
    from src.authority_protocol import AuthorityStore
    from src.extension_installer import ExtensionLifecycleManager
    from src.extension_registry import ExtensionRegistry

    manager = ExtensionLifecycleManager(
        root=tmp_path / "extensions",
        registry=ExtensionRegistry(tmp_path / "registry.json"),
        authority=AuthorityStore(tmp_path / "authority.json"),
    )
    routes = {
        r.path: r.endpoint
        for r in extension_routes.setup_extension_routes(
            manager, marketplace_loader=lambda: None
        ).routes
    }
    monkeypatch.setattr(
        extension_routes,
        "get_scan",
        lambda _: {"operator_id": "alice", "status": "succeeded"},
    )
    monkeypatch.setattr(
        extension_routes,
        "scan_package_content",
        lambda _: pytest.fail("Must not read another owner package"),
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            routes["/api/extensions/scans/{scan_id}/publish"](
                "scan-id", extension_routes.PublicationRequest(), "bob"
            )
        )
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException):
        asyncio.run(
            routes["/api/extensions/installed/{extension_id}/publish"](
                "missing", extension_routes.PublicationRequest(), "bob"
            )
        )


def test_executable_publication_validation_hides_publisher_credentials_and_cleans_runtime(
    tmp_path, monkeypatch
):
    from src.extension_cli_adapter import GeneratedCliAdapter
    from src.extension_package import package_tree_digest
    from tests.test_extension_cli_adapter import package

    source, manifest = package(tmp_path)
    private = tmp_path / "publisher-only.pem"
    private.write_text("private publisher fixture")
    monkeypatch.setenv("PANDAMONIUM_MARKETPLACE_SIGNING_KEY_FILE", str(private))
    adapter_file = source / ".pandamonium/adapter.py"
    adapter_file.write_text(
        'import os\nassert os.getenv("PANDAMONIUM_MARKETPLACE_SIGNING_KEY_FILE") is None\nassert not os.path.exists('
        + repr(str(private))
        + ")\n"
        + adapter_file.read_text()
    )
    work = tmp_path / "validation"
    proof = publisher.validate_package(source, manifest, work, "disposable")
    assert proof == {"kind": "isolated_operations", "checks": 1}
    runtime = GeneratedCliAdapter(work / "extensions")._runtime(
        manifest, package_tree_digest(source), "disposable"
    )
    assert not runtime.exists()
    assert private.read_text() == "private publisher fixture"


def test_catalog_cas_preserves_concurrent_entry_and_rollback_aborts_on_conflict(
    publication, monkeypatch
):
    archive, remote, key, _keys = publication
    publisher.publish_package(
        archive.read_bytes(), owner="disposable", progress=lambda _: None
    )
    snapshot = copy.deepcopy(remote["catalog"])
    entry = copy.deepcopy(snapshot["entries"][0])
    entry["manifest"]["version"] = "2.0.0"
    concurrent = copy.deepcopy(entry)
    concurrent["manifest"]["version"] = "1.5.0"
    cas = publisher._json

    def race(*args, payload=None):
        remote["catalog"] = signed([*remote["catalog"]["entries"], concurrent], key)
        remote["sha"] += "1"
        monkeypatch.setattr(publisher, "_json", cas)
        raise publisher.PublicationError("conflict")

    monkeypatch.setattr(publisher, "_json", race)
    published = publisher.update_catalog(entry, key)
    assert {e["manifest"]["version"] for e in published["entries"]} == {
        "1.0.0",
        "1.5.0",
        "2.0.0",
    }
    remote["conflict"] = True
    with pytest.raises(publisher.PublicationError, match="changed_during_rollback"):
        publisher.update_catalog(None, key, rollback=snapshot)
    assert remote["catalog"] == published
