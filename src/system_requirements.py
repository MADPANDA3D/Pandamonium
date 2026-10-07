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
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_RUNTIME_MOUNT = Path("/var/lib/pandamonium-package-runtime")
DEFAULT_RUNTIME_IMAGE = Path("/var/lib/pandamonium-package-runtime.img")
RUNTIME_VOLUME_BYTES = 2 * 1024**3
RUNTIME_VOLUME_INODES = 131_072
MAX_RUNTIME_VOLUME_BYTES = 8 * 1024**3
MAX_RUNTIME_VOLUME_INODES = 1_000_000

# Packages that a privileged run is ever allowed to install. Anything resolved
# outside this set is refused before a single command is built.
_PACKAGE_ALLOWLIST = frozenset(
    {
        "bubblewrap",
        "util-linux",
        "e2fsprogs",
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
        packages={
            family: ("e2fsprogs", "util-linux")
            for family in ("debian", "arch", "fedora", "suse", "alpine")
        },
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
        if mount.is_absolute() and not mount.is_symlink() and mount.is_dir():
            root = mount.resolve()
            stat = os.statvfs(root)
            if (
                os.path.ismount(root)
                and root != root.parent
                and root.stat().st_dev != root.parent.stat().st_dev
                and 0 < stat.f_blocks * stat.f_frsize <= MAX_RUNTIME_VOLUME_BYTES
                and 0 < stat.f_files <= MAX_RUNTIME_VOLUME_INODES
            ):
                return True, ""
    except OSError:
        pass
    return (
        False,
        f"Mount a dedicated filesystem (at most 8 GiB and 1000000 inodes) at {mount}.",
    )


def _probe_systemd_user() -> tuple[bool, str]:
    if not _which("systemd-run") or not _which("systemctl"):
        return False, "systemd-run and systemctl are required."
    try:
        result = subprocess.run(
            ["systemctl", "--user", "show", "--property=ControlGroup", "--value"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        group = result.stdout.strip()
        cgroup_root = Path("/sys/fs/cgroup")
        path = cgroup_root / group.lstrip("/")
        if (
            not group.startswith("/")
            or not group.strip("/")
            or not path.resolve().is_relative_to(cgroup_root)
        ):
            raise ValueError("No active user manager cgroup was returned.")
        controllers = set((path / "cgroup.controllers").read_text().split())
        delegated = set((path / "cgroup.subtree_control").read_text().split())
        if not {"memory", "pids", "cpu"} <= controllers & delegated:
            raise ValueError(
                "Delegate cgroup v2 memory, pids and cpu controllers to the user manager."
            )
        # Read actual kernel interfaces, rather than trusting session variables.
        for name in ("memory.max", "memory.swap.max", "pids.max", "cpu.max"):
            if not (path / name).read_text().strip():
                raise ValueError(
                    "A required cgroup v2 resource interface is unavailable."
                )
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return (
            False,
            f"A systemd user manager with cgroup v2 resource controls is required: {exc}",
        )
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
    return (
        result.returncode == 0,
        "" if result.returncode == 0 else "libmpv development files are not installed.",
    )


_PROBES: Mapping[str, Callable[[], tuple[bool, str]]] = {
    "bwrap": lambda: (_which("bwrap"), "bubblewrap is not installed."),
    "prlimit": lambda: (_which("prlimit"), "util-linux is not installed."),
    "systemd_user": _probe_systemd_user,
    "runtime_filesystem": _probe_runtime_filesystem,
    "cc": lambda: (_which("cc") or _which("gcc"), "No C compiler is installed."),
    "pkg_config": lambda: (_which("pkg-config"), "pkg-config is not installed."),
    "pkg_config_mpv": _probe_pkg_config_mpv,
}


def declared_capabilities(
    manifest: Mapping[str, Any], integration: Mapping[str, Any] | None = None
) -> list[str]:
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
                raise SystemRequirementError(
                    "system_requirement_package_not_allowed", name
                )
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
                "detail": "" if present else detail,
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


SudoRunner = Callable[
    [Sequence[str], "bytes | None"], "subprocess.CompletedProcess[bytes]"
]


def _default_runner(
    argv: Sequence[str], stdin_bytes: bytes | None
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        list(argv),
        input=stdin_bytes,
        capture_output=True,
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


# This fixed payload runs through the same privileged runner as package setup.
# Every path/data check also happens inside the privileged execution, so a retry
# cannot turn an existing image into a formatting target after an earlier probe.
_RUNTIME_FILESYSTEM_SCRIPT = r"""
import fcntl
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

MAX_BYTES = 8 * 1024**3
MAX_INODES = 1000000

def run(argv: list[str], *, pass_fds: tuple[int, ...] = ()) -> str:
    print("+ " + " ".join(argv), flush=True)
    result = subprocess.run(argv, capture_output=True, text=True, check=True, timeout=120, pass_fds=pass_fds, env={**os.environ, "LC_ALL": "C"})
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="", flush=True)
    if result.stdout and argv[0] not in {"blkid", "dumpe2fs", "findmnt", "losetup"}:
        print(result.stdout, end="", flush=True)
    return result.stdout.strip()

def safe_path(path: Path) -> None:
    if not path.is_absolute() or path == Path("/") or path.resolve() != path:
        raise ValueError("Runtime paths must be absolute, canonical and free of symlinks.")
    for component in (path, *path.parents):
        if component.is_symlink():
            raise ValueError("A runtime path contains a symlink.")

def bounded(size: int, inodes: int) -> None:
    if not 0 < size <= MAX_BYTES or not 0 < inodes <= MAX_INODES:
        raise ValueError("Runtime storage exceeds the 8 GiB/1000000 inode limits.")

def image_bounds(image: Path, image_fd: int | None = None) -> tuple[int, int]:
    metadata = os.fstat(image_fd) if image_fd is not None else image.stat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise ValueError("The runtime image must be a regular file with one link.")
    target = str(image) if image_fd is None else "/proc/self/fd/" + str(image_fd)
    descriptors = () if image_fd is None else (image_fd,)
    if run(["blkid", "-p", "-s", "TYPE", "-o", "value", target], pass_fds=descriptors) != "ext4":
        raise ValueError("The existing runtime image is not ext4; it will not be formatted.")
    fields = {}
    for line in run(["dumpe2fs", "-h", target], pass_fds=descriptors).splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip()
    capacity = int(fields["Block count"]) * int(fields["Block size"])
    inodes = int(fields["Inode count"])
    bounded(capacity, inodes)
    if not capacity <= metadata.st_size <= MAX_BYTES:
        raise ValueError("Runtime image file size does not match its bounded filesystem.")
    return capacity, inodes

def mounted_source(mount: Path) -> tuple[str, str] | None:
    result = subprocess.run(["findmnt", "-rn", "-M", str(mount), "-o", "SOURCE,FSTYPE"], capture_output=True, text=True, check=False, timeout=10)
    if result.returncode == 1 and not result.stdout.strip():
        return None
    if result.returncode != 0:
        raise ValueError("The runtime mount could not be inspected.")
    rows = [line.split() for line in result.stdout.splitlines() if line.strip()]
    if len(rows) != 1 or len(rows[0]) != 2:
        raise ValueError("The runtime mount identity is ambiguous.")
    return rows[0][0], rows[0][1]

def same_loop_image(source: str, image: Path) -> bool:
    loops = run(["losetup", "-j", str(image), "--output", "NAME", "--noheadings"]).splitlines()
    return source.startswith("/dev/loop") and os.path.realpath(source) in {os.path.realpath(loop.strip()) for loop in loops}

def check_mount_capacity(mount: Path) -> None:
    if not os.path.ismount(mount) or mount.stat().st_dev == mount.parent.stat().st_dev:
        raise ValueError("Runtime storage is not a dedicated filesystem.")
    metadata = os.statvfs(mount)
    bounded(metadata.f_blocks * metadata.f_frsize, metadata.f_files)

def fstab_escape(path: Path) -> str:
    value = str(path)
    if "\n" in value or "\r" in value or "#" in value:
        raise ValueError("Runtime paths cannot contain newlines or fstab comment markers.")
    return value.replace("\\", "\\134").replace(" ", "\\040").replace("\t", "\\011")

def main(mount_raw: str, image_raw: str, size_raw: str, inodes_raw: str, owner: str, *, fstab: Path = Path("/etc/fstab")) -> None:
    mount, image = Path(mount_raw), Path(image_raw)
    safe_path(mount)
    safe_path(image)
    if mount == image or mount in image.parents or image in mount.parents:
        raise ValueError("Runtime mount and image paths overlap.")
    size, inodes = int(size_raw), int(inodes_raw)
    bounded(size, inodes)
    if not re.fullmatch(r"[a-zA-Z0-9_-]+:[a-zA-Z0-9_-]+", owner):
        raise ValueError("Invalid runtime owner.")
    if not image.parent.is_dir():
        raise ValueError("Runtime image parent directory does not exist.")
    source = mounted_source(mount)
    exists = image.exists()
    if exists:
        bounded(*image_bounds(image))
    if source:
        if source[1] != "ext4" or not exists or not same_loop_image(source[0], image):
            raise ValueError("The occupied runtime mount does not use this ext4 image.")
        check_mount_capacity(mount)
    elif mount.exists() and (not mount.is_dir() or any(mount.iterdir())):
        raise ValueError("The unmounted runtime target is occupied; no files will be hidden.")

    expected = [fstab_escape(image), fstab_escape(mount), "ext4", "loop,defaults", "0", "0"]
    fd = os.open(fstab, os.O_RDWR | os.O_NOFOLLOW)
    with os.fdopen(fd, "r+", encoding="utf-8") as table:
        if not stat.S_ISREG(os.fstat(table.fileno()).st_mode):
            raise ValueError("fstab is not a regular file.")
        fcntl.flock(table, fcntl.LOCK_EX)
        original = table.read()
        found = False
        for line in original.splitlines():
            parts = line.split("#", 1)[0].split()
            if len(parts) >= 2 and (parts[0] == expected[0] or parts[1] == expected[1]):
                if parts != expected:
                    raise ValueError("fstab already assigns this image or mount to a different target.")
                if found:
                    raise ValueError("fstab has duplicate runtime entries.")
                found = True
        # Hold a descriptor through formatting and mounting. Replacing the path
        # cannot redirect mkfs to an existing file between the guard and command.
        flags = os.O_RDONLY if exists else os.O_RDWR | os.O_CREAT | os.O_EXCL
        image_fd = os.open(image, flags | os.O_NOFOLLOW, 0o600)
        try:
            target = "/proc/self/fd/" + str(image_fd)
            if not exists:
                os.ftruncate(image_fd, size)
                run(["mkfs.ext4", "-N", str(inodes), target], pass_fds=(image_fd,))
            bounded(*image_bounds(image, image_fd))
            if image.stat().st_ino != os.fstat(image_fd).st_ino or image.stat().st_dev != os.fstat(image_fd).st_dev:
                raise ValueError("Runtime image path changed during setup.")
            if source is None:
                mount.mkdir(mode=0o755, exist_ok=True)
                safe_path(mount)
                if any(mount.iterdir()):
                    raise ValueError("Runtime mount target became occupied during setup.")
                run(["mount", "-o", "loop", target, str(mount)], pass_fds=(image_fd,))
                source = mounted_source(mount)
                if not source or source[1] != "ext4" or not same_loop_image(source[0], image):
                    raise ValueError("Mounted runtime storage identity did not match the image.")
                check_mount_capacity(mount)
            if image.stat().st_ino != os.fstat(image_fd).st_ino or image.stat().st_dev != os.fstat(image_fd).st_dev:
                raise ValueError("Runtime image path changed during setup.")
            run(["chown", owner, str(mount)])
            if not found:
                table.seek(0, os.SEEK_END)
                table.write(("\n" if original and not original.endswith("\n") else "") + " ".join(expected) + "\n")
                table.flush()
                os.fsync(table.fileno())
        finally:
            os.close(image_fd)

if __name__ == "__main__":
    try:
        main(*sys.argv[1:])
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print("Runtime filesystem setup refused: " + str(exc), file=sys.stderr)
        sys.exit(1)
"""


def _runtime_filesystem_argv(
    mount: Path, image: Path, size_bytes: int, owner: str
) -> list[list[str]]:
    if not 0 < size_bytes <= MAX_RUNTIME_VOLUME_BYTES:
        raise SystemRequirementError(
            "system_requirements_provision_failed",
            "Runtime filesystem size exceeds the 8 GiB bound.",
        )
    return [
        [
            "python3",
            "-c",
            _RUNTIME_FILESYSTEM_SCRIPT,
            str(mount),
            str(image),
            str(size_bytes),
            str(RUNTIME_VOLUME_INODES),
            owner,
        ]
    ]


def _owner() -> str:
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
    runtime_image = image or DEFAULT_RUNTIME_IMAGE
    # A successful mount can precede an interrupted fstab write. Repeat the
    # guarded step for our existing image even when the current probe passes.
    if "runtime.filesystem" in missing or (
        "runtime.filesystem" in capabilities and runtime_image.exists()
    ):
        steps.extend(
            _runtime_filesystem_argv(
                mount or runtime_mount(),
                runtime_image,
                size_bytes,
                _owner(),
            )
        )
    if "systemd.user" in missing:
        steps.append(["loginctl", "enable-linger", _owner().split(":", 1)[0]])

    run = runner or _default_runner
    executed: list[dict[str, Any]] = []
    try:
        for step in steps:
            stdin_bytes = (password + "\n").encode("utf-8") if password else None
            argv = (["sudo", "-S", "-p", ""] if password else ["sudo", "-n"]) + step
            try:
                proc = run(argv, stdin_bytes)
            except (OSError, subprocess.SubprocessError) as exc:
                raise SystemRequirementError(
                    "system_requirements_provision_failed", str(exc)
                ) from exc
            executed.append({"command": step[0], "returncode": int(proc.returncode)})
            if proc.returncode != 0:
                raise SystemRequirementError(
                    "system_requirements_provision_failed", step[0]
                )
    finally:
        if password:
            run(["sudo", "-k"], None)
    readback = report(capabilities)
    if readback["missing"]:
        raise SystemRequirementError(
            "system_requirements_provision_failed",
            "Host requirements remain unavailable after setup: "
            + ", ".join(readback["missing"]),
        )
    return {
        "installed_packages": packages,
        "capabilities": missing,
        "steps": executed,
        "report": readback,
    }
