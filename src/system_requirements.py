"""Host system requirements for generated packages.

Third-party packages may only *name* capabilities from this first-party catalog.
Package names, distro mapping and provisioning commands live here so an untrusted
manifest can never turn the installer into arbitrary root execution. Detection is
read-only; provisioning is a separate, explicitly confirmed step.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

DEFAULT_RUNTIME_MOUNT = Path("/var/lib/pandamonium-package-runtime")
DEFAULT_RUNTIME_IMAGE = Path("/var/lib/pandamonium-package-runtime.img")
RUNTIME_VOLUME_BYTES = 2 * 1024**3

# Packages that a privileged run is ever allowed to install. Anything resolved
# outside this set is refused before a single command is built.
_PACKAGE_ALLOWLIST = frozenset(
    {
        "bubblewrap",
        "util-linux",
        "build-essential",
        "base-devel",
        "build-base",
        "gcc",
        "make",
        "pkg-config",
        "pkgconf",
        "pkgconf-pkg-config",
        "libmpv-dev",
        "mpv",
        "mpv-libs-devel",
        "mpv-devel",
    }
)

_DISTRO_ALIASES = {
    "ubuntu": "debian",
    "debian": "debian",
    "linuxmint": "debian",
    "pop": "debian",
    "raspbian": "debian",
    "arch": "arch",
    "manjaro": "arch",
    "endeavouros": "arch",
    "fedora": "fedora",
    "rhel": "fedora",
    "centos": "fedora",
    "rocky": "fedora",
    "almalinux": "fedora",
    "opensuse": "suse",
    "opensuse-leap": "suse",
    "opensuse-tumbleweed": "suse",
    "sles": "suse",
    "alpine": "alpine",
}


class SystemRequirementError(Exception):
    """A resolvable, coded host-requirement failure."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Capability:
    id: str
    summary: str
    packages: Mapping[str, tuple[str, ...]]
    probe: str
    needs_root: bool = True
    persistent: bool = False
    detail: str = ""


# capability id -> catalog entry. `probe` names a function in _PROBES.
CATALOG: Mapping[str, Capability] = {
    "sandbox.bubblewrap": Capability(
        id="sandbox.bubblewrap",
        summary="Bubblewrap sandbox for running package code",
        packages={
            "debian": ("bubblewrap",),
            "arch": ("bubblewrap",),
            "fedora": ("bubblewrap",),
            "suse": ("bubblewrap",),
            "alpine": ("bubblewrap",),
        },
        probe="bwrap",
    ),
    "sandbox.prlimit": Capability(
        id="sandbox.prlimit",
        summary="util-linux process limits (prlimit)",
        packages={
            "debian": ("util-linux",),
            "arch": ("util-linux",),
            "fedora": ("util-linux",),
            "suse": ("util-linux",),
            "alpine": ("util-linux",),
        },
        probe="prlimit",
    ),
    "systemd.user": Capability(
        id="systemd.user",
        summary="systemd user manager with cgroup v2 controls",
        packages={},
        probe="systemd_user",
    ),
    "runtime.filesystem": Capability(
        id="runtime.filesystem",
        summary="Dedicated bounded filesystem for private package runtimes",
        packages={},
        probe="runtime_filesystem",
        persistent=True,
    ),
    "build.c_toolchain": Capability(
        id="build.c_toolchain",
        summary="C toolchain for packages that build native code",
        packages={
            "debian": ("build-essential",),
            "arch": ("base-devel",),
            "fedora": ("gcc", "make"),
            "suse": ("gcc", "make"),
            "alpine": ("build-base",),
        },
        probe="cc",
    ),
    "build.pkg_config": Capability(
        id="build.pkg_config",
        summary="pkg-config for native build dependency lookup",
        packages={
            "debian": ("pkg-config",),
            "arch": ("pkgconf",),
            "fedora": ("pkgconf-pkg-config",),
            "suse": ("pkg-config",),
            "alpine": ("pkgconf",),
        },
        probe="pkg_config",
    ),
    "media.libmpv": Capability(
        id="media.libmpv",
        summary="libmpv development files for media playback packages",
        packages={
            "debian": ("libmpv-dev",),
            "arch": ("mpv",),
            "fedora": ("mpv-libs-devel",),
            "suse": ("mpv-devel",),
            "alpine": ("mpv-dev",),
        },
        probe="pkg_config_mpv",
    ),
}

# A generated service/CLI package always needs the sandbox foundation. Build and
# media capabilities are only used when the package explicitly declares them.
SANDBOX_CAPABILITIES = (
    "sandbox.bubblewrap",
    "sandbox.prlimit",
    "systemd.user",
    "runtime.filesystem",
)


def _which(name: str) -> bool:
    return bool(shutil.which(name))


def _read_os_release() -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return values


def distro_family() -> str:
    """Return the supported package-manager family, or "unknown"."""
    os_release = _read_os_release()
    for key in ("ID", "ID_LIKE"):
        for candidate in str(os_release.get(key) or "").lower().split():
            family = _DISTRO_ALIASES.get(candidate)
            if family:
                return family
    return "unknown"


