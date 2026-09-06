"""Signed release discovery and atomic native-install updates."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from core.atomic_io import atomic_write_json
from core.constants import APP_VERSION, DATA_DIR
from src.runtime_paths import get_app_root

ROOT = Path(get_app_root()).resolve()
UPDATE_DIR = Path(DATA_DIR).resolve() / "updates"
STATE_PATH = UPDATE_DIR / "state.json"
REQUEST_PATH = UPDATE_DIR / "request.json"
PUBLIC_KEY_PATH = ROOT / "config" / "pandamonium-release.pub"
RELEASES_API = "https://api.github.com/repos/MADPANDA3D/Pandamonium/releases"
SCHEMA = "pandamonium.release.v1"
STATE_SCHEMA = "pandamonium.update-state.v1"
REQUEST_SCHEMA = "pandamonium.update-request.v1"
ALLOWED_DOWNLOAD_HOSTS = {
    "github.com",
    "api.github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
}
_VERSION_RE = re.compile(
    r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
_PYTHON_VERSION_RE = re.compile(r"^(\d+)\.(\d+)$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_SERVICE_RE = re.compile(r"^[A-Za-z0-9_.@-]+\.service$")
_MAX_ARCHIVE_BYTES = 1_000_000_000
_MAX_ARCHIVE_FILES = 20_000
_MAX_EXTRACTED_BYTES = 2_000_000_000


class UpdateError(RuntimeError):
    """A fail-closed updater error safe to show to the owner."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def version_tuple(
    value: str,
) -> tuple[int, int, int, int, tuple[tuple[int, int | str], ...]]:
    match = _VERSION_RE.fullmatch(str(value or "").strip())
    if not match:
        raise UpdateError("invalid release version")
    major, minor, patch, prerelease = match.groups()
    identifiers = tuple(
        (0, int(part)) if part.isdigit() else (1, part.lower())
        for part in (prerelease or "").split(".")
        if part
    )
    return int(major), int(minor), int(patch), int(prerelease is None), identifiers


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def assert_immutable_tree(path: Path, label: str) -> None:
    """Reject application-owned or writable code before a root update uses it."""
    if os.geteuid() != 0:
        return
    try:
        entries = (path, *path.rglob("*"))
        for entry in entries:
            metadata = entry.lstat()
            writable = (
                not stat.S_ISLNK(metadata.st_mode)
                and stat.S_IMODE(metadata.st_mode) & 0o022
            )
            if metadata.st_uid != 0 or writable:
                raise UpdateError(f"{label} is not root-owned and immutable")
    except OSError as exc:
        raise UpdateError(f"{label} ownership could not be verified") from exc


def canonical_manifest_bytes(manifest: dict[str, Any]) -> bytes:
    signed_keys = {
        "schema_version",
        "version",
        "tag",
        "commit",
        "channel",
        "published_at",
        "compatibility",
        "artifact",
    }
    unsigned = {key: manifest[key] for key in signed_keys if key in manifest}
    if isinstance(unsigned.get("artifact"), dict):
        unsigned["artifact"] = {
            key: value for key, value in unsigned["artifact"].items() if key != "url"
        }
    return json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()


