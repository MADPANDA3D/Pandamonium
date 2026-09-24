import subprocess

import pytest

import src.system_requirements as sr


def _probe(present: bool):
    return lambda: (present, "" if present else "missing")


def _patch_host(monkeypatch, *, family="debian", container=False, present=()):
    monkeypatch.setattr(sr, "distro_family", lambda: family)
    monkeypatch.setattr(sr, "is_container", lambda: container)
    monkeypatch.setattr(
        sr,
        "_PROBES",
        {name: _probe(name in present) for name in ("bwrap", "prlimit", "systemd_user", "runtime_filesystem", "cc", "pkg_config", "pkg_config_mpv")},
    )


def test_distro_family_reads_os_release(monkeypatch):
    monkeypatch.setattr(sr, "_read_os_release", lambda: {"ID": "ubuntu", "ID_LIKE": "debian"})
    assert sr.distro_family() == "debian"
    monkeypatch.setattr(sr, "_read_os_release", lambda: {"ID": "endeavouros", "ID_LIKE": "arch"})
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
    bad = {"metadata": {"host_requirements": {"capabilities": ["apt.install.anything"]}}}
    with pytest.raises(sr.SystemRequirementError) as exc:
        sr.declared_capabilities(bad)
    assert exc.value.code == "system_requirement_unknown_capability"


def test_capabilities_for_package_adds_sandbox_for_service():
    manifest = {
        "runtime": {"type": "service"},
        "metadata": {"host_requirements": {"capabilities": ["media.libmpv", "build.pkg_config"]}},
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
    assert result["packages"] == ["bubblewrap"]

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
    result = sr.provision(
        ["sandbox.bubblewrap", "sandbox.prlimit", "runtime.filesystem"],
        password="s3cret",
        runner=runner,
        mount=mount,
        image=image,
    )
    assert result["installed_packages"] == ["bubblewrap", "util-linux"]
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
    assert ["sudo", "-S", "-p", "", "mount", "-o", "loop", str(image), str(mount)] in commands
    assert ["sudo", "-S", "-p", "", "mkfs.ext4", "-F", str(image)] in commands


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
            "metadata": {"host_requirements": {"capabilities": ["media.libmpv", "build.pkg_config"]}},
        }
    }
    assert host_requirements_summary(plan) == {"missing": []}
    assert {"media.libmpv", "build.pkg_config"} <= set(captured["capabilities"])
