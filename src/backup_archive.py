"""Versioned integrity contract for Pandamonium data backup archives."""

from __future__ import annotations

import hashlib
import json
import re
import tarfile
from pathlib import PurePosixPath
from typing import Any, Iterable, Mapping

BACKUP_MANIFEST_NAME = "data/.pandamonium-backup-manifest.json"
LEGACY_BACKUP_MANIFEST_NAME = "data/.odysseus-backup-manifest.json"
BACKUP_MANIFEST_NAMES = frozenset(
    {BACKUP_MANIFEST_NAME, LEGACY_BACKUP_MANIFEST_NAME}
)
BACKUP_SCHEMA_V1 = "jos-p7.backup.v1"
BACKUP_SCHEMA_V2 = "jos-p7.backup.v2"
BACKUP_INVENTORY_SCHEMA = "pandamonium.backup-inventory.v1"
_MAX_MANIFEST_BYTES = 16 * 1024 * 1024
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_INVENTORY_SOURCES = frozenset({"file", "materialized_internal_file_symlink"})


class BackupArchiveError(ValueError):
    """The backup archive does not satisfy the safe restore contract."""


def _canonical_inventory_bytes(files: Iterable[Mapping[str, Any]]) -> bytes:
    return json.dumps(
        list(files), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def build_backup_inventory(files: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Build a deterministic inventory for the regular payload members."""
    entries = sorted((dict(item) for item in files), key=lambda item: item["path"])
    return {
        "schema": BACKUP_INVENTORY_SCHEMA,
        "algorithm": "sha256",
        "file_count": len(entries),
        "total_bytes": sum(int(item["size"]) for item in entries),
        "digest": hashlib.sha256(_canonical_inventory_bytes(entries)).hexdigest(),
        "files": entries,
    }


def backup_inventory_summary(inventory: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return inventory evidence without emitting protected data paths."""
    value = inventory if isinstance(inventory, Mapping) else {}
    return {
        "schema": value.get("schema"),
        "algorithm": value.get("algorithm"),
        "file_count": value.get("file_count"),
        "total_bytes": value.get("total_bytes"),
        "digest": value.get("digest"),
    }


def validate_backup_members(
    members: Iterable[tarfile.TarInfo],
) -> list[tarfile.TarInfo]:
    """Validate safe, unique regular-file/directory members under ``data/``."""
    validated = list(members)
    seen: set[str] = set()
    for member in validated:
        rel = PurePosixPath(member.name)
        canonical = rel.as_posix()
        if rel.is_absolute() or ".." in rel.parts:
            raise BackupArchiveError(
                f"absolute or parent path is not allowed: {member.name!r}"
            )
        if not rel.parts or rel.parts[0] != "data":
            raise BackupArchiveError(
                f"entry is outside the data directory: {member.name!r}"
            )
        if canonical in seen:
            raise BackupArchiveError(f"duplicate archive member: {member.name!r}")
        seen.add(canonical)
        if len(rel.parts) == 1 and not member.isdir():
            raise BackupArchiveError("the data archive root must be a directory")
        if member.issym() or member.islnk():
            raise BackupArchiveError(f"link entry is not allowed: {member.name!r}")
        if not (member.isdir() or member.isfile()):
            raise BackupArchiveError(
                f"special file entry is not allowed: {member.name!r}"
            )
    return validated


def read_backup_manifest(
    tar: tarfile.TarFile, members: Iterable[tarfile.TarInfo]
) -> dict[str, Any]:
    """Read the one embedded manifest, retaining legacy archive support."""
    matches = [item for item in members if item.name in BACKUP_MANIFEST_NAMES]
    if not matches:
        return {"schema": "legacy", "scope": "data", "exclusions": "unknown"}
    if len(matches) != 1:
        raise BackupArchiveError("backup archive contains multiple manifests")
    member = matches[0]
    if not member.isfile() or member.size > _MAX_MANIFEST_BYTES:
        raise BackupArchiveError("backup manifest is unreadable or oversized")
    source = tar.extractfile(member)
    if source is None:
        raise BackupArchiveError("backup manifest is unreadable or oversized")
    try:
        with source:
            value = json.loads(source.read().decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise BackupArchiveError(f"backup manifest is invalid: {exc}") from exc
    if not isinstance(value, dict):
        raise BackupArchiveError("backup manifest must be a JSON object")
    return value


def verify_backup_inventory(
    tar: tarfile.TarFile,
    members: Iterable[tarfile.TarInfo],
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Verify the exact v2 payload membership, sizes, and SHA-256 digests."""
    schema = manifest.get("schema")
    if schema in {"legacy", BACKUP_SCHEMA_V1}:
        return {"verified": False, "schema": None}
    if schema != BACKUP_SCHEMA_V2:
        raise BackupArchiveError("backup manifest schema is unsupported")

    inventory = manifest.get("inventory")
    if not isinstance(inventory, Mapping):
        raise BackupArchiveError("backup inventory is missing")
    files = inventory.get("files")
    file_count = inventory.get("file_count")
    total_bytes = inventory.get("total_bytes")
    inventory_digest = inventory.get("digest")
    if (
        set(inventory) != {
            "schema",
            "algorithm",
            "file_count",
            "total_bytes",
            "digest",
            "files",
        }
        or inventory.get("schema") != BACKUP_INVENTORY_SCHEMA
        or inventory.get("algorithm") != "sha256"
        or not isinstance(file_count, int)
        or isinstance(file_count, bool)
        or file_count < 0
        or not isinstance(total_bytes, int)
        or isinstance(total_bytes, bool)
        or total_bytes < 0
        or not isinstance(inventory_digest, str)
        or not _SHA256_RE.fullmatch(inventory_digest)
        or not isinstance(files, list)
    ):
        raise BackupArchiveError("backup inventory contract is invalid")

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in files:
        if not isinstance(entry, Mapping) or set(entry) != {
            "path",
            "size",
            "sha256",
            "source",
        }:
            raise BackupArchiveError("backup inventory entry is invalid")
        path = entry.get("path")
        size = entry.get("size")
        digest = entry.get("sha256")
        source_kind = entry.get("source")
        if not isinstance(path, str):
            raise BackupArchiveError("backup inventory path is invalid")
        rel = PurePosixPath(path)
        if (
            rel.is_absolute()
            or ".." in rel.parts
            or not rel.parts
            or rel.parts[0] != "data"
            or rel.as_posix() != path
            or path in BACKUP_MANIFEST_NAMES
            or path in seen
        ):
            raise BackupArchiveError("backup inventory path is invalid")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise BackupArchiveError("backup inventory size is invalid")
        if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
            raise BackupArchiveError("backup inventory digest is invalid")
        if source_kind not in _INVENTORY_SOURCES:
            raise BackupArchiveError("backup inventory source is invalid")
        seen.add(path)
        normalized.append(
            {
                "path": path,
                "size": size,
                "sha256": digest,
                "source": source_kind,
            }
        )

    if normalized != sorted(normalized, key=lambda item: item["path"]):
        raise BackupArchiveError("backup inventory is not canonical")
    expected_digest = hashlib.sha256(
        _canonical_inventory_bytes(normalized)
    ).hexdigest()
    if (
        file_count != len(normalized)
        or total_bytes != sum(item["size"] for item in normalized)
        or inventory_digest != expected_digest
    ):
        raise BackupArchiveError("backup inventory summary is invalid")

    actual_members = {
        member.name: member
        for member in members
        if member.isfile() and member.name not in BACKUP_MANIFEST_NAMES
    }
    if set(actual_members) != seen:
        raise BackupArchiveError("backup inventory does not match archive members")
    by_path = {entry["path"]: entry for entry in normalized}
    for path in sorted(actual_members):
        member = actual_members[path]
        entry = by_path[path]
        if member.size != entry["size"]:
            raise BackupArchiveError(f"backup member size mismatch: {path!r}")
        source = tar.extractfile(member)
        if source is None:
            raise BackupArchiveError(f"backup member is unreadable: {path!r}")
        digest = hashlib.sha256()
        with source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != entry["sha256"]:
            raise BackupArchiveError(f"backup member digest mismatch: {path!r}")

    return {"verified": True, **backup_inventory_summary(inventory)}
