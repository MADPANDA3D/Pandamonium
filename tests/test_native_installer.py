"""Native snapshot isolation, interrupted-install safety and upgrade rollback."""

import importlib.util
import json
import subprocess
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import pytest

SPEC = importlib.util.spec_from_file_location(
    "native_installer", Path(__file__).parents[1] / "scripts/install-native-linux.py"
)
native = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(native)


def repository(tmp_path):
    repo = tmp_path / "source"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    (repo / "packaging/native-linux").mkdir(parents=True)
    (repo / "packaging/native-linux/requirements.txt").write_text("# fixture\n")
    (repo / "app.py").write_text("# first version\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", "test: snapshot"], check=True
    )
    revision = native.output(["git", "-C", str(repo), "rev-parse", "HEAD"])
    return repo, revision


def test_snapshot_excludes_credentials_and_exports_exact_committed_content(tmp_path):
    repo, revision = repository(tmp_path)
    (repo / ".env").write_text("PRIVATE=value")
    (repo / "app.py").write_text("# unrelated dirty edits\n")
    dest = tmp_path / "snapshot"
    dest.mkdir()
    native.export_snapshot(repo, revision, dest)
    assert not (dest / ".env").exists()
    assert (dest / "app.py").read_text() == "# first version\n"
    assert (dest / "SOURCE_REVISION").read_text().strip() == revision
    assert not (dest / ".git").exists()


def test_directory_guard_rejects_symlink_into_another_install(tmp_path):
    real = tmp_path / "unrelated"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(RuntimeError, match="without symlinks"):
        native.safe_directory(link / "data")
    assert not (real / "data").exists()


