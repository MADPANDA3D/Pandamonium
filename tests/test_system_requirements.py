import subprocess
from pathlib import Path

import pytest

import src.system_requirements as sr


def test_existing_image_is_never_a_formatting_plan(tmp_path):
    image = tmp_path / "runtime.img"
    image.write_bytes(b"seeded runtime state")
    commands = sr._runtime_filesystem_argv(
        tmp_path / "runtime", image, sr.RUNTIME_VOLUME_BYTES, "1000:1000"
    )
    assert not any(command[0] in {"truncate", "mkfs.ext4"} for command in commands)


@pytest.fixture
def filesystem_guard():
    # Execute the exact privileged payload, with its automatic entrypoint off.
    # Tests provide temporary fstab/storage paths and intercept host commands.
    namespace = {"__name__": "filesystem_guard_test"}
    exec(sr._RUNTIME_FILESYSTEM_SCRIPT, namespace)  # noqa: S102 - fixed first-party payload under test
    return namespace


def _guard_host(guard, monkeypatch, image, *, mounted=False, source_matches=True):
    commands = []
    state = {"mounted": mounted}

    def run(argv, **kwargs):
        commands.append(list(argv))
        if argv[0] == "mount":
            state["mounted"] = True
        return ""

    monkeypatch.setitem(guard, "run", run)
    monkeypatch.setitem(
        guard, "image_bounds", lambda path, *args: (2 * 1024**3, 131072)
    )
    monkeypatch.setitem(
        guard,
        "mounted_source",
        lambda mount: ("/dev/loop7", "ext4") if state["mounted"] else None,
    )
    monkeypatch.setitem(guard, "same_loop_image", lambda source, path: source_matches)
    monkeypatch.setitem(guard, "check_mount_capacity", lambda mount: None)
    return commands


def test_existing_unmounted_image_keeps_seeded_data(
    filesystem_guard, monkeypatch, tmp_path
):
    image = tmp_path / "runtime.img"
    seeded = b"existing package data that must survive interruption"
    image.write_bytes(seeded)
    mount = tmp_path / "runtime"
    fstab = tmp_path / "fstab"
    fstab.write_text("# unrelated mounts\n")
    commands = _guard_host(filesystem_guard, monkeypatch, image)
    filesystem_guard["main"](
        str(mount),
        str(image),
        str(sr.RUNTIME_VOLUME_BYTES),
        "131072",
        "1000:1000",
        fstab=fstab,
    )
    assert image.read_bytes() == seeded
    assert any(
        command[:3] == ["mount", "-o", "loop"]
        and command[3].startswith("/proc/self/fd/")
        and command[4] == str(mount)
        for command in commands
    )
    assert not any(command[0] == "mkfs.ext4" for command in commands)
    assert fstab.read_text().count(str(image)) == 1


def test_mounted_rerun_repairs_persistence_once(
    filesystem_guard, monkeypatch, tmp_path
):
    image = tmp_path / "runtime.img"
    image.write_bytes(b"keep")
    mount = tmp_path / "runtime"
    mount.mkdir()
    (mount / "package.db").write_text("keep")
    fstab = tmp_path / "fstab"
    fstab.write_text("")
    commands = _guard_host(filesystem_guard, monkeypatch, image, mounted=True)
    for _ in range(2):
        filesystem_guard["main"](
            str(mount),
            str(image),
            str(sr.RUNTIME_VOLUME_BYTES),
            "131072",
            "1000:1000",
            fstab=fstab,
        )
    assert image.read_bytes() == b"keep"
    assert (mount / "package.db").read_text() == "keep"
    assert fstab.read_text().count(str(image)) == 1
    assert not any(command[0] in {"mkfs.ext4", "mount"} for command in commands)


