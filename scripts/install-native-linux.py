#!/usr/bin/env python3
"""Install a pinned Pandamonium source snapshot as a Linux user application.

No system Python packages, containers, models or credentials are installed.
This host-managed profile deliberately does not enable the root signed updater.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import platform
import shlex
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

PYTHON = "3.12.12"
UNIT = "pandamonium-desktop.service"


def run(
    args: list[str],
    *,
    check: bool = True,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    stdout: int | None = None,
) -> subprocess.CompletedProcess[bytes]:
    print("+ " + shlex.join(args), flush=True)
    return subprocess.run(args, check=check, cwd=cwd, env=env, stdout=stdout)


def output(args: list[str]) -> str:
    return subprocess.check_output(args, text=True).strip()


def safe_directory(path: Path) -> None:
    if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
        raise RuntimeError(f"Use an absolute local path without symlinks: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)


def systemd_quote(value: str) -> str:
    return (
        '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'
    )


def health(port: int, revision: str | None = None, timeout: int = 90) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/health", timeout=2
            ) as response:
                ok = json.load(response).get("status") == "healthy"
            if ok and revision:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/version", timeout=5
                ) as response:
                    ok = json.load(response).get("commit") == revision
            if ok:
                return True
        except (OSError, ValueError):
            pass
        time.sleep(1)
    return False


def export_snapshot(source: Path, revision: str, destination: Path) -> None:
    # git archive exports tracked files only: no .env, private data or dirty work.
    with tempfile.TemporaryFile() as archive:
        subprocess.run(
            ["git", "-C", str(source), "archive", revision], stdout=archive, check=True
        )
        archive.seek(0)
        with tarfile.open(fileobj=archive) as tar:
            tar.extractall(destination, filter="data")
    (destination / "SOURCE_REVISION").write_text(revision + "\n")


def service_text(root: Path, config: Path) -> str:
    return f"""[Unit]
Description=Pandamonium native desktop backend
After=network.target

[Service]
Type=simple
WorkingDirectory={str(root / "current").replace("%", "%%")}
EnvironmentFile={str(config).replace("%", "%%")}
ExecStart={systemd_quote(str(root / "current/venv/bin/python"))} -m uvicorn app:app --host 127.0.0.1 --port 7000 --workers 1
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
UMask=0077
MemoryHigh=1500M
MemoryMax=3G
TasksMax=256
CPUQuota=200%
"""


def launcher_text(root: Path) -> str:
    return f"""#!/usr/bin/env python3
import json, pathlib, shutil, subprocess, time, urllib.request
root = pathlib.Path({str(root)!r})
port = 7000
subprocess.run(["systemctl", "--user", "start", {UNIT!r}], check=True)
for attempt in range(90):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{{port}}/api/health", timeout=2) as response:
            if json.load(response).get("status") == "healthy":
                break
    except (OSError, ValueError):
        pass
    time.sleep(1)
else:
    raise SystemExit("Pandamonium did not start. Run: journalctl --user -u {UNIT} -n 80")
