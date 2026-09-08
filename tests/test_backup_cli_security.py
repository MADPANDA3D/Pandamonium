import io
import json
import os
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.helpers.cli_loader import load_script


def _load_backup_cli():
    return load_script("odysseus-backup")


def _patch_repo(module, monkeypatch, root: Path):
    monkeypatch.setattr(module, "_REPO_ROOT", root)
    monkeypatch.setattr(module, "_DATA_DIR", root / "data")


def _restore_args(path: Path):
    return SimpleNamespace(path=str(path), yes=True, pretty=False)


def _verify_args(path: Path):
    return SimpleNamespace(path=str(path), pretty=False)


def _snapshot_args(path: Path):
    return SimpleNamespace(
        out=str(path),
        include_research=True,
        include_attachments=True,
        pretty=False,
    )


def test_backup_entry_skips_files_that_disappear():
    backup = _load_backup_cli()

    class Vanished:
        name = "gone.tar.gz"

        def is_file(self):
            return True

        def stat(self):
            raise FileNotFoundError("gone")

        def __str__(self):
            return "backups/gone.tar.gz"

    assert backup._backup_entry(Vanished()) is None


def test_backup_list_sorts_by_captured_mtime(monkeypatch):
    backup = _load_backup_cli()
    first = SimpleNamespace(name="older.tar.gz")
    second = SimpleNamespace(name="newer.tar.gz")
    monkeypatch.setattr(
        backup,
        "_BACKUP_DIR",
        SimpleNamespace(
            is_dir=lambda: True,
            iterdir=lambda: [first, second],
        ),
    )
    monkeypatch.setattr(
        backup,
        "_backup_entry",
        lambda p: {
            "name": p.name,
            "modified": "2026-10-25T01:45:00" if p is first else "2026-10-25T01:15:00",
            "_mtime": 100 if p is first else 200,
        },
    )
    seen = []
    monkeypatch.setattr(backup, "emit", lambda payload, args: seen.append(payload))

    backup.cmd_list(SimpleNamespace(pretty=False))

    assert [entry["name"] for entry in seen[0]] == ["newer.tar.gz", "older.tar.gz"]
    assert all("_mtime" not in entry for entry in seen[0])


def test_snapshot_rejects_output_inside_data_dir(tmp_path, monkeypatch):
    backup = _load_backup_cli()
    repo = tmp_path / "repo"
    data = repo / "data"
    data.mkdir(parents=True)
    _patch_repo(backup, monkeypatch, repo)

    with pytest.raises(SystemExit):
        backup._reject_output_inside_data(data / "self.tar.gz")


def test_restore_rejects_symlink_escape(tmp_path, monkeypatch):
    backup = _load_backup_cli()
    repo = tmp_path / "repo"
    data = repo / "data"
    outside = tmp_path / "outside"
    data.mkdir(parents=True)
    outside.mkdir()
    (data / "keep.txt").write_text("still here", encoding="utf-8")
    _patch_repo(backup, monkeypatch, repo)

    tar_path = tmp_path / "malicious.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        data_dir = tarfile.TarInfo("data")
        data_dir.type = tarfile.DIRTYPE
        tar.addfile(data_dir)

        link = tarfile.TarInfo("data/link")
        link.type = tarfile.SYMTYPE
        link.linkname = str(outside)
        tar.addfile(link)

        payload = b"escaped"
        escaped = tarfile.TarInfo("data/link/pwned.txt")
        escaped.size = len(payload)
        tar.addfile(escaped, io.BytesIO(payload))

    with pytest.raises(SystemExit):
        backup.cmd_restore(_restore_args(tar_path))

    assert not (outside / "pwned.txt").exists()
    assert (data / "keep.txt").read_text(encoding="utf-8") == "still here"


def test_verify_rejects_symlink_escape(tmp_path):
    backup = _load_backup_cli()

    tar_path = tmp_path / "malicious.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        link = tarfile.TarInfo("data/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "/tmp"
        tar.addfile(link)

    with pytest.raises(SystemExit):
        backup.cmd_verify(_verify_args(tar_path))


def test_restore_rejects_hardlink_entries(tmp_path, monkeypatch):
    backup = _load_backup_cli()
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    _patch_repo(backup, monkeypatch, repo)

    tar_path = tmp_path / "hardlink.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        link = tarfile.TarInfo("data/hardlink")
        link.type = tarfile.LNKTYPE
        link.linkname = "../outside.txt"
        tar.addfile(link)

    with pytest.raises(SystemExit):
        backup.cmd_restore(_restore_args(tar_path))


