from __future__ import annotations

import base64
import io
import json
import os
import sqlite3
import sys
import tarfile
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src import release_updater

OLD_COMMIT = "1" * 40
NEW_COMMIT = "2" * 40


def _signed_manifest(tmp_path: Path) -> tuple[dict, Path]:
    archive = tmp_path / "pandamonium-1.0.11.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for name, payload, mode in (
            ("pandamonium/SOURCE_REVISION", f"{NEW_COMMIT}\n".encode(), 0o644),
            ("pandamonium/src/constants.py", b'APP_VERSION = "1.0.11"\n', 0o644),
            ("pandamonium/requirements.txt", b"httpx\ncryptography\n", 0o644),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mode = mode
            tar.addfile(info, io.BytesIO(payload))
    key = Ed25519PrivateKey.generate()
    public_key = tmp_path / "release.pub"
    public_key.write_text(
        base64.b64encode(
            key.public_key().public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            )
        ).decode(),
        encoding="utf-8",
    )
    unsigned = {
        "schema_version": release_updater.SCHEMA,
        "version": "1.0.11",
        "tag": "v1.0.11",
        "commit": NEW_COMMIT,
        "channel": "stable",
        "published_at": "2026-09-06T00:00:00+00:00",
        "compatibility": {
            "minimum_version": "1.0.10",
            "minimum_python": "3.10",
            "migration_entrypoint": "core.database",
            "migration_version": "1.0.11",
            "data_restore_required": False,
        },
        "artifact": {
            "name": archive.name,
            "size": archive.stat().st_size,
            "sha256": release_updater.sha256_file(archive),
            "requirements_sha256": release_updater.hashlib.sha256(
                b"httpx\ncryptography\n"
            ).hexdigest(),
        },
    }
    return {
        **unsigned,
        "signature": {
            "algorithm": "ed25519",
            "key_id": "pandamonium-release-2026",
            "value": base64.b64encode(
                key.sign(release_updater.canonical_manifest_bytes(unsigned))
            ).decode(),
        },
    }, public_key


def _layout(
    tmp_path: Path, monkeypatch
) -> tuple[release_updater.UpdateExecutor, dict, Path, Path]:
    manifest, public_key = _signed_manifest(tmp_path)
    install = tmp_path / "install"
    old = install / "releases" / "1.0.10-old"
    old.mkdir(parents=True)
    (old / "SOURCE_REVISION").write_text(f"{OLD_COMMIT}\n", encoding="utf-8")
    (old / "src").mkdir()
    (old / "src" / "constants.py").write_text(
        'APP_VERSION = "1.0.10"\n', encoding="utf-8"
    )
    (install / "current").symlink_to(old)
    data = tmp_path / "data"
    data.mkdir()
    (data / "owner.txt").write_text("preserved", encoding="utf-8")
    backup_root = tmp_path / "backups"
    config = release_updater.UpdateConfig(
        install, data, backup_root, "pandamonium.service", "http://127.0.0.1:7000"
    )
    executor = release_updater.UpdateExecutor(config)
    monkeypatch.setattr(release_updater, "STATE_PATH", data / "updates" / "state.json")
    monkeypatch.setattr(
        release_updater,
        "verify_release_manifest",
        lambda value, **_kwargs: (
            value
            if value is manifest
            else release_updater.verify_release_manifest(
                value, public_key_path=public_key
            )
        ),
    )
    return executor, manifest, tmp_path / manifest["artifact"]["name"], old


def _stub_runtime(executor, monkeypatch, *, healthy=True, migration_error=False):
    events = []
    monkeypatch.setattr(executor, "_prepare_runtime", lambda *_args: None)
    monkeypatch.setattr(executor, "_service", lambda action: events.append(action))
    monkeypatch.setattr(
        executor,
        "_healthy",
        lambda version, **_kwargs: healthy or version == "1.0.10",
    )

    def backup(_previous, manifest):
        directory = executor.config.backup_root / f"update-{manifest['version']}"
        directory.mkdir(parents=True)
        archive = directory / "data.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(executor.config.data_dir, arcname="data")
        (directory / "update-backup.json").write_text(
            json.dumps(
                {
                    "schema_version": "pandamonium.update-backup.v1",
                    "data_sha256": release_updater.sha256_file(archive),
                }
            ),
            encoding="utf-8",
        )
        return directory, archive

    def migrate(_candidate, data):
        if migration_error:
            raise release_updater.UpdateError("migration rehearsal failed")
        assert (data / "owner.txt").read_text(encoding="utf-8") == "preserved"

    monkeypatch.setattr(executor, "_backup", backup)
    monkeypatch.setattr(executor, "_migrate", migrate)
    return events