url = f"http://127.0.0.1:{{port}}"
browser = next((shutil.which(name) for name in ("google-chrome-stable", "google-chrome", "chromium") if shutil.which(name)), None)
if browser:
    subprocess.Popen([browser, "--no-first-run", "--user-data-dir=" + str(root / "browser"), "--class=Pandamonium", "--app=" + url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
else:
    subprocess.Popen(["xdg-open", url])
"""


def install(args: argparse.Namespace) -> None:
    if sys.platform != "linux" or platform.machine() != "x86_64":
        raise RuntimeError(
            "This tested profile supports Linux x86_64. Other platforms need a validated lock."
        )
    if os.geteuid() == 0:
        raise RuntimeError(
            "Run as your desktop user. Only runtime provisioning uses sudo."
        )
    for command in ("uv", "git", "systemctl", "systemd-analyze", "xdg-open"):
        if not shutil.which(command):
            raise RuntimeError(f"Install {command} first; see docs/native-linux.md.")
    run(["systemctl", "--user", "show-environment"], stdout=subprocess.DEVNULL)
    source = Path(args.source).resolve()
    revision = output(
        ["git", "-C", str(source), "rev-parse", args.revision + "^{commit}"]
    )
    if args.revision == "HEAD" and output(
        ["git", "-C", str(source), "status", "--porcelain", "--untracked-files=no"]
    ):
        raise RuntimeError(
            "Commit or preserve tracked changes first. Only exact committed source is installed."
        )
    root = Path(args.root).expanduser().absolute()
    config = Path.home() / ".config/pandamonium/native.env"
    unit_path = Path.home() / ".config/systemd/user" / UNIT
    launcher = Path.home() / ".local/bin/pandamonium-desktop"
    desktop = Path.home() / ".local/share/applications/pandamonium.desktop"
    if not root.is_absolute() or any(p.is_symlink() for p in (root, *root.parents)):
        raise RuntimeError(f"Use an absolute local path without symlinks: {root}")
    existing_parent = next(p for p in (root, *root.parents) if p.exists())
    if shutil.disk_usage(existing_parent).free < 8 * 1024**3:
        raise RuntimeError(
            "At least 8 GiB free on the install disk is required (including optional 4 GiB runtime)."
        )
    memory_kib = next(
        int(line.split()[1])
        for line in Path("/proc/meminfo").read_text().splitlines()
        if line.startswith("MemAvailable:")
    )
    print(
        f"Linux {platform.machine()}, available RAM {memory_kib / 1024**2:.1f} GiB, disk free {shutil.disk_usage(existing_parent).free / 1024**3:.1f} GiB"
    )
    if memory_kib < 2 * 1024**2:
        raise RuntimeError(
            "Close other applications: at least 2 GiB available RAM is required for bounded plugin builds."
        )
    print(
        "Profile: on-demand user service, localhost, no local models; semantic memory/RAG needs an optional Chroma + embedding provider."
    )
    print(
        "Updates: host-managed pinned source; root-owned signed in-app updater is NOT enabled."
    )
    for target in (config, unit_path, launcher, desktop):
        if target.is_symlink():
            raise RuntimeError(f"Install file is a symlink; inspect it first: {target}")
        if (
            target.exists()
            and not (root / "installation.json").exists()
            and not (root / "owner.json").exists()
        ):
            raise RuntimeError(
                f"Existing unrelated install file: {target}. Inspect and back it up first."
            )
    if args.check:
        return
    safe_directory(root)
    with (root / "install.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        (root / "owner.json").write_text(
            json.dumps(
                {
                    "schema": "pandamonium.native-source.v1",
                    "uid": os.getuid(),
                    "root": str(root),
                }
            )
            + "\n"
        )
        versions = root / "versions"
        versions.mkdir(exist_ok=True)
        revision_lock = subprocess.check_output(
            [
                "git",
                "-C",
                str(source),
                "show",
                f"{revision}:packaging/native-linux/requirements.txt",
            ]
        )
        env_digest = hashlib.sha256(revision_lock).hexdigest()[:12]
        candidate = versions / f"{revision[:12]}-{env_digest}"
        if candidate.exists() and not (candidate / ".install-complete").exists():
            # Preserve interrupted candidates for inspection; stage a fresh one.
            candidate = versions / (candidate.name + f"-{int(time.time())}")
        if not candidate.exists():
            candidate.mkdir()
            export_snapshot(source, revision, candidate)
            run(["uv", "python", "install", PYTHON])
            run(["uv", "venv", "--python", PYTHON, str(candidate / "venv")])
            run(
                [
                    "uv",
                    "pip",
                    "sync",
                    "--python",
                    str(candidate / "venv/bin/python"),
                    "--require-hashes",
                    "--only-binary",
                    ":all:",
                    str(candidate / "packaging/native-linux/requirements.txt"),
                ]
            )
            (candidate / ".install-complete").touch()
        else:
            print("Reusing exact completed source/dependency snapshot.")
        data = root / "data"
        safe_directory(data)
        logs = root / "logs"
        safe_directory(logs)
        log_link = candidate / "logs"
        if not log_link.exists():
            log_link.symlink_to(logs, target_is_directory=True)
        for target in (config, unit_path, launcher, desktop):
            target.parent.mkdir(parents=True, exist_ok=True)
        if not config.exists():
            config.write_text(
                f"PANDAMONIUM_DATA_DIR={systemd_quote(str(data))}\nPANDAMONIUM_LOCAL_EMBEDDINGS=false\nPANDAMONIUM_STARTUP_WARMUPS=false\nPANDAMONIUM_KEEP_ALIVE=false\nPANDAMONIUM_UPDATE_TRIGGER=disabled\nAUTH_ENABLED=true\n"
            )
            config.chmod(0o600)
        if args.provision_runtime:
            print(
                "Provision only the existing first-party sandbox foundation (4 GiB bounded runtime)."
            )
            # NOPASSWD command permission need not include `sudo -v` itself.
            if subprocess.run(
                ["sudo", "-n", "true"], capture_output=True, check=False
            ).returncode:
                run(["sudo", "-v"])
            code = """import subprocess
from src.system_requirements import provision, SANDBOX_CAPABILITIES
def stream(argv, stdin):
    return subprocess.run(argv, input=stdin, check=False)
result = provision(SANDBOX_CAPABILITIES, runner=stream, size_bytes=4*1024**3)
print(result['report'])
if result['report']['missing']: raise SystemExit('Runtime requirements are still missing')
from src.extension_resources import admit
admit()
"""
            run(
                [str(candidate / "venv/bin/python"), "-c", code],
                cwd=candidate,
                env={
                    **os.environ,
                    "PANDAMONIUM_DATA_DIR": str(data),
                    "PANDAMONIUM_LOCAL_EMBEDDINGS": "false",
                },
            )
        current = root / "current"
        previous = current.resolve() if current.is_symlink() else None
        if current.exists() and not current.is_symlink():
            raise RuntimeError(
                "current is not this install's symlink; refusing to replace it."
            )
        # Refuse to take over a port held by an unrelated process.
        active = (
            subprocess.run(
                ["systemctl", "--user", "is-active", "--quiet", UNIT], check=False
            ).returncode
            == 0
        )
        if not active:
            with socket.socket() as sock:
                if sock.connect_ex(("127.0.0.1", 7000)) == 0:
                    raise RuntimeError("Port 7000 is occupied by another service.")
        backup = root / "backups" / time.strftime("%Y%m%dT%H%M%S")
        backup.mkdir(parents=True)
        run(["systemctl", "--user", "stop", UNIT], check=False)
        shutil.copytree(data, backup / "data")
        saved = {
            target: target.read_bytes() if target.exists() else None
            for target in (unit_path, launcher, desktop)
        }
        for target, content in saved.items():
            if content is not None:
                (backup / target.name).write_bytes(content)
        try:
            next_link = root / "current.next"
            next_link.unlink(missing_ok=True)
            next_link.symlink_to(candidate, target_is_directory=True)
            next_link.replace(current)
            unit_path.write_text(service_text(root, config))
            run(["systemd-analyze", "--user", "verify", str(unit_path)])
            launcher.write_text(launcher_text(root))
            launcher.chmod(0o755)
            # No browser download: use installed Chrome/Chromium app mode, otherwise normal browser.
            desktop.write_text(
                f'[Desktop Entry]\nType=Application\nName=Pandamonium\nComment=Your local AI workspace and Entertainment\nExec="{launcher}"\nIcon={root / "current/static/icons/pandamonium.png"}\nTerminal=false\nCategories=Utility;AudioVideo;\nStartupWMClass=Pandamonium\n'
            )
            run(["systemctl", "--user", "daemon-reload"])
            run(["systemctl", "--user", "start", UNIT])
            if not health(7000, revision):
                raise RuntimeError(
                    "Startup/version proof failed. See journalctl --user -u " + UNIT
                )
            (root / "installation.json").write_text(
                json.dumps(
                    {
                        "profile": "host-managed-source",
                        "revision": revision,
                        "python": PYTHON,
                        "lock_sha256": hashlib.sha256(revision_lock).hexdigest(),
                        "previous": str(previous) if previous else None,
                        "backup": str(backup),
                    },
                    indent=2,
                )
                + "\n"
            )
        except BaseException:
            subprocess.run(["systemctl", "--user", "stop", UNIT], check=False)
            # Keep failed data intact and restore the verified, stopped-state copy.
            data.rename(backup / "failed-data")
            shutil.copytree(backup / "data", data)
            current.unlink(missing_ok=True)
            if previous:
                current.symlink_to(previous, target_is_directory=True)
            for target, content in saved.items():
                if content is None:
                    target.unlink(missing_ok=True)
                else:
                    target.write_bytes(content)
            subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
            if active and previous:
                subprocess.run(["systemctl", "--user", "start", UNIT], check=False)
            raise
        print("Installed: http://127.0.0.1:7000 — create your account in the browser.")
        print("Launch: ~/.local/bin/pandamonium-desktop (also in the application menu)")
        print("Stop: systemctl --user stop " + UNIT)
        print("Logs: journalctl --user -u " + UNIT + " -f")
        print("Data and backups: " + str(root))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        default=str(Path(__file__).resolve().parents[1]),
        help="Reviewed local Git checkout",
    )
    parser.add_argument("--revision", default="HEAD", help="Exact commit to export")
    parser.add_argument("--root", default=str(Path.home() / ".local/share/pandamonium"))
    parser.add_argument(
        "--provision-runtime",
        action="store_true",
        help="Explicitly authorize first-party sandbox provisioning with sudo",
    )
    parser.add_argument(
        "--check", action="store_true", help="Read-only prerequisite check"
    )
    args = parser.parse_args()
    try:
        install(args)
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        raise SystemExit(f"Installation stopped: {error}") from error


if __name__ == "__main__":
    main()