@pytest.mark.parametrize(
    "mismatch",
    [
        "occupied",
        "wrong_loop",
        "wrong_fs",
        "image_symlink",
        "mount_symlink",
        "parent_symlink",
        "oversized",
        "too_many_inodes",
        "fstab_conflict",
    ],
)
def test_filesystem_target_mismatch_fails_before_mutation(
    filesystem_guard, monkeypatch, tmp_path, mismatch
):
    image = tmp_path / "runtime.img"
    image.write_bytes(b"preserve")
    mount = tmp_path / "runtime"
    mount.mkdir()
    fstab = tmp_path / "fstab"
    fstab.write_text("")
    commands = _guard_host(
        filesystem_guard,
        monkeypatch,
        image,
        mounted=mismatch == "wrong_loop",
        source_matches=False,
    )
    if mismatch == "occupied":
        (mount / "unrelated.db").write_text("preserve")
    elif mismatch == "wrong_fs":
        monkeypatch.setitem(
            filesystem_guard,
            "image_bounds",
            lambda path: (_ for _ in ()).throw(ValueError("image is not ext4")),
        )
    elif mismatch == "image_symlink":
        image = tmp_path / "alias.img"
        image.symlink_to(tmp_path / "runtime.img")
    elif mismatch == "mount_symlink":
        mount = tmp_path / "alias"
        mount.symlink_to(tmp_path / "runtime", target_is_directory=True)
    elif mismatch == "parent_symlink":
        (tmp_path / "alias").symlink_to(tmp_path, target_is_directory=True)
        image = tmp_path / "alias" / "runtime.img"
    elif mismatch == "oversized":
        monkeypatch.setitem(
            filesystem_guard, "image_bounds", lambda path: (9 * 1024**3, 131072)
        )
    elif mismatch == "too_many_inodes":
        monkeypatch.setitem(
            filesystem_guard, "image_bounds", lambda path: (2 * 1024**3, 1000001)
        )
    elif mismatch == "fstab_conflict":
        fstab.write_text(f"/dev/other {mount} ext4 defaults 0 0\n")
    original_fstab = fstab.read_text()
    with pytest.raises(ValueError):
        filesystem_guard["main"](
            str(mount),
            str(image),
            str(sr.RUNTIME_VOLUME_BYTES),
            "131072",
            "1000:1000",
            fstab=fstab,
        )
    assert (tmp_path / "runtime.img").read_bytes() == b"preserve"
    assert fstab.read_text() == original_fstab
    assert commands == []


def test_new_image_has_explicit_bounded_inodes(filesystem_guard, monkeypatch, tmp_path):
    image = tmp_path / "runtime.img"
    mount = tmp_path / "runtime"
    fstab = tmp_path / "fstab"
    fstab.write_text("")
    commands = _guard_host(filesystem_guard, monkeypatch, image)
    filesystem_guard["main"](
        str(mount),
        str(image),
        str(sr.RUNTIME_VOLUME_BYTES),
        "131072",
        "1000:1000",
        fstab=fstab,
    )
    assert image.stat().st_size == sr.RUNTIME_VOLUME_BYTES
    assert any(
        command[:3] == ["mkfs.ext4", "-N", "131072"]
        and command[3].startswith("/proc/self/fd/")
        for command in commands
    )


def test_creation_race_never_formats_replacement(
    filesystem_guard, monkeypatch, tmp_path
):
    image = tmp_path / "runtime.img"
    mount = tmp_path / "runtime"
    fstab = tmp_path / "fstab"
    fstab.write_text("")
    commands = _guard_host(filesystem_guard, monkeypatch, image)
    real_open = filesystem_guard["os"].open

    def raced_open(path, flags, *args):
        if Path(path) == image and flags & filesystem_guard["os"].O_EXCL:
            image.write_bytes(b"arrived after preflight")
        return real_open(path, flags, *args)

    monkeypatch.setattr(filesystem_guard["os"], "open", raced_open)
    with pytest.raises(FileExistsError):
        filesystem_guard["main"](
            str(mount),
            str(image),
            str(sr.RUNTIME_VOLUME_BYTES),
            "131072",
            "1000:1000",
            fstab=fstab,
        )
    assert image.read_bytes() == b"arrived after preflight"
    assert commands == []
    assert fstab.read_text() == ""


def test_new_image_path_swap_formats_only_exclusive_descriptor(
    filesystem_guard, monkeypatch, tmp_path
):
    image = tmp_path / "runtime.img"
    mount = tmp_path / "runtime"
    fstab = tmp_path / "fstab"
    fstab.write_text("")
    _guard_host(filesystem_guard, monkeypatch, image)

    def replace_before_format(argv, *, pass_fds=()):
        if argv[0] == "mkfs.ext4":
            image.rename(tmp_path / "exclusive-new.img")
            image.write_bytes(b"existing replacement data")
            assert argv[-1] == f"/proc/self/fd/{pass_fds[0]}"
            assert Path(argv[-1]).stat().st_ino != image.stat().st_ino
        return ""

    monkeypatch.setitem(filesystem_guard, "run", replace_before_format)
    with pytest.raises(ValueError, match="image path changed"):
        filesystem_guard["main"](
            str(mount),
            str(image),
            str(sr.RUNTIME_VOLUME_BYTES),
            "131072",
            "1000:1000",
            fstab=fstab,
        )
    assert image.read_bytes() == b"existing replacement data"
    assert fstab.read_text() == ""