def test_release_manifest_signature_and_asset_binding_fail_closed(tmp_path):
    manifest, public_key = _signed_manifest(tmp_path)
    release = {
        "tag_name": "v1.0.11",
        "draft": False,
        "prerelease": False,
        "assets": [
            {
                "name": manifest["artifact"]["name"],
                "browser_download_url": "https://github.com/MADPANDA3D/Pandamonium/releases/download/v1.0.11/pandamonium-1.0.11.tar.gz",
            }
        ],
    }

    verified = release_updater.verify_release_manifest(
        manifest, public_key_path=public_key, release=release
    )
    assert verified["artifact"]["url"].startswith("https://github.com/")

    manifest["commit"] = "3" * 40
    with pytest.raises(
        release_updater.UpdateError, match="signature verification failed"
    ):
        release_updater.verify_release_manifest(
            manifest, public_key_path=public_key, release=release
        )


def test_prerelease_versions_sort_before_their_stable_release():
    assert release_updater.version_tuple(
        "v1.0.11-rc.1"
    ) > release_updater.version_tuple("1.0.10")
    assert release_updater.version_tuple("1.0.11-rc.1") < release_updater.version_tuple(
        "1.0.11"
    )


def test_release_asset_redirects_remain_on_allowlisted_hosts():
    def handler(request):
        if request.url.host == "github.com":
            return httpx.Response(
                302,
                headers={
                    "location": "https://release-assets.githubusercontent.com/proof/manifest"
                },
            )
        return httpx.Response(200, content=b'{"ok": true}')

    with httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=False
    ) as client:
        content = release_updater._get_release_asset(
            client,
            "https://github.com/MADPANDA3D/Pandamonium/releases/download/v1/proof",
            accept="application/json",
            max_bytes=1024,
        )
    assert json.loads(content) == {"ok": True}


def test_release_asset_redirect_to_untrusted_host_fails_closed():
    def handler(_request):
        return httpx.Response(302, headers={"location": "https://example.com/proof"})

    with (
        httpx.Client(
            transport=httpx.MockTransport(handler), follow_redirects=False
        ) as client,
        pytest.raises(release_updater.UpdateError, match="allowed GitHub"),
    ):
        release_updater._get_release_asset(
            client,
            "https://github.com/MADPANDA3D/Pandamonium/releases/download/v1/proof",
            accept="application/json",
            max_bytes=1024,
        )


def test_successful_update_noop_and_manual_rollback_preserve_data(
    tmp_path, monkeypatch
):
    executor, manifest, archive, old = _layout(tmp_path, monkeypatch)
    events = _stub_runtime(executor, monkeypatch)

    result = executor.apply(manifest, archive)

    assert result["status"] == "succeeded"
    assert (
        executor.current_link.resolve()
        == executor.releases_dir / f"1.0.11-{NEW_COMMIT[:8]}"
    )
    assert (executor.config.data_dir / "owner.txt").read_text(
        encoding="utf-8"
    ) == "preserved"
    assert events == ["stop", "start"]
    assert (
        release_updater.UpdateExecutor(executor.config).apply(manifest, archive)[
            "status"
        ]
        == "current"
    )

    (executor.config.data_dir / "owner.txt").write_text(
        "created after update", encoding="utf-8"
    )
    rollback = release_updater.UpdateExecutor(executor.config)
    monkeypatch.setattr(rollback, "_service", lambda action: events.append(action))
    monkeypatch.setattr(rollback, "_healthy", lambda *_args, **_kwargs: True)
    rolled_back = rollback.rollback(
        {
            "previous_release": str(old),
            "backup_location": result["backup_location"],
            "data_restore_required": True,
        }
    )
    assert rolled_back["status"] == "rolled_back"
    assert rollback.current_link.resolve() == old
    assert (executor.config.data_dir / "owner.txt").read_text(
        encoding="utf-8"
    ) == "preserved"
    assert rolled_back["rollback_available"] is False