def test_restore_extracts_regular_files_without_extractall(tmp_path, monkeypatch):
    backup = _load_backup_cli()
    repo = tmp_path / "repo"
    data = repo / "data"
    data.mkdir(parents=True)
    (data / "old.txt").write_text("old", encoding="utf-8")
    _patch_repo(backup, monkeypatch, repo)

    tar_path = tmp_path / "valid.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        folder = tarfile.TarInfo("data/nested")
        folder.type = tarfile.DIRTYPE
        tar.addfile(folder)

        payload = b"new"
        item = tarfile.TarInfo("data/nested/new.txt")
        item.size = len(payload)
        tar.addfile(item, io.BytesIO(payload))

    backup.cmd_restore(_restore_args(tar_path))

    assert (repo / "data" / "nested" / "new.txt").read_text(encoding="utf-8") == "new"
    assert not (repo / "data" / "old.txt").exists()
    assert list(repo.glob("data.before-restore-*"))


def test_snapshot_manifest_and_verify_sidecar_record_recovery_evidence(
    tmp_path, monkeypatch
):
    backup = _load_backup_cli()
    repo = tmp_path / "repo"
    data = repo / "data"
    data.mkdir(parents=True)
    (data / "state.json").write_text('{"ok": true}', encoding="utf-8")
    _patch_repo(backup, monkeypatch, repo)
    archive = tmp_path / "snapshot.tar.gz"
    emitted = []
    monkeypatch.setattr(backup, "emit", lambda payload, args: emitted.append(payload))
    args = SimpleNamespace(
        out=str(archive),
        include_research=False,
        include_attachments=False,
        pretty=False,
    )

    backup.cmd_snapshot(args)
    snapshot = emitted.pop()
    assert snapshot["schema"] == "jos-p7.backup.v2"
    assert snapshot["integrity"]["verified"] is False
    assert snapshot["integrity"]["sha256"]
    with tarfile.open(archive, "r:gz") as tar:
        manifest = json.loads(
            tar.extractfile("data/.pandamonium-backup-manifest.json").read()
        )
    assert manifest["scope"] == "pandamonium canonical data directory"
    assert "data/deep_research" in manifest["exclusions"]
    assert "qdrant" in manifest["external_vectors"]
    assert manifest["inventory"]["schema"] == "pandamonium.backup-inventory.v1"
    assert manifest["inventory"]["file_count"] == 1

    backup.cmd_verify(_verify_args(archive))
    proof = emitted.pop()
    assert proof["ok"] is True
    assert proof["inventory"]["verified"] is True
    assert proof["proof_recorded"] is True
    assert Path(str(archive) + ".verified.json").exists()


def test_external_data_root_round_trips_without_restoring_into_source(
    tmp_path, monkeypatch
):
    backup = _load_backup_cli()
    repo = tmp_path / "release"
    repo.mkdir()
    data = tmp_path / "persistent" / "data"
    data.mkdir(parents=True)
    (data / "owner.txt").write_text("before", encoding="utf-8")
    monkeypatch.setattr(backup, "_REPO_ROOT", repo)
    monkeypatch.setattr(backup, "_DATA_DIR", data)
    archive = tmp_path / "snapshot.tar.gz"
    emitted = []
    monkeypatch.setattr(backup, "emit", lambda payload, args: emitted.append(payload))

    backup.cmd_snapshot(
        SimpleNamespace(
            out=str(archive),
            include_research=True,
            include_attachments=True,
            pretty=False,
        )
    )
    (data / "owner.txt").write_text("after", encoding="utf-8")
    backup.cmd_restore(_restore_args(archive))

    assert (data / "owner.txt").read_text(encoding="utf-8") == "before"
    assert not (repo / "data").exists()
    assert list(data.parent.glob("data.before-restore-*"))


def test_internal_file_symlink_is_materialized_and_verified_on_restore(
    tmp_path, monkeypatch
):
    backup = _load_backup_cli()
    repo = tmp_path / "repo"
    data = repo / "data"
    blob = data / "fastembed_cache" / "models" / "blobs" / "model.bin"
    blob.parent.mkdir(parents=True)
    blob.write_bytes(b"protected-model-weights")
    snapshot_file = (
        data / "fastembed_cache" / "models" / "snapshots" / "revision" / "model.bin"
    )
    snapshot_file.parent.mkdir(parents=True)
    snapshot_file.symlink_to("../../blobs/model.bin")
    _patch_repo(backup, monkeypatch, repo)
    archive = tmp_path / "snapshot.tar.gz"
    emitted = []
    monkeypatch.setattr(backup, "emit", lambda payload, args: emitted.append(payload))

    backup.cmd_snapshot(_snapshot_args(archive))

    with tarfile.open(archive, "r:gz") as tar:
        archived_link = tar.getmember(
            "data/fastembed_cache/models/snapshots/revision/model.bin"
        )
        assert archived_link.isfile()
        assert not archived_link.issym()
        assert tar.extractfile(archived_link).read() == b"protected-model-weights"
        manifest = json.loads(tar.extractfile(backup._MANIFEST_NAME).read())
    inventory = {item["path"]: item for item in manifest["inventory"]["files"]}
    link_entry = inventory[
        "data/fastembed_cache/models/snapshots/revision/model.bin"
    ]
    assert link_entry["source"] == "materialized_internal_file_symlink"
    assert link_entry["sha256"]

    backup.cmd_verify(_verify_args(archive))
    assert emitted[-1]["inventory"]["verified"] is True
    blob.write_bytes(b"changed")
    snapshot_file.unlink()
    backup.cmd_restore(_restore_args(archive))

    restored = (
        data
        / "fastembed_cache"
        / "models"
        / "snapshots"
        / "revision"
        / "model.bin"
    )
    assert restored.is_file()
    assert not restored.is_symlink()
    assert restored.read_bytes() == b"protected-model-weights"