@pytest.mark.parametrize(
    "filesystem,capacity,inodes",
    [
        ("ext4", 2 * 1024**3, 131072),
        ("ext3", 2 * 1024**3, 131072),
        ("ext4", 9 * 1024**3, 131072),
        ("ext4", 2 * 1024**3, 1000001),
    ],
)
def test_existing_image_metadata_requires_ext4_and_both_bounds(
    filesystem_guard, monkeypatch, tmp_path, filesystem, capacity, inodes
):
    image = tmp_path / "runtime.img"
    with image.open("wb") as volume:
        volume.truncate(capacity)

    def metadata(argv, **kwargs):
        if argv[0] == "blkid":
            return filesystem
        assert argv[:2] == ["dumpe2fs", "-h"]
        return f"Block count: {capacity // 4096}\nBlock size: 4096\nInode count: {inodes}\n"

    monkeypatch.setitem(filesystem_guard, "run", metadata)
    if filesystem == "ext4" and capacity <= 8 * 1024**3 and inodes <= 1000000:
        assert filesystem_guard["image_bounds"](image) == (capacity, inodes)
    else:
        with pytest.raises(ValueError):
            filesystem_guard["image_bounds"](image)


@pytest.mark.parametrize("controllers", ["cpu memory pids", "cpu pids", ""])
def test_systemd_probe_reads_real_user_manager_and_controllers(
    monkeypatch, tmp_path, controllers
):
    group = tmp_path / "manager"
    group.mkdir()
    for name, value in {
        "cgroup.controllers": controllers,
        "cgroup.subtree_control": controllers,
        "memory.max": "max",
        "memory.swap.max": "max",
        "pids.max": "max",
        "cpu.max": "max 100000",
    }.items():
        (group / name).write_text(value)
    monkeypatch.setattr(
        sr, "Path", lambda raw: tmp_path if raw == "/sys/fs/cgroup" else Path(raw)
    )
    monkeypatch.setattr(sr, "_which", lambda name: True)
    calls = []

    def systemctl(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "/manager\n", "")

    monkeypatch.setattr(sr.subprocess, "run", systemctl)
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
    present, detail = sr._probe_systemd_user()
    assert present is (controllers == "cpu memory pids")
    assert bool(detail) is not present
    assert calls == [
        ["systemctl", "--user", "show", "--property=ControlGroup", "--value"]
    ]


def test_systemd_session_variables_do_not_substitute_for_manager(monkeypatch):
    monkeypatch.setattr(sr, "_which", lambda name: True)
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")

    def unavailable(argv, **kwargs):
        raise subprocess.CalledProcessError(1, argv)

    monkeypatch.setattr(sr.subprocess, "run", unavailable)
    assert sr._probe_systemd_user()[0] is False


@pytest.mark.parametrize(
    "capacity,inodes,separate_device,expected",
    [
        (2 * 1024**3, 131072, True, True),
        (9 * 1024**3, 131072, True, False),
        (2 * 1024**3, 1000001, True, False),
        (2 * 1024**3, 131072, False, False),
        (0, 131072, True, False),
    ],
)
def test_runtime_probe_matches_storage_admission(
    monkeypatch, tmp_path, capacity, inodes, separate_device, expected
):
    import os
    from types import SimpleNamespace

    from src.extension_installer import ExtensionLifecycleError
    from src.extension_resources import storage_root

    root = tmp_path / "runtime"
    root.mkdir()
    monkeypatch.setenv("ODYSSEUS_EXTENSION_RUNTIME_ROOT", str(root))
    monkeypatch.setattr(sr.os.path, "ismount", lambda path: True)
    monkeypatch.setattr(
        sr.os,
        "statvfs",
        lambda path: SimpleNamespace(
            f_blocks=capacity // 4096, f_frsize=4096, f_files=inodes
        ),
    )
    real_stat = Path.stat

    def device_stat(path, *args, **kwargs):
        metadata = list(real_stat(path, *args, **kwargs))
        metadata[2] = 2 if path == root and separate_device else 1
        return os.stat_result(metadata)

    monkeypatch.setattr(Path, "stat", device_stat)
    assert sr._probe_runtime_filesystem()[0] is expected
    if expected:
        assert storage_root() == root
    else:
        with pytest.raises(ExtensionLifecycleError):
            storage_root()


def test_present_capabilities_have_no_missing_detail(monkeypatch):
    monkeypatch.setattr(sr, "_which", lambda name: True)
    monkeypatch.setattr(sr, "distro_family", lambda: "debian")
    result = sr.report(
        [
            "sandbox.bubblewrap",
            "sandbox.prlimit",
            "build.c_toolchain",
            "build.pkg_config",
        ]
    )
    assert all(row["present"] and row["detail"] == "" for row in result["capabilities"])


