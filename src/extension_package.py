"""Bounded source packages. Extraction never executes repository code."""

from __future__ import annotations

import gzip
import hashlib
import io
import shutil
import stat
import tarfile
import tempfile
import zlib
from itertools import islice
from pathlib import Path, PurePosixPath

MAX_PACKAGE_BYTES = 512 * 1024 * 1024
MAX_PACKAGE_FILES = 50_000
MANIFEST_NAME = "jarvis-extension.json"


class PackageError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _safe_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if (not name or len(name) > 2048 or path.is_absolute()
            or any(part.casefold() in {"..", ".git"} for part in path.parts)
            or "\\" in name or ":" in name or "\x00" in name):
        raise PackageError("extension_package_path_invalid")
    return path


def extract_package(content: bytes, destination: Path) -> None:
    """Accept a flat archive or a git-archive-style single enclosing directory."""
    if not content or len(content) > MAX_PACKAGE_BYTES:
        raise PackageError("extension_package_size_invalid")
    if destination.exists():
        raise PackageError("extension_package_destination_exists")
    try:
        # Bound decompression before tarfile parses attacker-controlled PAX headers.
        with tempfile.TemporaryFile() as expanded:
            with gzip.GzipFile(fileobj=io.BytesIO(content), mode="rb") as compressed:
                total = 0
                for chunk in iter(lambda: compressed.read(1024 * 1024), b""):
                    total += len(chunk)
                    if total > MAX_PACKAGE_BYTES:
                        raise PackageError("extension_package_size_invalid")
                    expanded.write(chunk)
            expanded.seek(0)
            with tarfile.open(fileobj=expanded, mode="r:") as bundle:
                members: list[tarfile.TarInfo] = []
                names: set[str] = set()
                total = 0
                for member in bundle:
                    path = _safe_path(member.name)
                    if not (member.isfile() or member.isdir()) or member.sparse:
                        raise PackageError("extension_package_member_invalid")
                    key = str(path).casefold()
                    if key in names:
                        raise PackageError("extension_package_path_duplicate")
                    names.add(key)
                    members.append(member)
                    total += member.size
                    if len(members) > MAX_PACKAGE_FILES or total > MAX_PACKAGE_BYTES or member.size < 0:
                        raise PackageError("extension_package_size_invalid")
                manifests = [PurePosixPath(m.name) for m in members if m.isfile()
                             and PurePosixPath(m.name).name == MANIFEST_NAME
                             and len(PurePosixPath(m.name).parts) <= 2]
                if len(manifests) != 1:
                    raise PackageError("extension_package_manifest_missing")
                prefix = manifests[0].parent
                destination.mkdir(parents=True)
                for member in members:
                    path = PurePosixPath(member.name)
                    if not path.is_relative_to(prefix):
                        raise PackageError("extension_package_path_invalid")
                    relative = path.relative_to(prefix)
                    target = destination.joinpath(*relative.parts)
                    if member.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        source = bundle.extractfile(member)
                        if source is None:
                            raise PackageError("extension_package_member_invalid")
                        with source, target.open("xb") as output:
                            shutil.copyfileobj(source, output, length=1024 * 1024)
                        target.chmod(0o755 if member.mode & 0o111 else 0o644)
    except (OSError, ValueError, EOFError, tarfile.TarError, zlib.error) as exc:
        shutil.rmtree(destination, ignore_errors=True)
        if isinstance(exc, PackageError):
            raise
        raise PackageError("extension_package_invalid") from exc


def package_tree_digest(root: Path) -> str:
    """Bind all prepared source files and executable bits, rejecting links."""
    if root.is_symlink() or not root.is_dir():
        raise PackageError("extension_package_path_invalid")
    digest = hashlib.sha256()
    total = 0
    paths = list(islice(root.rglob("*"), MAX_PACKAGE_FILES + 1))
    if len(paths) > MAX_PACKAGE_FILES:
        raise PackageError("extension_package_size_invalid")
    names: set[str] = set()
    for path in sorted(paths):
        relative = path.relative_to(root).as_posix()
        _safe_path(relative)
        if relative.casefold() in names:
            raise PackageError("extension_package_path_duplicate")
        names.add(relative.casefold())
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise PackageError("extension_package_member_invalid")
        total += path.stat().st_size
        if total > MAX_PACKAGE_BYTES:
            raise PackageError("extension_package_size_invalid")
        digest.update(relative.encode() + b"\0" + str(bool(mode & 0o111)).encode() + b"\0")
        with path.open("rb") as source:
            file_hash = hashlib.sha256()
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                file_hash.update(chunk)
            digest.update(file_hash.digest())
    return digest.hexdigest()


def build_prepared_package(root: Path, output: Path) -> None:
    """Package an explicit prepared tree, excluding no content silently."""
    root = root.resolve()
    if output.resolve().is_relative_to(root):
        raise PackageError("extension_package_destination_invalid")
    before = package_tree_digest(root)
    for path in root.rglob("*"):
        name = path.name.lower()
        if (name == ".env" or (name.startswith(".env.") and name not in {".env.example", ".env.sample", ".env.template"})
                or name in {"credentials.json", "id_rsa", "id_ed25519"}
                or name.endswith((".pem", ".key", ".p12", ".pfx"))):
            raise PackageError("extension_package_private_file")
    try:
        with output.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as bundle:
                for path in sorted(root.rglob("*")):
                    if path.is_dir():
                        continue
                    info = tarfile.TarInfo(path.relative_to(root).as_posix())
                    info.size = path.stat().st_size
                    info.mode = 0o755 if path.stat().st_mode & 0o111 else 0o644
                    with path.open("rb") as source:
                        bundle.addfile(info, source)
                if compressed.tell() + 10240 > MAX_PACKAGE_BYTES:
                    raise PackageError("extension_package_size_invalid")
        if output.stat().st_size > MAX_PACKAGE_BYTES or package_tree_digest(root) != before:
            raise PackageError("extension_package_content_changed")
    except Exception:
        output.unlink(missing_ok=True)
        raise
