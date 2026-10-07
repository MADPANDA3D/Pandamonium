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


def test_failed_upgrade_restores_previous_app_data_and_launcher(tmp_path, monkeypatch):
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
    launcher = home / ".local/bin/pandamonium-desktop"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("existing launcher")
    launcher.chmod(0o755)
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(native, "run", runner)
    monkeypatch.setattr(native, "health", lambda *args: False)
    original = subprocess.run
    with patch.object(native.subprocess, "run", wraps=original) as proc:

        def fake_process(argv, **kwargs):
            if argv[0] == "systemctl":
                return subprocess.CompletedProcess(argv, 0)
            return original(argv, **kwargs)

        proc.side_effect = fake_process
        with pytest.raises(RuntimeError, match="Startup/version proof failed"):
            native.install(
                Namespace(
                    source=str(repo),
                    revision=revision,
                    root=str(root),
                    check=False,
                    provision_runtime=False,
                )
            )
    assert (root / "current").resolve() == previous
    assert (data / "retained.txt").read_text() == "existing user data"
    assert launcher.read_text() == "existing launcher"
    assert launcher.stat().st_mode & 0o777 == 0o755
    assert list((root / "backups").glob("*/failed-data/retained.txt"))


def test_launcher_uses_loopback_and_keeps_models_out_of_startup():
    unit = native.service_text(Path("/home/user/app"), Path("/home/user/config.env"))
    assert "--host 127.0.0.1" in unit
    assert "--workers 1" in unit
    assert "[Install]" not in unit