def test_successful_commands_do_not_hide_failed_readback(monkeypatch):
    _patch_host(monkeypatch, present=())
    with pytest.raises(sr.SystemRequirementError) as exc:
        sr.provision(["systemd.user"], runner=_Runner())
    assert exc.value.code == "system_requirements_provision_failed"
    assert "systemd.user" in exc.value.detail


def test_present_image_still_gets_persistence_repair(monkeypatch, tmp_path):
    _patch_host(monkeypatch, present=("runtime_filesystem",))
    image = tmp_path / "runtime.img"
    image.write_bytes(b"keep")
    runner = _Runner()
    sr.provision(
        ["runtime.filesystem"], image=image, mount=tmp_path / "runtime", runner=runner
    )
    assert any(command[2:4] == ["python3", "-c"] for command, _ in runner.calls)


def _probe(present: bool):
    return lambda: (present, "" if present else "missing")


def _patch_host(monkeypatch, *, family="debian", container=False, present=()):
    monkeypatch.setattr(sr, "distro_family", lambda: family)
    monkeypatch.setattr(sr, "is_container", lambda: container)
    monkeypatch.setattr(
        sr,
        "_PROBES",
        {
            name: _probe(name in present)
            for name in (
                "bwrap",
                "prlimit",
                "systemd_user",
                "runtime_filesystem",
                "cc",
                "pkg_config",
                "pkg_config_mpv",
            )
        },
    )


def test_distro_family_reads_os_release(monkeypatch):
    monkeypatch.setattr(
        sr, "_read_os_release", lambda: {"ID": "ubuntu", "ID_LIKE": "debian"}
    )
    assert sr.distro_family() == "debian"
    monkeypatch.setattr(
        sr, "_read_os_release", lambda: {"ID": "endeavouros", "ID_LIKE": "arch"}
    )
    assert sr.distro_family() == "arch"
    monkeypatch.setattr(sr, "_read_os_release", lambda: {"ID": "gentoo"})
    assert sr.distro_family() == "unknown"


def test_is_container_uses_env(monkeypatch):
    monkeypatch.delenv("container", raising=False)
    monkeypatch.setattr(sr.Path, "exists", lambda self: False)
    assert sr.is_container() is False
    monkeypatch.setenv("container", "docker")
    assert sr.is_container() is True


def test_declared_capabilities_rejects_unknown():
    manifest = {"metadata": {"host_requirements": {"capabilities": ["media.libmpv"]}}}
    assert sr.declared_capabilities(manifest) == ["media.libmpv"]
    bad = {
        "metadata": {"host_requirements": {"capabilities": ["apt.install.anything"]}}
    }
    with pytest.raises(sr.SystemRequirementError) as exc:
        sr.declared_capabilities(bad)
    assert exc.value.code == "system_requirement_unknown_capability"


def test_capabilities_for_package_adds_sandbox_for_service():
    manifest = {
        "runtime": {"type": "service"},
        "metadata": {
            "host_requirements": {"capabilities": ["media.libmpv", "build.pkg_config"]}
        },
    }
    caps = sr.capabilities_for_package(manifest)
    assert set(sr.SANDBOX_CAPABILITIES) <= set(caps)
    assert {"media.libmpv", "build.pkg_config"} <= set(caps)
    assert sr.capabilities_for_package({"runtime": {"type": "web"}}) == []


def test_package_names_maps_and_rejects():
    assert sr.package_names(["sandbox.bubblewrap", "sandbox.prlimit"], "debian") == [
        "bubblewrap",
        "util-linux",
    ]
    assert sr.package_names(["media.libmpv"], "fedora") == ["mpv-libs-devel"]
    with pytest.raises(sr.SystemRequirementError):
        sr.package_names(["not.a.capability"], "debian")


def test_report_marks_missing_and_blocks_container(monkeypatch):
    _patch_host(monkeypatch, family="debian", container=True, present=())
    result = sr.report(["sandbox.bubblewrap", "runtime.filesystem"])
    assert result["missing"] == ["sandbox.bubblewrap", "runtime.filesystem"]
    assert result["auto_provisionable"] is False
    assert result["blocked_reason"] == "system_requirements_container_unsupported"
    assert result["packages"] == ["bubblewrap", "e2fsprogs", "util-linux"]

    _patch_host(monkeypatch, family="unknown", container=False, present=())
    assert sr.report(["sandbox.bubblewrap"])["blocked_reason"] == (
        "system_requirements_distro_unsupported"
    )


