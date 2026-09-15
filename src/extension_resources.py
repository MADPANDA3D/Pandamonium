"""Kernel-enforced aggregate budgets for generated package processes and storage."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

from src.extension_installer import ExtensionLifecycleError

SLICE = "pandamonium-extensions.slice"
MEMORY_BYTES = 4 * 1024**3
STORAGE_BYTES = 8 * 1024**3
TASKS = 256


def storage_root() -> Path:
    """Require a dedicated, finite filesystem, never a directory size estimate."""
    raw = os.getenv("ODYSSEUS_EXTENSION_RUNTIME_ROOT", "")
    root = Path(raw)
    if not raw or not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise ExtensionLifecycleError(
            "extension_needs_setup:Mount a dedicated filesystem (at most 8 GiB, "
            "at most 1000000 inodes) at ODYSSEUS_EXTENSION_RUNTIME_ROOT."
        )
    root = root.resolve()
    stat = os.statvfs(root)
    if (
        not os.path.ismount(root)
        or root == root.parent
        or root.stat().st_dev == root.parent.stat().st_dev
        or not 0 < stat.f_blocks * stat.f_frsize <= STORAGE_BYTES
        or not 0 < stat.f_files <= 1_000_000
    ):
        raise ExtensionLifecycleError(
            "extension_needs_setup:Runtime storage needs an enforced filesystem "
            "capacity of at most 8 GiB and 1000000 inodes."
        )
    return root


def _systemctl(*args: str) -> str:
    try:
        result = subprocess.run(
            ["systemctl", "--user", *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise ExtensionLifecycleError(
            "extension_needs_setup:Enable a systemd user manager with delegated "
            "cgroup v2 memory, pids and cpu controllers for the app service user."
        ) from exc


def admit() -> None:
    storage_root()
    if not shutil.which("systemd-run"):
        raise ExtensionLifecycleError("extension_needs_setup:systemd-run required")
    _systemctl(
        "set-property",
        "--runtime",
        SLICE,
        f"MemoryMax={MEMORY_BYTES}",
        "MemorySwapMax=0",
        f"TasksMax={TASKS}",
        "CPUQuota=200%",
    )


def scope(command: list[str], unit: str | None = None) -> tuple[list[str], str]:
    admit()
    unit = unit or "pandamonium-package-" + uuid.uuid4().hex + ".scope"
    return [
        "systemd-run",
        "--user",
        "--scope",
        "--quiet",
        "--collect",
        "--unit=" + unit,
        "--slice=" + SLICE,
        "--property=MemoryMax=2G",
        "--property=MemorySwapMax=0",
        "--property=TasksMax=128",
        "--",
        *command,
    ], unit


def verify_scope(unit: str) -> None:
    """Read actual kernel limits before releasing the untrusted process."""
    group = _systemctl("show", unit, "--property=ControlGroup", "--value")
    path = Path("/sys/fs/cgroup") / group.lstrip("/")
    try:
        if not group or not path.resolve().is_relative_to(Path("/sys/fs/cgroup")):
            raise ValueError("missing cgroup")
        for parent, memory, tasks in (
            (path, 2 * 1024**3, 128),
            (path.parent, MEMORY_BYTES, TASKS),
        ):
            if int((parent / "memory.max").read_text()) > memory:
                raise ValueError("memory limit unavailable")
            if int((parent / "pids.max").read_text()) > tasks:
                raise ValueError("task limit unavailable")
            if (parent / "memory.swap.max").read_text().strip() != "0":
                raise ValueError("swap limit unavailable")
        quota, period = (path.parent / "cpu.max").read_text().split()
        if int(quota) > 2 * int(period):
            raise ValueError("CPU limit unavailable")
    except (OSError, ValueError) as exc:
        raise ExtensionLifecycleError(
            "extension_needs_setup:Kernel cgroup resource limits were not applied."
        ) from exc


def stop(unit: str) -> None:
    # Stopping an already-collected scope is harmless; never stop another unit.
    if re.fullmatch(r"pandamonium-package-[a-f0-9]{32}\.scope", unit):
        subprocess.run(
            ["systemctl", "--user", "kill", "--signal=KILL", "--kill-whom=all", unit],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
        subprocess.run(
            ["systemctl", "--user", "stop", unit],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