@pytest.mark.parametrize(
    "failure", ["startup", "backup", "stop", "restore", "readiness"]
)
def test_failed_upgrade_restores_previous_app_data_and_launcher(
    tmp_path, monkeypatch, failure
):
    repo, revision = repository(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(native.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(native.shutil, "which", lambda command: "/usr/bin/" + command)
    monkeypatch.setattr(
        native.shutil, "disk_usage", lambda path: Namespace(free=20 * 1024**3)
    )
    root = home / ".local/share/pandamonium"
    previous = root / "versions/previous"
    previous.mkdir(parents=True)
    (root / "current").symlink_to(previous, target_is_directory=True)
    data = root / "data"
    data.mkdir()
    (data / "retained.txt").write_text("existing user data")
    (root / "installation.json").write_text(json.dumps({"previous": "before"}))
    native.atomic_json(
        root / "owner.json",
        {
            "schema": "pandamonium.native-source.v1",
            "uid": native.os.getuid(),
            "root": str(root),
        },
    )
    launcher = home / ".local/bin/pandamonium-desktop"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("existing launcher")
    launcher.chmod(0o755)
    calls = []
    active = True

    def runner(argv, **kwargs):
        nonlocal active
        calls.append(argv)
        if failure == "readiness" and "-c" in argv and "check_readiness" in argv[-1]:
            raise subprocess.CalledProcessError(1, argv)
        if argv[:4] == ["systemctl", "--user", "stop", native.UNIT]:
            if failure == "stop":
                raise subprocess.CalledProcessError(1, argv)
            active = False
        if argv[:4] == ["systemctl", "--user", "start", native.UNIT]:
            active = True
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(native, "run", runner)

    def failed_health(*args):
        if failure == "restore":
            (data / "retained.txt").write_text("failed candidate data")
        return failure == "readiness"

    monkeypatch.setattr(native, "health", failed_health)
    original = subprocess.run
    if failure == "backup":

        def fail_copy(*args, **kwargs):
            raise OSError("backup disk is full")

        monkeypatch.setattr(native.shutil, "copytree", fail_copy)
    if failure == "restore":
        copy = native.shutil.copytree

        def fail_restore(source, destination, *args, **kwargs):
            if Path(destination).name == "restored-data":
                raise OSError("restore disk is full")
            return copy(source, destination, *args, **kwargs)

        monkeypatch.setattr(native.shutil, "copytree", fail_restore)
    with patch.object(native.subprocess, "run", wraps=original) as proc:

        def fake_process(argv, **kwargs):
            nonlocal active
            if argv[0] == "systemctl":
                if "show" in argv:
                    state = "active" if active else "inactive"
                    pid = "123" if active else "0"
                    return subprocess.CompletedProcess(
                        argv, 0, stdout=f"ActiveState={state}\nMainPID={pid}\n"
                    )
                if "is-active" in argv:
                    return subprocess.CompletedProcess(argv, 0 if active else 3)
                if "stop" in argv:
                    active = False
                if "start" in argv:
                    active = True
                calls.append(argv)
                return subprocess.CompletedProcess(argv, 0)
            return original(argv, **kwargs)

        proc.side_effect = fake_process
        with pytest.raises((RuntimeError, OSError, subprocess.CalledProcessError)):
            native.install(
                Namespace(
                    source=str(repo),
                    revision=revision,
                    root=str(root),
                    check=False,
                    provision_runtime=False,
                )
            )
    if failure == "restore":
        assert not active
        assert (root / "recovery.json").exists()
        assert (data / "retained.txt").read_text() == "failed candidate data"
        assert (
            next((root / "backups").glob("*/data/retained.txt")).read_text()
            == "existing user data"
        )
        return
    assert not (root / "recovery.json").exists()
    assert (root / "current").resolve() == previous
    assert (data / "retained.txt").read_text() == "existing user data"
    assert launcher.read_text() == "existing launcher"
    assert launcher.stat().st_mode & 0o777 == 0o755
    assert json.loads((root / "installation.json").read_text()) == {
        "previous": "before"
    }
    assert active
    if failure in {"startup", "readiness"}:
        assert list((root / "backups").glob("*/failed-data/retained.txt"))
    else:
        assert not list((root / "backups").glob("*/failed-data"))


def test_launcher_uses_loopback_and_keeps_models_out_of_startup():
    unit = native.service_text(Path("/home/user/app"), Path("/home/user/config.env"))
    assert "--host 127.0.0.1" in unit
    assert "--workers 1" in unit
    assert "[Install]" not in unit
    assert "DATABASE_URL=sqlite:////home/user/app/data/app.db" in unit
    assert "PANDAMONIUM_DATA_DIR=/home/user/app/data" in unit
    assert "ODYSSEUS_DATA_DIR=/home/user/app/data" in unit


@pytest.mark.parametrize(
    "report",
    ["", "ActiveState=inactive\nMainPID=23", "ActiveState=deactivating\nMainPID=0"],
)
def test_unknown_or_live_service_state_never_authorizes_data_restore(
    monkeypatch, report
):
    monkeypatch.setattr(native, "output", lambda args: report)
    with pytest.raises(RuntimeError, match="stop was not confirmed"):
        native.require_stopped()


def test_ownership_marker_requires_exact_user_and_path(tmp_path):
    native.atomic_json(
        tmp_path / "owner.json",
        {
            "schema": "pandamonium.native-source.v1",
            "uid": native.os.getuid(),
            "root": str(tmp_path),
        },
    )
    assert native.owned_install(tmp_path)
    native.atomic_json(tmp_path / "owner.json", {"root": "/unrelated"})
    with pytest.raises(RuntimeError, match="ownership does not match"):
        native.owned_install(tmp_path)
    (tmp_path / "owner.json").unlink()
    (tmp_path / "owner.json").symlink_to(tmp_path / "outside")
    with pytest.raises(RuntimeError, match="symlink"):
        native.owned_install(tmp_path)


@pytest.mark.parametrize(
    "key", ["PANDAMONIUM_DATA_DIR", "ODYSSEUS_DATA_DIR", "DATABASE_URL"]
)
def test_external_data_configuration_is_refused(tmp_path, key):
    config = tmp_path / "native.env"
    config.write_text(f'{key}="/outside-this-install"\n')
    with pytest.raises(RuntimeError, match="outside this profile"):
        native.check_config(config, tmp_path / "data")


def test_atomic_metadata_failure_preserves_previous_marker(tmp_path, monkeypatch):
    marker = tmp_path / "installation.json"
    marker.write_text('{"previous": "retained"}\n')

    def full_disk(fd):
        raise OSError("disk is full")

    monkeypatch.setattr(native.os, "fsync", full_disk)
    with pytest.raises(OSError, match="disk is full"):
        native.atomic_json(marker, {"previous": "replacement"})
    assert json.loads(marker.read_text()) == {"previous": "retained"}
    assert list(tmp_path.iterdir()) == [marker]