def test_report_present_capability_is_not_missing(monkeypatch):
    _patch_host(monkeypatch, present=("bwrap", "prlimit"))
    result = sr.report(["sandbox.bubblewrap", "sandbox.prlimit"])
    assert result["missing"] == []
    assert result["auto_provisionable"] is False
    assert sr.installed(["sandbox.bubblewrap"]) is True


class _Runner:
    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on

    def __call__(self, argv, stdin_bytes):
        self.calls.append((list(argv), stdin_bytes))
        code = 0
        if self.fail_on and self.fail_on in argv:
            code = 100
        return subprocess.CompletedProcess(list(argv), code, b"", b"")


def test_provision_pipes_password_only_to_stdin(monkeypatch, tmp_path):
    _patch_host(monkeypatch, family="debian", container=False, present=())
    runner = _Runner()
    mount = tmp_path / "rt"
    image = tmp_path / "rt.img"
    initial_report = sr.report(
        ["sandbox.bubblewrap", "sandbox.prlimit", "runtime.filesystem"]
    )
    reports = iter([initial_report, {"missing": []}])
    monkeypatch.setattr(sr, "report", lambda caps: next(reports))
    result = sr.provision(
        ["sandbox.bubblewrap", "sandbox.prlimit", "runtime.filesystem"],
        password="s3cret",
        runner=runner,
        mount=mount,
        image=image,
    )
    assert result["installed_packages"] == ["bubblewrap", "util-linux", "e2fsprogs"]
    # password never appears in any argv
    for argv, _stdin in runner.calls:
        assert "s3cret" not in argv
        assert not any("s3cret" in part for part in argv)
    # sudo password is only fed on stdin, and sudo creds are cleared at the end
    docker_free = [c for c in runner.calls if c[0][:1] == ["sudo"]]
    assert docker_free[0][0][:3] == ["sudo", "-S", "-p"]
    assert docker_free[0][1] == b"s3cret\n"
    assert runner.calls[-1][0] == ["sudo", "-k"]
    commands = [argv for argv, _ in runner.calls]
    guarded = [command for command in commands if command[4:6] == ["python3", "-c"]]
    assert len(guarded) == 1
    assert guarded[0][-5:] == [
        str(mount),
        str(image),
        str(sr.RUNTIME_VOLUME_BYTES),
        str(sr.RUNTIME_VOLUME_INODES),
        sr._owner(),
    ]


def test_provision_refuses_container(monkeypatch):
    _patch_host(monkeypatch, container=True)
    with pytest.raises(sr.SystemRequirementError) as exc:
        sr.provision(["sandbox.bubblewrap"], runner=_Runner())
    assert exc.value.code == "system_requirements_container_unsupported"


def test_provision_failure_clears_sudo_and_raises(monkeypatch, tmp_path):
    _patch_host(monkeypatch, family="debian", container=False, present=())
    runner = _Runner(fail_on="apt-get")
    with pytest.raises(sr.SystemRequirementError) as exc:
        sr.provision(
            ["sandbox.bubblewrap"],
            password="pw",
            runner=runner,
            mount=tmp_path / "rt",
            image=tmp_path / "rt.img",
        )
    assert exc.value.code == "system_requirements_provision_failed"
    assert runner.calls[-1][0] == ["sudo", "-k"]


def test_host_requirements_summary_reports_service_packages(monkeypatch):
    from src.extension_installer import host_requirements_summary

    captured: dict[str, list[str]] = {}

    def fake_report(capabilities):
        captured["capabilities"] = list(capabilities)
        return {"missing": []}

    monkeypatch.setattr(sr, "report", fake_report)
    plan = {"manifest": {"runtime": {"type": "service"}}}
    assert host_requirements_summary(plan) == {"missing": []}
    assert set(captured["capabilities"]) == set(sr.SANDBOX_CAPABILITIES)
    assert host_requirements_summary({"manifest": {"runtime": {"type": "web"}}}) is None


def test_declared_media_capabilities_reach_the_report(monkeypatch):
    from src.extension_installer import host_requirements_summary

    captured: dict[str, list[str]] = {}

    def fake_report(capabilities):
        captured["capabilities"] = list(capabilities)
        return {"missing": []}

    monkeypatch.setattr(sr, "report", fake_report)
    plan = {
        "manifest": {
            "runtime": {"type": "service"},
            "metadata": {
                "host_requirements": {
                    "capabilities": ["media.libmpv", "build.pkg_config"]
                }
            },
        }
    }
    assert host_requirements_summary(plan) == {"missing": []}
    assert {"media.libmpv", "build.pkg_config"} <= set(captured["capabilities"])
