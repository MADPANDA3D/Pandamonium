"""Bounded, read-only local-folder discovery and Gallery indexing."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import threading
import uuid
from collections import defaultdict
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import or_

from core.database import GalleryImage, GallerySource, GallerySourceFile
from src.host_docker_access import running_in_container


MEDIA_ROOTS_ENV = "PANDAMONIUM_GALLERY_MEDIA_ROOTS"
LEGACY_MEDIA_ROOTS_ENV = "ODYSSEUS_GALLERY_MEDIA_ROOTS"
SINGLE_USER_OWNER = "__single_user__"
SOURCE_MODEL = "local-folder"
SUPPORTED_MEDIA_EXTENSIONS = frozenset(
    {".gif", ".jpeg", ".jpg", ".m4v", ".mkv", ".mov", ".mp4", ".png", ".webm", ".webp"}
)
DEFAULT_SCAN_LIMIT = 10_000
MAX_SCAN_LIMIT = 100_000

# ponytail: one process-wide scan lock is enough for an owner-operated app;
# use per-source locks only if concurrent multi-owner scans become measurable.
_SCAN_LOCK = threading.Lock()


def source_owner(user: str | None) -> str:
    return user or SINGLE_USER_OWNER


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _configured_scan_limit(environ: Mapping[str, str] | None = None) -> int:
    env = os.environ if environ is None else environ
    raw = env.get("PANDAMONIUM_GALLERY_SCAN_LIMIT", str(DEFAULT_SCAN_LIMIT))
    try:
        return min(MAX_SCAN_LIMIT, max(1, int(raw)))
    except (TypeError, ValueError):
        return DEFAULT_SCAN_LIMIT


def _windows_pictures_dir() -> Path | None:
    """Resolve FOLDERID_Pictures through the supported Known Folder API."""
    if os.name != "nt":
        return None
    import ctypes

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", ctypes.c_ulong),
            ("Data2", ctypes.c_ushort),
            ("Data3", ctypes.c_ushort),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    raw = uuid.UUID("33e28130-4e1e-4676-835a-98395c3bc3bb").bytes_le
    folder_id = GUID.from_buffer_copy(raw)
    value = ctypes.c_wchar_p()
    result = ctypes.windll.shell32.SHGetKnownFolderPath(  # type: ignore[attr-defined]
        ctypes.byref(folder_id), 0, None, ctypes.byref(value)
    )
    if result != 0 or not value.value:
        return None
    try:
        return Path(value.value)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(value)  # type: ignore[attr-defined]


def _linux_pictures_dir(home: Path, environ: Mapping[str, str]) -> Path | None:
    config_home = Path(environ.get("XDG_CONFIG_HOME") or home / ".config")
    config = config_home / "user-dirs.dirs"
    try:
        contents = config.read_text(encoding="utf-8")
    except OSError:
        return home / "Pictures"
    match = re.search(r'^XDG_PICTURES_DIR=(?:"([^"]*)"|([^\n#]*))', contents, re.MULTILINE)
    if not match:
        return home / "Pictures"
    value = (match.group(1) if match.group(1) is not None else match.group(2) or "").strip()
    value = value.replace("${HOME}", str(home)).replace("$HOME", str(home))
    if "$" in value or not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else home / path


def _parse_media_roots(environ: Mapping[str, str]) -> list[Path]:
    raw = (environ.get(MEDIA_ROOTS_ENV) or environ.get(LEGACY_MEDIA_ROOTS_ENV) or "").strip()
    if not raw:
        return []
    if raw.startswith("["):
        try:
            values = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if not isinstance(values, list):
            return []
    else:
        values = raw.split(os.pathsep)
    roots = []
    for value in values:
        path = Path(str(value).strip()).expanduser()
        if path.is_absolute() and path not in roots:
            roots.append(path)
    return roots


def _candidate(path: Path, *, mounted: bool | None = None) -> dict:
    safe = path.is_absolute() and path != Path(path.anchor) and not path.is_symlink()
    available = safe and path.is_dir() and os.access(path, os.R_OK | os.X_OK)
    if mounted is not None:
        available = available and mounted
    reason = None
    if not safe:
        reason = "Folder is unsafe or too broad"
    elif not path.is_dir():
        reason = "Folder is unavailable"
    elif not os.access(path, os.R_OK | os.X_OK):
        reason = "Folder is not readable"
    elif mounted is False:
        reason = "Configured container path is not an explicit mount"
    return {
        "path": str(path),
        "label": path.name or "Pictures",
        "available": available,
        "reason": reason,
    }


def discover_gallery_roots(
    *,
    platform_name: str | None = None,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
    containerized: bool | None = None,
    is_mount: Callable[[str], bool] = os.path.ismount,
    windows_resolver: Callable[[], Path | None] = _windows_pictures_dir,
) -> dict:
    """Return only the conventional native folder or explicit container mounts."""
    env = os.environ if environ is None else environ
    in_container = running_in_container() if containerized is None else containerized
    if in_container:
        candidates = [
            _candidate(path, mounted=is_mount(str(path)))
            for path in _parse_media_roots(env)
        ]
        return {
            "environment": "container",
            "candidates": candidates,
            "message": (
                "Docker can use only read-only host folders explicitly mounted into the container."
                if candidates
                else "No host photo folder is mounted. Add a read-only mount and set "
                f"{MEDIA_ROOTS_ENV}."
            ),
        }

    platform_id = (platform_name or sys.platform).lower()
    user_home = (home or Path.home()).expanduser()
    if platform_id.startswith("win"):
        path = windows_resolver()
    elif platform_id == "darwin":
        path = user_home / "Pictures"
    else:
        path = _linux_pictures_dir(user_home, env)
    candidates = [_candidate(path)] if path is not None else []
    return {
        "environment": "native",
        "candidates": candidates,
        "message": "Pandamonium uses the operating system's conventional Pictures folder.",
    }


def validate_source_root(
    path: str,
    *,
    containerized: bool | None = None,
    environ: Mapping[str, str] | None = None,
    is_mount: Callable[[str], bool] = os.path.ismount,
) -> tuple[Path, str]:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError("Folder path must be absolute and cannot contain parent traversal")
    in_container = running_in_container() if containerized is None else containerized
    if in_container:
        configured = _parse_media_roots(os.environ if environ is None else environ)
        if candidate not in configured:
            raise ValueError("Docker folders must be listed in PANDAMONIUM_GALLERY_MEDIA_ROOTS")
        status = _candidate(candidate, mounted=is_mount(str(candidate)))
        kind = "container"
    else:
        status = _candidate(candidate)
        kind = "native"
    if not status["available"]:
        raise ValueError(status["reason"] or "Folder is unavailable")
    return candidate, kind


def _walk_media_files(root: Path, limit: int) -> tuple[list[Path], bool, list[str]]:
    files: list[Path] = []
    stack = [root]
    complete = True
    errors: list[str] = []
    while stack and len(files) <= limit:
        directory = stack.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda item: item.name.casefold(), reverse=True)
        except OSError as exc:
            complete = False
            errors.append(f"{directory}: {exc.strerror or exc}")
            continue
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    stack.append(Path(entry.path))
                elif (
                    entry.is_file(follow_symlinks=False)
                    and Path(entry.name).suffix.lower() in SUPPORTED_MEDIA_EXTENSIONS
                ):
                    files.append(Path(entry.path))
                    if len(files) > limit:
                        complete = False
                        stack.clear()
                        break
            except OSError as exc:
                complete = False
                errors.append(f"{entry.path}: {exc.strerror or exc}")
    return sorted(files[:limit]), complete, errors[:5]


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _image_dimensions(path: Path) -> tuple[int | None, int | None]:
    if path.suffix.lower() not in {".gif", ".jpeg", ".jpg", ".png", ".webp"}:
        return None, None
    try:
        from PIL import Image, ImageOps
        with Image.open(path) as image:
            display = ImageOps.exif_transpose(image) or image
            return display.width, display.height
    except Exception:
        return None, None


def _owner_images(db, owner: str):
    if owner == SINGLE_USER_OWNER:
        return db.query(GalleryImage).filter(
            or_(GalleryImage.owner == None, GalleryImage.owner == SINGLE_USER_OWNER)  # noqa: E711
        )
    return db.query(GalleryImage).filter(GalleryImage.owner == owner)


def _reconcile_owner_images(db, owner: str) -> None:
    active_files = (
        db.query(GallerySourceFile)
        .join(GallerySource, GallerySource.id == GallerySourceFile.source_id)
        .filter(
            GallerySource.owner == owner,
            GallerySource.enabled == True,  # noqa: E712
            GallerySourceFile.active == True,  # noqa: E712
        )
        .order_by(GallerySourceFile.relative_path.asc())
        .all()
    )
    by_hash: dict[str, list[GallerySourceFile]] = defaultdict(list)
    for item in active_files:
        by_hash[item.file_hash].append(item)

    source_images = _owner_images(db, owner).filter(
        GalleryImage.source_file_id != None  # noqa: E711
    ).all()
    source_by_hash: dict[str, list[GalleryImage]] = defaultdict(list)
    for image in source_images:
        source_by_hash[image.file_hash or ""].append(image)

    for file_hash, files in by_hash.items():
        ordinary = _owner_images(db, owner).filter(
            GalleryImage.file_hash == file_hash,
            GalleryImage.source_file_id == None,  # noqa: E711
            GalleryImage.is_active == True,  # noqa: E712
        ).first()
        rows = source_by_hash.get(file_hash, [])
        if ordinary:
            for row in rows:
                row.is_active = False
            continue
        row = rows[0] if rows else None
        selected = files[0]
        if row is None:
            suffix = Path(selected.relative_path).suffix.lower()
            row = GalleryImage(
                id=str(uuid.uuid4()),
                filename=f"source-{uuid.uuid4().hex[:20]}{suffix}",
                prompt=Path(selected.relative_path).stem,
                model=SOURCE_MODEL,
                owner=None if owner == SINGLE_USER_OWNER else owner,
                file_hash=file_hash,
                source_file_id=selected.id,
            )
            db.add(row)
        elif row.source_file_id != selected.id:
            row.source_file_id = selected.id
            row.prompt = Path(selected.relative_path).stem
        row.is_active = True
        row.file_size = selected.file_size
        try:
            width, height = _image_dimensions(resolve_source_file(db, row, owner))
            row.width, row.height = width, height
        except FileNotFoundError:
            # A bounded/incomplete scan retains unseen metadata. The next full
            # scan resolves whether an unavailable entry was removed.
            pass
        for duplicate in rows[1:]:
            duplicate.is_active = False

    for file_hash, rows in source_by_hash.items():
        if file_hash not in by_hash:
            for row in rows:
                row.is_active = False


def scan_gallery_source(db, source: GallerySource, *, limit: int | None = None) -> dict:
    scan_limit = limit or _configured_scan_limit()
    root = Path(source.path)
    status = _candidate(
        root,
        mounted=os.path.ismount(root) if source.kind == "container" else None,
    )
    if not status["available"]:
        source.last_error = status["reason"] or "Folder is unavailable"
        return {"scanned": 0, "indexed": 0, "unchanged": 0, "removed": 0, "truncated": False, "error": source.last_error}

    with _SCAN_LOCK:
        files, complete, errors = _walk_media_files(root, scan_limit)
        existing = {
            item.relative_path: item
            for item in db.query(GallerySourceFile).filter(
                GallerySourceFile.source_id == source.id
            ).all()
        }
        seen: set[str] = set()
        indexed = unchanged = 0
        for path in files:
            relative = path.relative_to(root).as_posix()
            try:
                stat_result = path.stat(follow_symlinks=False)
                current = existing.get(relative)
                if (
                    current is not None
                    and current.active
                    and current.modified_ns == stat_result.st_mtime_ns
                    and current.file_size == stat_result.st_size
                ):
                    unchanged += 1
                else:
                    file_hash = _hash_file(path)
                    if current is None:
                        current = GallerySourceFile(
                            id=str(uuid.uuid4()),
                            source_id=source.id,
                            relative_path=relative,
                            file_hash=file_hash,
                            modified_ns=stat_result.st_mtime_ns,
                            file_size=stat_result.st_size,
                        )
                        db.add(current)
                    else:
                        current.file_hash = file_hash
                        current.modified_ns = stat_result.st_mtime_ns
                        current.file_size = stat_result.st_size
                        current.active = True
                    indexed += 1
                seen.add(relative)
            except OSError as exc:
                complete = False
                errors.append(f"{relative}: {exc.strerror or exc}")

        removed = 0
        if complete:
            for relative, item in existing.items():
                if item.active and relative not in seen:
                    item.active = False
                    removed += 1
        db.flush()
        _reconcile_owner_images(db, source.owner)
        source.last_scan_at = _utcnow()
        source.last_error = "; ".join(errors[:5]) or None
        return {
            "scanned": len(files),
            "indexed": indexed,
            "unchanged": unchanged,
            "removed": removed,
            "truncated": not complete and len(files) >= scan_limit,
            "error": source.last_error,
        }


def _serialize_source(db, source: GallerySource) -> dict:
    indexed = db.query(GallerySourceFile).filter(
        GallerySourceFile.source_id == source.id,
        GallerySourceFile.active == True,  # noqa: E712
    ).count()
    return {
        "id": source.id,
        "path": source.path,
        "label": source.label,
        "kind": source.kind,
        "enabled": bool(source.enabled),
        "auto_connected": bool(source.auto_connected),
        "indexed": indexed,
        "last_scan_at": source.last_scan_at.isoformat() if source.last_scan_at else None,
        "error": source.last_error,
    }


def source_status(db, owner: str, *, discovery: dict | None = None) -> dict:
    detected = discovery or discover_gallery_roots()
    sources = db.query(GallerySource).filter(GallerySource.owner == owner).order_by(GallerySource.created_at.asc()).all()
    return {
        "environment": detected["environment"],
        "message": detected["message"],
        "candidates": detected["candidates"],
        "sources": [_serialize_source(db, source) for source in sources],
    }


def sync_gallery_sources(db, owner: str, *, discovery: dict | None = None, limit: int | None = None) -> dict:
    detected = discovery or discover_gallery_roots()
    sources = db.query(GallerySource).filter(GallerySource.owner == owner).all()
    if not sources:
        for item in detected["candidates"]:
            if not item["available"]:
                continue
            source = GallerySource(
                id=str(uuid.uuid4()),
                owner=owner,
                path=item["path"],
                label=item["label"],
                kind=detected["environment"],
                enabled=True,
                auto_connected=True,
            )
            db.add(source)
            sources.append(source)
        db.flush()
    results = {}
    for source in sources:
        if source.enabled:
            results[source.id] = scan_gallery_source(db, source, limit=limit)
    db.commit()
    status = source_status(db, owner, discovery=detected)
    status["results"] = results
    return status


def resolve_source_file(db, image: GalleryImage, owner: str) -> Path:
    if not image.source_file_id:
        raise FileNotFoundError("Gallery image is not backed by a local source")
    item = db.query(GallerySourceFile).filter(
        GallerySourceFile.id == image.source_file_id,
        GallerySourceFile.active == True,  # noqa: E712
    ).first()
    if item is None:
        raise FileNotFoundError("Source photo is unavailable")
    source = db.query(GallerySource).filter(
        GallerySource.id == item.source_id,
        GallerySource.owner == owner,
        GallerySource.enabled == True,  # noqa: E712
    ).first()
    if source is None:
        raise FileNotFoundError("Source folder is disconnected")
    relative = Path(item.relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise FileNotFoundError("Unsafe source path")
    root = Path(source.path)
    current = root
    if current.is_symlink():
        raise FileNotFoundError("Unsafe source path")
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise FileNotFoundError("Unsafe source path")
    if not current.is_file():
        raise FileNotFoundError("Source photo is unavailable")
    try:
        if os.path.commonpath([str(root.resolve()), str(current.resolve())]) != str(root.resolve()):
            raise FileNotFoundError("Unsafe source path")
    except (OSError, ValueError):
        raise FileNotFoundError("Unsafe source path")
    return current