def test_manual_rollback_rejects_a_tampered_backup(tmp_path, monkeypatch):
    executor, manifest, archive, old = _layout(tmp_path, monkeypatch)
    _stub_runtime(executor, monkeypatch)
    result = executor.apply(manifest, archive)
    backup_archive = Path(result["backup_location"]) / "data.tar.gz"
    backup_archive.write_bytes(backup_archive.read_bytes() + b"tampered")
    rollback = release_updater.UpdateExecutor(executor.config)
    events = []
    monkeypatch.setattr(rollback, "_service", lambda action: events.append(action))

    with pytest.raises(release_updater.UpdateError, match="checksum verification"):
        rollback.rollback(
            {
                "previous_release": str(old),
                "backup_location": result["backup_location"],
                "data_restore_required": True,
            }
        )

    assert rollback.current_link.resolve() != old
    assert events == ["stop", "start"]


def test_manual_rollback_rejects_backup_outside_configured_store(tmp_path, monkeypatch):
    executor, _manifest, _archive, old = _layout(tmp_path, monkeypatch)
    outside = tmp_path / "owner-controlled"
    outside.mkdir()
    rollback = release_updater.UpdateExecutor(executor.config)
    events = []
    monkeypatch.setattr(rollback, "_service", lambda action: events.append(action))

    with pytest.raises(release_updater.UpdateError, match="configured backup store"):
        rollback.rollback(
            {
                "previous_release": str(old),
                "backup_location": str(outside),
                "data_restore_required": True,
            }
        )

    assert events == []


def test_dependency_runtime_is_published_only_after_install_succeeds(
    tmp_path, monkeypatch
):
    executor, manifest, _archive, _old = _layout(tmp_path, monkeypatch)
    current = executor.current_link.resolve()
    (current / "requirements.txt").write_text("old\n", encoding="utf-8")
    (current / "venv" / "bin").mkdir(parents=True)
    (current / "venv" / "bin" / "python").symlink_to(sys.executable)
    candidate = tmp_path / "candidate"
    (candidate / "src").mkdir(parents=True)
    (candidate / "SOURCE_REVISION").write_text(f"{NEW_COMMIT}\n", encoding="utf-8")
    (candidate / "src" / "constants.py").write_text(
        'APP_VERSION = "1.0.11"\n', encoding="utf-8"
    )
    (candidate / "requirements.txt").write_text(
        "httpx\ncryptography\n", encoding="utf-8"
    )
    commands = []

    def fail_install(args, **_kwargs):
        commands.append(args)
        if args[1:3] == ["-m", "venv"]:
            (Path(args[3]) / "bin").mkdir(parents=True)
            (Path(args[3]) / "bin" / "python").symlink_to(sys.executable)
            return
        raise release_updater.UpdateError("dependency install failed")

    monkeypatch.setattr(executor, "_run", fail_install)

    with pytest.raises(release_updater.UpdateError, match="dependency install failed"):
        executor._prepare_runtime(candidate, manifest)

    final_runtime = executor.venvs_dir / f"1.0.11-{NEW_COMMIT[:8]}"
    assert not final_runtime.exists()
    assert not list(executor.venvs_dir.glob(".*.staging-*"))
    assert len(commands) == 2


def test_root_updater_rejects_writable_runtime(tmp_path, monkeypatch):
    runtime = tmp_path / "venv"
    runtime.mkdir()
    runtime.chmod(0o775)
    monkeypatch.setattr(release_updater.os, "geteuid", lambda: 0)

    with pytest.raises(release_updater.UpdateError, match="root-owned and immutable"):
        release_updater.assert_immutable_tree(runtime, "release runtime")


def test_environment_config_requires_root(monkeypatch):
    monkeypatch.setattr(release_updater.os, "geteuid", lambda: 1000)

    with pytest.raises(release_updater.UpdateError, match="must run as root"):
        release_updater.UpdateConfig.from_env()


def test_failed_health_check_restores_release_and_backup_data(tmp_path, monkeypatch):
    executor, manifest, archive, old = _layout(tmp_path, monkeypatch)
    _stub_runtime(executor, monkeypatch, healthy=False)
    original_migrate = executor._migrate

    def mutate_live(candidate, data):
        original_migrate(candidate, data)
        if data == executor.config.data_dir:
            (data / "owner.txt").write_text("migrated", encoding="utf-8")

    monkeypatch.setattr(executor, "_migrate", mutate_live)

    with pytest.raises(release_updater.UpdateError, match="health check"):
        executor.apply(manifest, archive)

    assert executor.current_link.resolve() == old
    assert (executor.config.data_dir / "owner.txt").read_text(
        encoding="utf-8"
    ) == "preserved"
    state = release_updater.read_update_state()
    assert state["status"] == "failed"
    assert state["auto_rolled_back"] is True