def _safe_release_url(value: str) -> str:
    parsed = urlsplit(str(value or ""))
    if (
        parsed.scheme != "https"
        or parsed.hostname not in ALLOWED_DOWNLOAD_HOSTS
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise UpdateError("release asset URL is not an allowed GitHub HTTPS URL")
    return value


def verify_release_manifest(
    manifest: dict[str, Any],
    *,
    public_key_path: Path = PUBLIC_KEY_PATH,
    release: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate the signed release contract and bind it to GitHub assets."""
    if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA:
        raise UpdateError("unsupported release manifest")
    version = str(manifest.get("version") or "")
    tag = str(manifest.get("tag") or "")
    commit = str(manifest.get("commit") or "")
    if tag != f"v{version}" or not _COMMIT_RE.fullmatch(commit):
        raise UpdateError("release identity is invalid")
    version_tuple(version)
    if manifest.get("channel") not in {"stable", "prerelease"}:
        raise UpdateError("release channel is invalid")
    compatibility = manifest.get("compatibility")
    artifact = manifest.get("artifact")
    signature = manifest.get("signature")
    if (
        not isinstance(compatibility, dict)
        or not isinstance(artifact, dict)
        or not isinstance(signature, dict)
    ):
        raise UpdateError("release manifest is incomplete")
    if compatibility.get("migration_entrypoint") != "core.database":
        raise UpdateError("release migration entrypoint is unsupported")
    if compatibility.get("migration_version") != version:
        raise UpdateError("release migration version does not match the artifact")
    version_tuple(str(compatibility.get("minimum_version") or ""))
    if not _PYTHON_VERSION_RE.fullmatch(str(compatibility.get("minimum_python") or "")):
        raise UpdateError("release Python compatibility is invalid")
    if not isinstance(compatibility.get("data_restore_required"), bool):
        raise UpdateError("release rollback contract is invalid")
    name = str(artifact.get("name") or "")
    digest = str(artifact.get("sha256") or "")
    size = artifact.get("size")
    requirements_digest = str(artifact.get("requirements_sha256") or "")
    if (
        name != f"pandamonium-{version}.tar.gz"
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
        or not re.fullmatch(r"[0-9a-f]{64}", requirements_digest)
        or not isinstance(size, int)
        or not 0 < size <= _MAX_ARCHIVE_BYTES
    ):
        raise UpdateError("release artifact contract is invalid")
    if (
        signature.get("algorithm") != "ed25519"
        or signature.get("key_id") != "pandamonium-release-2026"
    ):
        raise UpdateError("release signature contract is invalid")
    try:
        raw_key = base64.b64decode(
            public_key_path.read_text(encoding="utf-8").strip(), validate=True
        )
        raw_signature = base64.b64decode(
            str(signature.get("value") or ""), validate=True
        )
        Ed25519PublicKey.from_public_bytes(raw_key).verify(
            raw_signature, canonical_manifest_bytes(manifest)
        )
    except (OSError, ValueError, InvalidSignature) as exc:
        raise UpdateError("release signature verification failed") from exc

    normalized = dict(manifest)
    normalized["artifact"] = dict(artifact)
    if release is not None:
        if str(release.get("tag_name") or "") != tag or bool(release.get("draft")):
            raise UpdateError("release metadata does not match the signed manifest")
        if bool(release.get("prerelease")) != (manifest["channel"] == "prerelease"):
            raise UpdateError("release channel does not match GitHub metadata")
        assets = {
            str(item.get("name") or ""): str(item.get("browser_download_url") or "")
            for item in release.get("assets") or []
            if isinstance(item, dict)
        }
        if name not in assets:
            raise UpdateError("signed release artifact is missing")
        normalized["artifact"]["url"] = _safe_release_url(assets[name])
    return normalized


def _github_json(client: httpx.Client, url: str) -> Any:
    try:
        response = client.get(url, headers={"Accept": "application/vnd.github+json"})
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise UpdateError("GitHub release metadata is unavailable") from exc


def _get_release_asset(
    client: httpx.Client,
    url: str,
    *,
    accept: str,
    max_bytes: int,
) -> bytes:
    """Fetch a small GitHub asset while validating every redirect target."""
    current = _safe_release_url(url)
    for _ in range(6):
        try:
            response = client.get(current, headers={"Accept": accept})
        except httpx.HTTPError as exc:
            raise UpdateError("GitHub release asset is unavailable") from exc
        if response.status_code in {301, 302, 303, 307, 308}:
            location = response.headers.get("location")
            if not location:
                raise UpdateError("release asset redirect has no destination")
            current = _safe_release_url(urljoin(current, location))
            continue
        try:
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise UpdateError("GitHub release asset is unavailable") from exc
        if len(response.content) > max_bytes:
            raise UpdateError("release asset is oversized")
        return response.content
    raise UpdateError("release asset exceeded the redirect limit")


def discover_release(channel: str | None = None) -> dict[str, Any]:
    """Return the newest signed candidate from the configured GitHub channel."""
    selected = (
        (channel or os.getenv("PANDAMONIUM_UPDATE_CHANNEL") or "stable").strip().lower()
    )
    if selected not in {"stable", "prerelease"}:
        raise UpdateError("PANDAMONIUM_UPDATE_CHANNEL must be stable or prerelease")
    with httpx.Client(timeout=10.0, follow_redirects=False) as client:
        if selected == "stable":
            release = _github_json(client, f"{RELEASES_API}/latest")
        else:
            releases = _github_json(client, f"{RELEASES_API}?per_page=20")
            if not isinstance(releases, list):
                raise UpdateError("prerelease metadata is invalid")
            release = next(
                (
                    item
                    for item in releases
                    if isinstance(item, dict)
                    and item.get("prerelease")
                    and not item.get("draft")
                ),
                None,
            )
            if release is None:
                raise UpdateError("no prerelease is available")
        if not isinstance(release, dict):
            raise UpdateError("release metadata is invalid")
        tag = str(release.get("tag_name") or "")
        latest = tag.removeprefix("v")
        version_tuple(latest)
        if version_tuple(latest) <= version_tuple(APP_VERSION):
            return {
                "version": latest,
                "tag": tag,
                "channel": selected,
                "release_url": str(release.get("html_url") or ""),
                "current": True,
            }
        manifest_name = "pandamonium-release.json"
        manifest_url = next(
            (
                str(item.get("browser_download_url") or "")
                for item in release.get("assets") or []
                if isinstance(item, dict) and item.get("name") == manifest_name
            ),
            "",
        )
        if not manifest_url:
            raise UpdateError("newer release has no signed update manifest")
        content = _get_release_asset(
            client,
            manifest_url,
            accept="application/json",
            max_bytes=128 * 1024,
        )
        try:
            manifest_value = json.loads(content)
        except (UnicodeDecodeError, ValueError) as exc:
            raise UpdateError("release manifest is invalid JSON") from exc
        manifest = verify_release_manifest(manifest_value, release=release)
        manifest["release_url"] = str(release.get("html_url") or "")
        manifest["current"] = False
        return manifest


def current_revision(root: Path = ROOT) -> str | None:
    revision_file = root / "SOURCE_REVISION"
    try:
        value = revision_file.read_text(encoding="utf-8").strip()
        if _COMMIT_RE.fullmatch(value):
            return value
    except OSError:
        pass
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        value = result.stdout.strip()
        return value if result.returncode == 0 and _COMMIT_RE.fullmatch(value) else None
    except (OSError, subprocess.SubprocessError):
        return None


def managed_install_root(root: Path = ROOT) -> Path | None:
    configured = os.getenv("PANDAMONIUM_UPDATE_ROOT")
    if configured:
        return Path(configured).resolve()
    if root.parent.name == "releases":
        return root.parent.parent.resolve()
    return None


def installation_status(root: Path = ROOT) -> dict[str, Any]:
    install_root = managed_install_root(root)
    trigger = (os.getenv("PANDAMONIUM_UPDATE_TRIGGER") or "disabled").strip().lower()
    supported = bool(
        install_root
        and (install_root / "current").is_symlink()
        and trigger == "systemd-path"
        and sys.platform.startswith("linux")
        and not Path("/.dockerenv").exists()
    )
    reason = None
    if Path("/.dockerenv").exists():
        reason = "Container updates must be run from the host."
    elif not install_root or not (install_root / "current").is_symlink():
        reason = "Atomic updates require a managed immutable-release install."
    elif not sys.platform.startswith("linux"):
        reason = "Atomic installation is supported only on managed Linux hosts."
    elif trigger != "systemd-path":
        reason = "The root-owned systemd updater trigger is not configured."
    return {
        "supported": supported,
        "reason": reason,
        "kind": "managed-native" if install_root else "source-checkout",
        "root": str(install_root) if install_root else None,
        "trigger": trigger,
    }


def read_update_state(path: Path | None = None) -> dict[str, Any]:
    path = path or STATE_PATH
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def write_update_state(
    state: dict[str, Any], path: Path | None = None
) -> dict[str, Any]:
    path = path or STATE_PATH
    state = {**state, "schema_version": STATE_SCHEMA, "updated_at": utc_now()}
    atomic_write_json(str(path), state, indent=2)
    return state


def public_update_state() -> dict[str, Any]:
    state = read_update_state()
    return {
        "schema_version": STATE_SCHEMA,
        "status": state.get("status", "idle"),
        "phase": state.get("phase"),
        "progress": state.get("progress", 0),
        "message": state.get("message"),
        "target_version": state.get("target_version"),
        "target_commit": state.get("target_commit"),
        "previous_release": state.get("previous_release"),
        "backup_location": state.get("backup_location"),
        "rollback_available": bool(state.get("rollback_available")),
        "auto_rolled_back": bool(state.get("auto_rolled_back")),
        "rollback_error": state.get("rollback_error"),
        "history": list(state.get("history") or [])[-20:],
        "updated_at": state.get("updated_at"),
    }


def queue_update(
    candidate: dict[str, Any], *, action: str = "update"
) -> dict[str, Any]:
    install = installation_status()
    if not install["supported"]:
        raise UpdateError(str(install["reason"]))
    state = read_update_state()
    if state.get("status") in {"queued", "running"}:
        raise UpdateError("an update operation is already active")
    request_id = uuid.uuid4().hex
    request: dict[str, Any] = {
        "schema_version": REQUEST_SCHEMA,
        "request_id": request_id,
        "action": action,
        "requested_at": utc_now(),
    }
    if action == "update":
        if candidate.get("current") or not candidate.get("artifact", {}).get("url"):
            raise UpdateError("no verified update is available")
        request.update(
            {
                "target_version": candidate["version"],
                "target_commit": candidate["commit"],
                "channel": candidate["channel"],
            }
        )
    elif action == "rollback":
        if not state.get("rollback_available") or not state.get("previous_release"):
            raise UpdateError("no verified rollback is available")
        request.update(
            {
                "previous_release": state["previous_release"],
                "backup_location": state.get("backup_location"),
                "data_restore_required": bool(state.get("data_restore_required")),
            }
        )
    else:
        raise UpdateError("unsupported update action")
    write_update_state(
        {
            **state,
            "request_id": request_id,
            "status": "queued",
            "phase": "queued",
            "progress": 0,
            "message": "Update queued" if action == "update" else "Rollback queued",
            "target_version": request.get("target_version"),
            "target_commit": request.get("target_commit"),
            "auto_rolled_back": False,
        }
    )
    atomic_write_json(str(REQUEST_PATH), request, indent=2)
    return public_update_state()


@dataclass(frozen=True)
class UpdateConfig:
    install_root: Path
    data_dir: Path
    backup_root: Path
    service: str
    health_url: str
    config_files: tuple[Path, ...] = ()

    @classmethod
    def from_env(cls) -> UpdateConfig:
        if os.geteuid() != 0:
            raise UpdateError("release apply and rollback must run as root")
        install_root = managed_install_root()
        if install_root is None:
            raise UpdateError("PANDAMONIUM_UPDATE_ROOT is required for apply")
        if install_root == Path("/") or not (install_root / "current").is_symlink():
            raise UpdateError("PANDAMONIUM_UPDATE_ROOT is not a managed install")
        service = (
            os.getenv("PANDAMONIUM_UPDATE_SERVICE") or "pandamonium.service"
        ).strip()
        if not _SERVICE_RE.fullmatch(service):
            raise UpdateError("PANDAMONIUM_UPDATE_SERVICE is invalid")
        backup_root = Path(
            os.getenv("PANDAMONIUM_UPDATE_BACKUP_DIR")
            or (Path(DATA_DIR).resolve().parent / "backups")
        ).resolve()
        try:
            port = int(os.getenv("APP_PORT") or "7000")
        except ValueError as exc:
            raise UpdateError("APP_PORT is invalid") from exc
        if not 1 <= port <= 65535:
            raise UpdateError("APP_PORT is invalid")
        config_files = tuple(
            Path(item).resolve()
            for item in (os.getenv("PANDAMONIUM_UPDATE_CONFIG_FILES") or "").split(":")
            if item.strip()
        )
        data_dir = Path(DATA_DIR).resolve()
        if data_dir == Path("/") or not data_dir.is_dir():
            raise UpdateError("the canonical data directory is invalid")
        if backup_root == Path("/") or backup_root.is_relative_to(data_dir):
            raise UpdateError("the update backup directory is unsafe")
        return cls(
            install_root=install_root,
            data_dir=data_dir,
            backup_root=backup_root,
            service=service,
            health_url=f"http://127.0.0.1:{port}",
            config_files=config_files,
        )


class UpdateExecutor:
    """Execute one verified update while preserving one rollback point."""

    def __init__(self, config: UpdateConfig):
        self.config = config
        self.current_link = config.install_root / "current"
        self.releases_dir = config.install_root / "releases"
        self.venvs_dir = config.install_root / "venvs"
        self._previous_release: Path | None = None
        self._backup_dir: Path | None = None
        self._live_migration_started = False
        self._service_stopped = False
        self._switched = False

    def _state(self, phase: str, progress: int, message: str, **extra: Any) -> None:
        current = read_update_state()
        write_update_state(
            {
                **current,
                "status": "running",
                "phase": phase,
                "progress": progress,
                "message": message,
                **extra,
            }
        )

    def _run(
        self,
        args: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout: int = 1800,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            args,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode:
            detail = (
                (result.stderr or result.stdout or "command failed")
                .strip()
                .splitlines()[-1][:500]
            )
            raise UpdateError(detail)
        return result

    def _service(self, action: str) -> None:
        self._run(["systemctl", action, self.config.service], timeout=120)
        if action == "stop":
            self._service_stopped = True
        elif action in {"start", "restart"}:
            self._service_stopped = False

    def _healthy(self, version: str, *, timeout: int = 75) -> bool:
        deadline = time.monotonic() + timeout
        with httpx.Client(timeout=3.0) as client:
            while time.monotonic() < deadline:
                try:
                    health = client.get(f"{self.config.health_url}/api/health")
                    release = client.get(f"{self.config.health_url}/api/version")
                    if (
                        health.status_code == 200
                        and health.json().get("status") == "healthy"
                        and release.json().get("version") == version
                    ):
                        return True
                except (httpx.HTTPError, ValueError):
                    pass
                time.sleep(1)
        return False

    def _download(self, manifest: dict[str, Any], target: Path) -> None:
        artifact = manifest["artifact"]
        expected_size = int(artifact["size"])
        target.parent.mkdir(parents=True, exist_ok=True)
        total = 0
        current = _safe_release_url(artifact["url"])
        with httpx.Client(timeout=60.0, follow_redirects=False) as client:
            for _ in range(6):
                with client.stream("GET", current) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise UpdateError(
                                "release artifact redirect has no destination"
                            )
                        current = _safe_release_url(urljoin(current, location))
                        continue
                    response.raise_for_status()
                    with target.open("wb") as handle:
                        for chunk in response.iter_bytes(1024 * 1024):
                            total += len(chunk)
                            if total > expected_size or total > _MAX_ARCHIVE_BYTES:
                                raise UpdateError(
                                    "release artifact exceeded its signed size"
                                )
                            handle.write(chunk)
                    break
            else:
                raise UpdateError("release artifact exceeded the redirect limit")
        if total != expected_size or sha256_file(target) != artifact["sha256"]:
            raise UpdateError("release artifact checksum verification failed")

    def _extract_release(self, archive: Path, target: Path) -> Path:
        target.mkdir(parents=True, exist_ok=False)
        symlinks: list[tuple[Path, str]] = []
        count = 0
        total = 0
        try:
            with tarfile.open(archive, "r:gz") as tar:
                for member in tar:
                    count += 1
                    total += max(0, member.size)
                    rel = PurePosixPath(member.name)
                    if count > _MAX_ARCHIVE_FILES or total > _MAX_EXTRACTED_BYTES:
                        raise UpdateError("release archive exceeds extraction limits")
                    if (
                        rel.is_absolute()
                        or ".." in rel.parts
                        or not rel.parts
                        or rel.parts[0] != "pandamonium"
                    ):
                        raise UpdateError("release archive contains an unsafe path")
                    destination = target.joinpath(*rel.parts)
                    if member.isdir():
                        destination.mkdir(parents=True, exist_ok=True)
                    elif member.isfile():
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        source = tar.extractfile(member)
                        if source is None:
                            raise UpdateError(
                                "release archive contains an unreadable file"
                            )
                        with source, destination.open("wb") as handle:
                            shutil.copyfileobj(source, handle)
                        destination.chmod(member.mode & 0o755)
                    elif member.issym():
                        link = PurePosixPath(member.linkname)
                        if link.is_absolute() or ".." in link.parts:
                            raise UpdateError(
                                "release archive contains an unsafe symlink"
                            )
                        symlinks.append((destination, member.linkname))
                    else:
                        raise UpdateError(
                            "release archive contains an unsupported member"
                        )
            for destination, link in symlinks:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.symlink_to(link)
            for directory in (
                target,
                *(path for path in target.rglob("*") if path.is_dir()),
            ):
                if not directory.is_symlink():
                    directory.chmod(0o755)
            root = target / "pandamonium"
            if not root.is_dir():
                raise UpdateError("release archive has no Pandamonium root")
            return root
        except Exception:
            shutil.rmtree(target, ignore_errors=True)
            raise

    def _prepare_runtime(self, candidate: Path, manifest: dict[str, Any]) -> None:
        if current_revision(candidate) != manifest["commit"]:
            raise UpdateError("extracted release revision does not match its signature")
        version_text = (candidate / "src" / "constants.py").read_text(encoding="utf-8")
        if f'APP_VERSION = "{manifest["version"]}"' not in version_text:
            raise UpdateError("extracted release version does not match its signature")
        requirements = candidate / "requirements.txt"
        if sha256_file(requirements) != manifest["artifact"]["requirements_sha256"]:
            raise UpdateError("release requirements checksum does not match")
        current = self.current_link.resolve(strict=True)
        assert_immutable_tree(current, "current release")
        assert_immutable_tree(candidate, "candidate release")
        current_requirements = current / "requirements.txt"
        if current_requirements.is_file() and sha256_file(
            current_requirements
        ) == sha256_file(requirements):
            current_venv = (current / "venv").resolve(strict=True)
            assert_immutable_tree(current_venv, "current release runtime")
            candidate_venv = candidate / "venv"
            if candidate_venv.is_symlink():
                if candidate_venv.resolve(strict=True) != current_venv:
                    raise UpdateError("existing release has a mismatched runtime")
            elif candidate_venv.exists():
                raise UpdateError(
                    "existing release runtime is not an immutable symlink"
                )
            else:
                candidate_venv.symlink_to(current_venv)
        else:
            venv = self.venvs_dir / f"{manifest['version']}-{manifest['commit'][:8]}"
            runtime_marker = venv / ".pandamonium-runtime.json"
            expected_runtime = {
                "schema_version": "pandamonium.runtime.v1",
                "requirements_sha256": manifest["artifact"]["requirements_sha256"],
            }
            if venv.exists():
                try:
                    installed_runtime = json.loads(
                        runtime_marker.read_text(encoding="utf-8")
                    )
                except (OSError, ValueError) as exc:
                    raise UpdateError(
                        "existing release runtime is incomplete or unverified"
                    ) from exc
                if (
                    installed_runtime != expected_runtime
                    or not (venv / "bin" / "python").is_file()
                ):
                    raise UpdateError(
                        "existing release runtime is incomplete or unverified"
                    )
            else:
                self.venvs_dir.mkdir(parents=True, exist_ok=True)
                staged_venv = self.venvs_dir / (
                    f".{venv.name}.staging-{os.getpid()}-{uuid.uuid4().hex[:8]}"
                )
                try:
                    self._run(
                        [sys.executable, "-m", "venv", str(staged_venv)],
                        timeout=300,
                    )
                    self._run(
                        [
                            str(staged_venv / "bin" / "python"),
                            "-m",
                            "pip",
                            "install",
                            "-r",
                            str(requirements),
                        ],
                        timeout=1800,
                    )
                    atomic_write_json(
                        str(staged_venv / ".pandamonium-runtime.json"),
                        expected_runtime,
                        indent=2,
                    )
                    assert_immutable_tree(staged_venv, "staged release runtime")
                    os.replace(staged_venv, venv)
                finally:
                    shutil.rmtree(staged_venv, ignore_errors=True)
            candidate_venv = candidate / "venv"
            if candidate_venv.is_symlink():
                if candidate_venv.resolve(strict=True) != venv.resolve(strict=True):
                    raise UpdateError("existing release has a mismatched runtime")
            elif candidate_venv.exists():
                raise UpdateError(
                    "existing release runtime is not an immutable symlink"
                )
            else:
                candidate_venv.symlink_to(venv)
        if (current / ".env").is_file():
            shutil.copy2(current / ".env", candidate / ".env")

    def _backup(self, previous: Path, manifest: dict[str, Any]) -> tuple[Path, Path]:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_dir = (
            self.config.backup_root
            / f"update-{manifest['version']}-{manifest['commit'][:8]}-{stamp}"
        )
        backup_dir.mkdir(parents=True, mode=0o700)
        archive = backup_dir / "data.tar.gz"
        env = {
            **os.environ,
            "PANDAMONIUM_DATA_DIR": str(self.config.data_dir),
            "ODYSSEUS_DATA_DIR": str(self.config.data_dir),
        }
        result = self._run(
            [
                sys.executable,
                str(previous / "scripts" / "pandamonium-backup"),
                "snapshot",
                "--out",
                str(archive),
                "--include-research",
                "--include-attachments",
            ],
            cwd=previous,
            env=env,
        )
        snapshot = json.loads(result.stdout)
        if not snapshot.get("ok"):
            raise UpdateError("data backup did not complete")
        verified = self._run(
            [
                sys.executable,
                str(previous / "scripts" / "pandamonium-backup"),
                "verify",
                str(archive),
            ],
            cwd=previous,
            env=env,
        )
        if not json.loads(verified.stdout).get("ok"):
            raise UpdateError("data backup verification failed")
        config_dir = backup_dir / "config"
        for source in (previous / ".env", *self.config.config_files):
            if source.is_file():
                config_dir.mkdir(exist_ok=True)
                shutil.copy2(source, config_dir / source.name)
        metadata = {
            "schema_version": "pandamonium.update-backup.v1",
            "created_at": utc_now(),
            "previous_release": str(previous),
            "target_version": manifest["version"],
            "target_commit": manifest["commit"],
            "data_archive": str(archive),
            "data_sha256": sha256_file(archive),
            "config_files": sorted(item.name for item in config_dir.iterdir())
            if config_dir.exists()
            else [],
        }
        atomic_write_json(str(backup_dir / "update-backup.json"), metadata, indent=2)
        (backup_dir / "SHA256SUMS").write_text(
            f"{metadata['data_sha256']}  data.tar.gz\n", encoding="utf-8"
        )
        archive.chmod(0o600)
        return backup_dir, archive

    def _extract_data_backup(self, archive: Path, target_parent: Path) -> Path:
        target = target_parent / "data"
        owner = None
        root_mode = None
        if os.geteuid() == 0 and self.config.data_dir.exists():
            data_stat = self.config.data_dir.stat()
            owner = (data_stat.st_uid, data_stat.st_gid)
            root_mode = data_stat.st_mode
        with tarfile.open(archive, "r:gz") as tar:
            members = tar.getmembers()
            directories: list[tuple[Path, tarfile.TarInfo]] = []
            for member in members:
                rel = PurePosixPath(member.name)
                if (
                    rel.is_absolute()
                    or ".." in rel.parts
                    or not rel.parts
                    or rel.parts[0] != "data"
                    or (len(rel.parts) == 1 and not member.isdir())
                    or not (member.isdir() or member.isfile())
                ):
                    raise UpdateError("data backup contains an unsafe member")
                destination = target_parent.joinpath(*rel.parts)
                if member.isdir():
                    destination.mkdir(parents=True, mode=0o700, exist_ok=True)
                    directories.append((destination, member))
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                source = tar.extractfile(member)
                if source is None:
                    raise UpdateError("data backup contains an unreadable file")
                with source, destination.open("wb") as handle:
                    shutil.copyfileobj(source, handle)
                destination.chmod((member.mode & 0o777) or 0o600)
                if owner is not None:
                    os.chown(destination, *owner)
            for destination, member in reversed(directories):
                destination.chmod(((member.mode & 0o777) or 0o755) | 0o700)
                if owner is not None:
                    os.chown(destination, *owner)
            if target.is_dir():
                for directory in (item for item in target.rglob("*") if item.is_dir()):
                    if owner is not None:
                        os.chown(directory, *owner)
                if root_mode is not None:
                    target.chmod((root_mode & 0o777) | 0o700)
                if owner is not None:
                    os.chown(target, *owner)
        return target

    def _migrate(self, candidate: Path, data_dir: Path) -> None:
        env = {
            **os.environ,
            "PANDAMONIUM_DATA_DIR": str(data_dir),
            "ODYSSEUS_DATA_DIR": str(data_dir),
            "PANDAMONIUM_INPROCESS_POLLERS": "0",
            "PANDAMONIUM_INPROCESS_TASKS": "0",
        }
        python = str(candidate / "venv" / "bin" / "python")
        for _ in range(2):
            self._run(
                [python, "-c", "import core.database"],
                cwd=candidate,
                env=env,
                timeout=300,
            )
        for database in data_dir.rglob("*.db"):
            try:
                with sqlite3.connect(database) as connection:
                    if connection.execute("PRAGMA integrity_check").fetchone() != (
                        "ok",
                    ):
                        raise UpdateError(
                            f"database integrity failed for {database.name}"
                        )
            except sqlite3.DatabaseError as exc:
                raise UpdateError(
                    f"database integrity failed for {database.name}"
                ) from exc

    def _atomic_switch(self, target: Path) -> None:
        resolved_target = target.resolve(strict=True)
        if (
            target.is_symlink()
            or not resolved_target.is_dir()
            or resolved_target.parent != self.releases_dir.resolve()
        ):
            raise UpdateError("release switch target is outside the immutable store")
        temporary = self.config.install_root / f".current.{os.getpid()}"
        temporary.unlink(missing_ok=True)
        temporary.symlink_to(resolved_target)
        os.replace(temporary, self.current_link)
        self._switched = True

    @staticmethod
    def _release_version(release: Path) -> str:
        try:
            source = (release / "src" / "constants.py").read_text(encoding="utf-8")
        except OSError as exc:
            raise UpdateError("installed release has no readable version") from exc
        match = re.search(r'^APP_VERSION = "([^"]+)"', source, re.MULTILINE)
        if not match:
            raise UpdateError("installed release has no valid version")
        version_tuple(match.group(1))
        return match.group(1)

    def _check_compatibility(self, manifest: dict[str, Any], previous: Path) -> None:
        current_version = self._release_version(previous)
        minimum_version = str(manifest["compatibility"]["minimum_version"])
        required_python = str(manifest["compatibility"]["minimum_python"])
        python_match = _PYTHON_VERSION_RE.fullmatch(required_python)
        if python_match is None:  # already rejected by signature verification
            raise UpdateError("release Python compatibility is invalid")
        if version_tuple(manifest["version"]) <= version_tuple(current_version):
            raise UpdateError("signed release is not newer than the installed release")
        if version_tuple(current_version) < version_tuple(minimum_version):
            raise UpdateError(
                f"manual upgrade required from versions older than v{minimum_version}"
            )
        if sys.version_info[:2] < tuple(int(part) for part in python_match.groups()):
            raise UpdateError(f"Python {required_python}+ is required")

    def _restore_data(self, archive: Path) -> Path:
        metadata_path = archive.parent / "update-backup.json"
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise UpdateError("rollback backup metadata is unreadable") from exc
        if (
            not isinstance(metadata, dict)
            or metadata.get("schema_version") != "pandamonium.update-backup.v1"
            or metadata.get("data_sha256") != sha256_file(archive)
        ):
            raise UpdateError("rollback backup checksum verification failed")
        failed = self.config.data_dir.with_name(
            f"{self.config.data_dir.name}.failed-update-"
            f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
        )
        parent = self.config.data_dir.parent
        with tempfile.TemporaryDirectory(dir=parent) as tmp:
            restored = self._extract_data_backup(archive, Path(tmp))
            if self.config.data_dir.exists():
                os.replace(self.config.data_dir, failed)
            os.replace(restored, self.config.data_dir)
        return failed

    def _rollback_after_failure(self, archive: Path | None) -> None:
        changed_live_state = self._switched or self._live_migration_started
        if not changed_live_state:
            return
        if not self._service_stopped:
            self._service("stop")
        if self._switched and self._previous_release is not None:
            self._atomic_switch(self._previous_release)
        if self._live_migration_started and archive is not None:
            self._restore_data(archive)
        self._service("start")
        if self._previous_release is None or not self._healthy(
            self._release_version(self._previous_release)
        ):
            raise UpdateError(
                "automatically rolled-back release failed its health check"
            )

    def apply(
        self, manifest: dict[str, Any], archive: Path | None = None
    ) -> dict[str, Any]:
        manifest = verify_release_manifest(manifest)
        self._previous_release = self.current_link.resolve(strict=True)
        if current_revision(self._previous_release) == manifest["commit"]:
            return write_update_state(
                {
                    **read_update_state(),
                    "status": "current",
                    "phase": "complete",
                    "progress": 100,
                    "message": "Already on the requested release",
                }
            )
        self._check_compatibility(manifest, self._previous_release)
        work = Path(
            tempfile.mkdtemp(prefix="pandamonium-update-", dir=self.config.install_root)
        )
        downloaded = work / manifest["artifact"]["name"]
        data_archive: Path | None = None
        try:
            self._state(
                "download",
                10,
                "Downloading signed release",
                previous_release=str(self._previous_release),
                target_version=manifest["version"],
                target_commit=manifest["commit"],
                data_restore_required=manifest["compatibility"][
                    "data_restore_required"
                ],
            )
            if archive is None:
                self._download(manifest, downloaded)
            else:
                shutil.copy2(archive, downloaded)
                if (
                    downloaded.stat().st_size != manifest["artifact"]["size"]
                    or sha256_file(downloaded) != manifest["artifact"]["sha256"]
                ):
                    raise UpdateError("release artifact checksum verification failed")
            self._state("stage", 25, "Verifying and staging immutable release")
            extracted = self._extract_release(downloaded, work / "extract")
            release = (
                self.releases_dir / f"{manifest['version']}-{manifest['commit'][:8]}"
            )
            if release.exists():
                if current_revision(release) != manifest["commit"]:
                    raise UpdateError(
                        "target release path already contains another revision"
                    )
                shutil.rmtree(extracted.parent)
            else:
                release.parent.mkdir(parents=True, exist_ok=True)
                os.replace(extracted, release)
            self._prepare_runtime(release, manifest)
            self._state("backup", 40, "Creating and verifying full data backup")
            self._backup_dir, data_archive = self._backup(
                self._previous_release, manifest
            )
            self._state(
                "rehearsal",
                55,
                "Rehearsing migrations twice on backup data",
                backup_location=str(self._backup_dir),
            )
            with tempfile.TemporaryDirectory(
                prefix="pandamonium-rehearsal-"
            ) as rehearsal:
                rehearsal_data = self._extract_data_backup(
                    data_archive, Path(rehearsal)
                )
                self._migrate(release, rehearsal_data)
            self._state(
                "migration", 65, "Stopping service and applying idempotent migrations"
            )
            self._service("stop")
            self._live_migration_started = True
            self._migrate(release, self.config.data_dir)
            self._state("activate", 78, "Switching the immutable release atomically")
            self._atomic_switch(release)
            self._service("start")
            self._state("health", 90, "Checking the new release")
            if not self._healthy(manifest["version"]):
                raise UpdateError("new release failed its health check")
            history = list(read_update_state().get("history") or [])[-19:]
            history.append(
                {
                    "action": "update",
                    "status": "succeeded",
                    "at": utc_now(),
                    "version": manifest["version"],
                    "commit": manifest["commit"],
                    "backup_location": str(self._backup_dir),
                }
            )
            return write_update_state(
                {
                    **read_update_state(),
                    "status": "succeeded",
                    "phase": "complete",
                    "progress": 100,
                    "message": f"Updated to v{manifest['version']}",
                    "previous_release": str(self._previous_release),
                    "backup_location": str(self._backup_dir),
                    "rollback_available": True,
                    "data_restore_required": manifest["compatibility"][
                        "data_restore_required"
                    ],
                    "auto_rolled_back": False,
                    "history": history,
                }
            )
        except BaseException as exc:
            rollback_error = None
            try:
                self._rollback_after_failure(data_archive)
            except Exception as rollback_exc:  # noqa: BLE001 - preserve both failures
                rollback_error = str(rollback_exc)
            history = list(read_update_state().get("history") or [])[-19:]
            history.append(
                {
                    "action": "update",
                    "status": "failed",
                    "at": utc_now(),
                    "version": manifest.get("version"),
                    "error": str(exc)[:500],
                    "auto_rolled_back": rollback_error is None
                    and (self._switched or self._live_migration_started),
                }
            )
            write_update_state(
                {
                    **read_update_state(),
                    "status": "failed",
                    "phase": "rollback" if rollback_error else "complete",
                    "message": str(exc)[:500],
                    "progress": 100,
                    "previous_release": str(self._previous_release)
                    if self._previous_release
                    else None,
                    "backup_location": str(self._backup_dir)
                    if self._backup_dir
                    else None,
                    "rollback_available": False,
                    "auto_rolled_back": rollback_error is None
                    and (self._switched or self._live_migration_started),
                    "rollback_error": rollback_error,
                    "history": history,
                }
            )
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise UpdateError(str(exc)) from exc
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def rollback(self, request: dict[str, Any]) -> dict[str, Any]:
        target = Path(str(request.get("previous_release") or "")).resolve()
        if target.parent != self.releases_dir.resolve() or not target.is_dir():
            raise UpdateError("rollback target is not an installed immutable release")
        current = self.current_link.resolve(strict=True)
        backup_dir = (
            Path(str(request.get("backup_location") or "")).resolve()
            if request.get("backup_location")
            else None
        )
        if backup_dir is not None and (
            backup_dir.parent != self.config.backup_root.resolve()
            or backup_dir.is_symlink()
            or not backup_dir.is_dir()
        ):
            raise UpdateError("rollback backup is outside the configured backup store")
        archive = backup_dir / "data.tar.gz" if backup_dir else None
        displaced_data: Path | None = None
        self._state("rollback", 35, "Stopping service for rollback")
        self._service("stop")
        try:
            if request.get("data_restore_required"):
                if archive is None or not archive.is_file():
                    raise UpdateError("rollback requires its verified data backup")
                displaced_data = self._restore_data(archive)
            self._atomic_switch(target)
            self._service("start")
            version_text = (target / "src" / "constants.py").read_text(encoding="utf-8")
            match = re.search(r'^APP_VERSION = "([^"]+)"', version_text, re.MULTILINE)
            version = match.group(1) if match else ""
            if not version or not self._healthy(version):
                raise UpdateError("rolled-back release failed its health check")
        except Exception:
            self._atomic_switch(current)
            if displaced_data is not None and displaced_data.exists():
                rejected = self.config.data_dir.with_name(
                    f"{self.config.data_dir.name}.rejected-rollback-"
                    f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-"
                    f"{uuid.uuid4().hex[:8]}"
                )
                if self.config.data_dir.exists():
                    os.replace(self.config.data_dir, rejected)
                os.replace(displaced_data, self.config.data_dir)
            self._service("start")
            raise
        history = list(read_update_state().get("history") or [])[-19:]
        history.append(
            {
                "action": "rollback",
                "status": "succeeded",
                "at": utc_now(),
                "release": str(target),
            }
        )
        return write_update_state(
            {
                **read_update_state(),
                "status": "rolled_back",
                "phase": "complete",
                "progress": 100,
                "message": f"Rolled back to {target.name}",
                "previous_release": str(current),
                "rollback_available": False,
                "auto_rolled_back": False,
                "history": history,
            }
        )


def load_request(path: Path) -> dict[str, Any]:
    try:
        request = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UpdateError("update request is unreadable") from exc
    if not isinstance(request, dict) or request.get("schema_version") != REQUEST_SCHEMA:
        raise UpdateError("update request is invalid")
    path.unlink(missing_ok=True)
    return request


def execute_request(path: Path) -> dict[str, Any]:
    try:
        request = load_request(path)
        executor = UpdateExecutor(UpdateConfig.from_env())
        if request.get("action") == "rollback":
            return executor.rollback(request)
        if request.get("action") != "update":
            raise UpdateError("update request action is invalid")
        candidate = discover_release(str(request.get("channel") or "stable"))
        if (
            candidate.get("current")
            or candidate.get("version") != request.get("target_version")
            or candidate.get("commit") != request.get("target_commit")
        ):
            raise UpdateError(
                "available release changed after owner approval; check again"
            )
        return executor.apply(candidate)
    except BaseException as exc:
        state = read_update_state()
        if state.get("status") != "failed":
            write_update_state(
                {
                    **state,
                    "status": "failed",
                    "phase": "complete",
                    "progress": 100,
                    "message": str(exc)[:500],
                    "auto_rolled_back": False,
                }
            )
        raise


def recover_interrupted() -> dict[str, Any]:
    state = read_update_state()
    if state.get("status") != "running" or not state.get("previous_release"):
        return state
    request = {
        "previous_release": state["previous_release"],
        "backup_location": state.get("backup_location"),
        "data_restore_required": bool(state.get("data_restore_required")),
    }
    result = UpdateExecutor(UpdateConfig.from_env()).rollback(request)
    return write_update_state(
        {**result, "status": "recovered", "message": "Recovered interrupted update"}
    )