@pytest.mark.parametrize(
    "unsafe_kind",
    [
        "dangling",
        "external_absolute",
        "external_escape",
        "directory",
        "chained",
        "special",
    ],
)
def test_snapshot_fails_closed_for_unsafe_data_symlinks(
    tmp_path, monkeypatch, unsafe_kind
):
    backup = _load_backup_cli()
    repo = tmp_path / "repo"
    data = repo / "data"
    data.mkdir(parents=True)
    internal = data / "target.bin"
    internal.write_bytes(b"safe")
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside")
    link = data / "model.bin"
    if unsafe_kind == "dangling":
        link.symlink_to("missing.bin")
    elif unsafe_kind == "external_absolute":
        link.symlink_to(outside)
    elif unsafe_kind == "external_escape":
        link.symlink_to(os.path.relpath(outside, link.parent))
    elif unsafe_kind == "directory":
        directory = data / "models"
        directory.mkdir()
        link.symlink_to(directory)
    elif unsafe_kind == "chained":
        chained = data / "current.bin"
        chained.symlink_to(internal.name)
        link.symlink_to(chained.name)
    else:
        special = data / "pipe"
        os.mkfifo(special)
        link.symlink_to(special.name)
    _patch_repo(backup, monkeypatch, repo)
    archive = tmp_path / f"{unsafe_kind}.tar.gz"

    with pytest.raises(SystemExit):
        backup.cmd_snapshot(_snapshot_args(archive))

    assert not archive.exists()


def test_inventory_digest_blocks_corrupt_restore_before_live_data_swap(
    tmp_path, monkeypatch
):
    backup = _load_backup_cli()
    repo = tmp_path / "repo"
    data = repo / "data"
    data.mkdir(parents=True)
    state = data / "state.bin"
    state.write_bytes(b"good")
    _patch_repo(backup, monkeypatch, repo)
    archive = tmp_path / "snapshot.tar.gz"
    corrupt = tmp_path / "corrupt.tar.gz"
    backup.cmd_snapshot(_snapshot_args(archive))

    with tarfile.open(archive, "r:gz") as source, tarfile.open(
        corrupt, "w:gz"
    ) as target:
        for member in source.getmembers():
            if member.name == "data/state.bin":
                target.addfile(member, io.BytesIO(b"evil"))
            elif member.isfile():
                target.addfile(member, source.extractfile(member))
            else:
                target.addfile(member)

    with pytest.raises(SystemExit):
        backup.cmd_verify(_verify_args(corrupt))

    state.write_bytes(b"live")
    with pytest.raises(SystemExit):
        backup.cmd_restore(_restore_args(corrupt))
    assert state.read_bytes() == b"live"
    assert not list(repo.glob("data.before-restore-*"))


def test_inventory_rejects_an_unrecorded_archive_member(tmp_path, monkeypatch):
    backup = _load_backup_cli()
    repo = tmp_path / "repo"
    data = repo / "data"
    data.mkdir(parents=True)
    (data / "state.bin").write_bytes(b"good")
    _patch_repo(backup, monkeypatch, repo)
    archive = tmp_path / "snapshot.tar.gz"
    expanded = tmp_path / "expanded.tar.gz"
    backup.cmd_snapshot(_snapshot_args(archive))

    with tarfile.open(archive, "r:gz") as source, tarfile.open(
        expanded, "w:gz"
    ) as target:
        for member in source.getmembers():
            if member.isfile():
                target.addfile(member, source.extractfile(member))
            else:
                target.addfile(member)
        payload = b"not-in-inventory"
        extra = tarfile.TarInfo("data/unrecorded.bin")
        extra.size = len(payload)
        target.addfile(extra, io.BytesIO(payload))

    with pytest.raises(SystemExit):
        backup.cmd_verify(_verify_args(expanded))