def is_container() -> bool:
    """True when the app runs inside a container that cannot mount/systemd."""
    if os.getenv("container"):
        return True
    return Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()


def runtime_mount() -> Path:
    raw = os.getenv("ODYSSEUS_EXTENSION_RUNTIME_ROOT", "").strip()
    return Path(raw) if raw else DEFAULT_RUNTIME_MOUNT


def _probe_runtime_filesystem() -> tuple[bool, str]:
    mount = runtime_mount()
    try:
        if mount.is_dir() and mount.is_mount():
            return True, ""
    except OSError:
        pass
    return False, f"Mount a dedicated filesystem at {mount}."


def _probe_systemd_user() -> tuple[bool, str]:
    if not _which("systemd-run"):
        return False, "systemd-run is not available."
    if not os.getenv("XDG_RUNTIME_DIR") and not os.getenv("DBUS_SESSION_BUS_ADDRESS"):
        return False, "No systemd user session is active for the app service user."
    return True, ""


def _probe_pkg_config_mpv() -> tuple[bool, str]:
    if not _which("pkg-config"):
        return False, "pkg-config is not available."
    try:
        result = subprocess.run(
            ["pkg-config", "--exists", "mpv"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False, "pkg-config could not be queried."
    return result.returncode == 0, "libmpv development files are not installed."


_PROBES: Mapping[str, Callable[[], tuple[bool, str]]] = {
    "bwrap": lambda: (_which("bwrap"), "bubblewrap is not installed."),
    "prlimit": lambda: (_which("prlimit"), "util-linux is not installed."),
    "systemd_user": _probe_systemd_user,
    "runtime_filesystem": _probe_runtime_filesystem,
    "cc": lambda: (_which("cc") or _which("gcc"), "No C compiler is installed."),
    "pkg_config": lambda: (_which("pkg-config"), "pkg-config is not installed."),
    "pkg_config_mpv": _probe_pkg_config_mpv,
}


def declared_capabilities(manifest: Mapping[str, Any], integration: Mapping[str, Any] | None = None) -> list[str]:
    """Read explicitly declared capabilities from the manifest/integration.

    Unknown ids are rejected so a manifest can never smuggle in a package name.
    """
    declared: list[str] = []
    for source in (manifest.get("metadata") or {}, integration or {}, manifest):
        block = source.get("host_requirements") if isinstance(source, Mapping) else None
        if not isinstance(block, Mapping):
            continue
        raw = block.get("capabilities")
        if isinstance(raw, list):
            declared.extend(str(item).strip() for item in raw if str(item).strip())
    unknown = sorted({item for item in declared if item not in CATALOG})
    if unknown:
        raise SystemRequirementError(
            "system_requirement_unknown_capability", ", ".join(unknown)
        )
    return declared


def capabilities_for_package(
    manifest: Mapping[str, Any], integration: Mapping[str, Any] | None = None
) -> list[str]:
    """Capabilities a package needs: declared ones plus the sandbox foundation."""
    runtime = (manifest.get("runtime") or {}).get("type")
    required = set(declared_capabilities(manifest, integration))
    if runtime in {"service", "cli", "mcp"}:
        required.update(SANDBOX_CAPABILITIES)
    return sorted(required)


def package_names(capabilities: Iterable[str], family: str) -> list[str]:
    """Resolve the ordered, allowlisted package list for a distro family."""
    names: list[str] = []
    for capability_id in capabilities:
        entry = CATALOG.get(capability_id)
        if entry is None:
            raise SystemRequirementError(
                "system_requirement_unknown_capability", capability_id
            )
        for name in entry.packages.get(family, ()):
            if name not in _PACKAGE_ALLOWLIST:
                raise SystemRequirementError("system_requirement_package_not_allowed", name)
            if name not in names:
                names.append(name)
    return names


def report(capabilities: Sequence[str]) -> dict[str, Any]:
    """Read-only host report for the requested capabilities."""
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for capability_id in capabilities:
        entry = CATALOG.get(capability_id)
        if entry is None:
            raise SystemRequirementError(
                "system_requirement_unknown_capability", capability_id
            )
        present, detail = _PROBES[entry.probe]()
        if not present:
            missing.append(capability_id)
        rows.append(
            {
                "id": entry.id,
                "summary": entry.summary,
                "present": present,
                "needs_root": entry.needs_root,
                "persistent": entry.persistent,
                "detail": detail,
            }
        )
    family = distro_family()
    container = is_container()
    packages = package_names(missing, family) if family != "unknown" else []
    needs_root = any(CATALOG[cap].needs_root for cap in missing)
    blocked_reason = None
    if missing and container:
        blocked_reason = "system_requirements_container_unsupported"
    elif missing and family == "unknown":
        blocked_reason = "system_requirements_distro_unsupported"
    return {
        "host": {
            "distro_family": family,
            "container": container,
            "runtime_mount": str(runtime_mount()),
        },
        "capabilities": rows,
        "missing": missing,
        "packages": packages,
        "needs_root": needs_root,
        "auto_provisionable": bool(missing) and blocked_reason is None,
        "blocked_reason": blocked_reason,
    }


def installed(capabilities: Sequence[str]) -> bool:
    return not report(capabilities)["missing"]


SudoRunner = Callable[[Sequence[str], "bytes | None"], "subprocess.CompletedProcess[bytes]"]


def _default_runner(argv: Sequence[str], stdin_bytes: "bytes | None") -> "subprocess.CompletedProcess[bytes]":
    return subprocess.run(
        list(argv),
        input=stdin_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=900,
        check=False,
    )


def _install_argv(family: str, packages: Sequence[str]) -> list[list[str]]:
    if not packages:
        return []
    if family == "debian":
        return [
            ["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "update", "-qq"],
            [
                "env",
                "DEBIAN_FRONTEND=noninteractive",
                "apt-get",
                "install",
                "-y",
                "--no-install-recommends",
                *packages,
            ],
        ]
    if family == "arch":
        return [["pacman", "-S", "--needed", "--noconfirm", *packages]]
    if family == "fedora":
        return [["dnf", "install", "-y", *packages]]
    if family == "suse":
        return [["zypper", "install", "-n", *packages]]
    if family == "alpine":
        return [["apk", "add", *packages]]
    raise SystemRequirementError("system_requirements_distro_unsupported", family)


def _runtime_filesystem_argv(
    mount: Path, image: Path, size_bytes: int, owner: str
) -> list[list[str]]:
    fstab_entry = f"{image} {mount} ext4 loop,defaults 0 0"
    return [
        ["install", "-d", "-m", "0755", str(mount)],
        ["truncate", "-s", str(size_bytes), str(image)],
        ["mkfs.ext4", "-F", str(image)],
        ["mount", "-o", "loop", str(image), str(mount)],
        [
            "sh",
            "-c",
            'grep -qsF "$1" /etc/fstab || printf "%s\\n" "$1" >> /etc/fstab',
            "sh",
            fstab_entry,
        ],
        ["chown", owner, str(mount)],
    ]


def _owner() -> str:
    import getpass
    import grp
    import pwd

    user = pwd.getpwuid(os.getuid())
    try:
        group = grp.getgrgid(user.pw_gid).gr_name
    except KeyError:
        group = str(user.pw_gid)
    return f"{user.pw_name}:{group}"


def provision(
    capabilities: Sequence[str],
    *,
    password: str | None = None,
    runner: SudoRunner | None = None,
    family: str | None = None,
    mount: Path | None = None,
    image: Path | None = None,
    size_bytes: int = RUNTIME_VOLUME_BYTES,
) -> dict[str, Any]:
    """Install missing requirements for `capabilities` with one privileged run.

    Only catalog-derived, allowlisted package names are ever passed to a package
    manager. The password, when supplied, is written to sudo's stdin and never
    appears in argv, the environment, logs, or the returned payload.
    """
    if is_container():
        raise SystemRequirementError(
            "system_requirements_container_unsupported",
            "Install Pandamonium natively to use packages that need the sandbox runtime.",
        )
    resolved_family = family or distro_family()
    if resolved_family == "unknown":
        raise SystemRequirementError("system_requirements_distro_unsupported")

    current = report(capabilities)
    missing = [row["id"] for row in current["capabilities"] if not row["present"]]
    packages = package_names(missing, resolved_family)

    steps: list[list[str]] = list(_install_argv(resolved_family, packages))
    if "runtime.filesystem" in missing:
        steps.extend(
            _runtime_filesystem_argv(
                mount or runtime_mount(),
                image or DEFAULT_RUNTIME_IMAGE,
                size_bytes,
                _owner(),
            )
        )
    if "systemd.user" in missing:
        steps.append(["loginctl", "enable-linger", _owner().split(":", 1)[0]])

    run = runner or _default_runner
    executed: list[dict[str, Any]] = []
    for step in steps:
        stdin_bytes = (password + "\n").encode("utf-8") if password else None
        argv = (["sudo", "-S", "-p", ""] if password else ["sudo", "-n"]) + step
        try:
            proc = run(argv, stdin_bytes)
        except (OSError, subprocess.SubprocessError) as exc:
            raise SystemRequirementError(
                "system_requirements_provision_failed", str(exc)
            ) from exc
        executed.append(
            {"command": step[0], "returncode": int(proc.returncode)}
        )
        if proc.returncode != 0:
            if password:
                run(["sudo", "-k"], None)
            raise SystemRequirementError(
                "system_requirements_provision_failed", step[0]
            )
    if password:
        run(["sudo", "-k"], None)
    return {
        "installed_packages": packages,
        "capabilities": missing,
        "steps": executed,
        "report": report(capabilities),
    }