def test_migration_failure_never_switches_live_release(tmp_path, monkeypatch):
    executor, manifest, archive, old = _layout(tmp_path, monkeypatch)
    events = _stub_runtime(executor, monkeypatch, migration_error=True)

    with pytest.raises(release_updater.UpdateError, match="migration rehearsal failed"):
        executor.apply(manifest, archive)

    assert executor.current_link.resolve() == old
    assert "stop" not in events
    assert (executor.config.data_dir / "owner.txt").read_text(
        encoding="utf-8"
    ) == "preserved"


def test_interrupted_atomic_switch_automatically_restores_previous_release(
    tmp_path, monkeypatch
):
    executor, manifest, archive, old = _layout(tmp_path, monkeypatch)
    _stub_runtime(executor, monkeypatch)
    real_switch = executor._atomic_switch
    calls = 0

    def interrupt_once(target):
        nonlocal calls
        calls += 1
        real_switch(target)
        if calls == 1:
            raise KeyboardInterrupt

    monkeypatch.setattr(executor, "_atomic_switch", interrupt_once)
    with pytest.raises(KeyboardInterrupt):
        executor.apply(manifest, archive)

    assert executor.current_link.resolve() == old
    assert release_updater.read_update_state()["auto_rolled_back"] is True


@pytest.mark.parametrize(
    "legacy", [False, True], ids=["clean-install", "legacy-upgrade"]
)
def test_database_migration_entrypoint_is_idempotent(tmp_path, legacy):
    install = tmp_path / "install"
    candidate = tmp_path / "candidate"
    core = candidate / "core"
    (candidate / "venv" / "bin").mkdir(parents=True)
    core.mkdir()
    (core / "__init__.py").write_text("", encoding="utf-8")
    (core / "database.py").write_text(
        "import os, sqlite3\n"
        "p=os.path.join(os.environ['ODYSSEUS_DATA_DIR'],'app.db')\n"
        "c=sqlite3.connect(p)\n"
        "c.execute('CREATE TABLE IF NOT EXISTS update_probe (id INTEGER PRIMARY KEY, value TEXT)')\n"
        "c.commit(); c.close()\n",
        encoding="utf-8",
    )
    (candidate / "venv" / "bin" / "python").symlink_to(sys.executable)
    data = tmp_path / "data"
    data.mkdir()
    if legacy:
        with sqlite3.connect(data / "app.db") as connection:
            connection.execute("CREATE TABLE legacy_owner (name TEXT)")
            connection.execute("INSERT INTO legacy_owner VALUES ('Leo')")
    executor = release_updater.UpdateExecutor(
        release_updater.UpdateConfig(
            install,
            data,
            tmp_path / "backups",
            "pandamonium.service",
            "http://127.0.0.1:7000",
        )
    )

    executor._migrate(candidate, data)

    with sqlite3.connect(data / "app.db") as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name='update_probe'"
        ).fetchone()
        if legacy:
            assert connection.execute("SELECT name FROM legacy_owner").fetchone() == (
                "Leo",
            )


def test_release_archive_rejects_parent_traversal(tmp_path):
    archive = tmp_path / "bad.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        payload = b"bad"
        info = tarfile.TarInfo("pandamonium/../outside")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    executor = release_updater.UpdateExecutor(
        release_updater.UpdateConfig(
            tmp_path / "install",
            tmp_path / "data",
            tmp_path / "backups",
            "pandamonium.service",
            "http://127.0.0.1:7000",
        )
    )

    with pytest.raises(release_updater.UpdateError, match="unsafe path"):
        executor._extract_release(archive, tmp_path / "extract")

    assert not (tmp_path / "outside").exists()


def test_release_archive_strips_group_and_other_write_bits(tmp_path):
    archive = tmp_path / "writable.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for name, mode in (
            ("pandamonium/config.py", 0o664),
            ("pandamonium/run", 0o775),
        ):
            payload = b"proof\n"
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mode = mode
            tar.addfile(info, io.BytesIO(payload))
    executor = release_updater.UpdateExecutor(
        release_updater.UpdateConfig(
            tmp_path / "install",
            tmp_path / "data",
            tmp_path / "backups",
            "pandamonium.service",
            "http://127.0.0.1:7000",
        )
    )

    previous_umask = os.umask(0o077)
    try:
        extracted = executor._extract_release(archive, tmp_path / "extract")
    finally:
        os.umask(previous_umask)

    assert extracted.stat().st_mode & 0o777 == 0o755
    assert extracted.joinpath("config.py").stat().st_mode & 0o777 == 0o644
    assert extracted.joinpath("run").stat().st_mode & 0o777 == 0o755
