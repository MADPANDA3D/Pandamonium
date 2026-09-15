"""Reviewable marketplace submission bundles for installed extensions (MAD-956).

A submission is a deterministic, unsigned artifact: the reviewed manifest, its
pinned revision, capabilities, permissions, and configuration declarations
(never values). Publishing stays with the offline catalog tooling and release
signing keys; the app never reads or stores a signing key here.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from core.atomic_io import atomic_write_json
from src.action_protocol import utc_now
from src.constants import EXTENSIONS_DIR
from src.extension_registry import ExtensionRegistry, validate_extension_manifest

SUBMISSION_VERSION = "pandamonium.marketplace-submission.v1"
SUBMISSIONS_DIRNAME = "submissions"
MAX_SUBMISSION_CAPABILITIES = 256


class SubmissionError(Exception):
    """Fail-closed submission error with a stable code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _default_submissions_root() -> Path:
    return Path(EXTENSIONS_DIR) / SUBMISSIONS_DIRNAME


def _declared_configuration(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Configuration declarations only — key names, never owner-supplied values."""
    entries: list[dict[str, Any]] = []
    for item in (manifest.get("configuration") or [])[:32]:
        if not isinstance(item, Mapping):
            continue
        entries.append({
            "key": str(item.get("key") or "")[:64],
            "description": str(item.get("description") or "")[:200],
            "required": bool(item.get("required")),
            "secret": bool(item.get("secret")),
        })
    return entries


def _summarized_capabilities(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    inventory = record.get("capability_inventory")
    if not isinstance(inventory, Mapping):
        return []
    summaries: list[dict[str, Any]] = []
    for item in (inventory.get("capabilities") or [])[:MAX_SUBMISSION_CAPABILITIES]:
        if not isinstance(item, Mapping):
            continue
        summaries.append({
            "name": str(item.get("name") or "")[:128],
            "kind": str(item.get("kind") or "")[:32],
            "descriptor": str(item.get("descriptor") or "")[:40],
            "permission_mode": str(item.get("permission_mode") or "")[:40],
        })
    return summaries


def build_submission_bundle(
    registry: ExtensionRegistry,
    extension_id: str,
    *,
    operator_id: str,
    submissions_root: Path | None = None,
) -> dict[str, Any]:
    """Write (or reuse) a reviewable submission bundle for one installed extension."""
    extension_id = str(extension_id or "").strip()[:64]
    if not extension_id:
        raise SubmissionError("extension_submission_unavailable")
    record = registry.snapshot().get("extensions", {}).get(extension_id)
    if not isinstance(record, Mapping):
        raise SubmissionError("extension_not_installed")
    if not record.get("enabled"):
        raise SubmissionError("extension_submission_unavailable")
    manifest = validate_extension_manifest(record.get("manifest"))
    if manifest["extension_id"] != extension_id:
        raise SubmissionError("extension_submission_unavailable")
    source = manifest.get("source") or {}
    revision = str(source.get("revision") or "")
    capabilities = manifest.get("capabilities") or {}
    submission = {
        "schema_version": SUBMISSION_VERSION,
        "extension_id": extension_id,
        "name": manifest["name"],
        "version": manifest["version"],
        "source": {"url": source.get("url"), "revision": revision},
        "runtime": manifest["runtime"],
        "capabilities": {
            "descriptor": capabilities.get("descriptor"),
            "schemas": capabilities.get("schemas") or [],
            "inventory": _summarized_capabilities(record),
        },
        "permissions": manifest["permissions"],
        "data_boundaries": manifest["data_boundaries"],
        "configuration": _declared_configuration(manifest),
        "review": {
            "state": "submitted",
            "requested_by": str(operator_id or "")[:200],
            "publishing": "offline — catalog signing stays with the release tooling",
        },
        "manifest": manifest,
    }
    canonical = json.dumps(
        submission, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    digest = "sha256:" + hashlib.sha256(canonical).hexdigest()
    root = submissions_root or _default_submissions_root()
    revision_key = revision[:12] or "unpinned"
    target = root / extension_id / revision_key / "submission.json"
    if target.is_file():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = {}
        if isinstance(existing, Mapping) and existing.get("digest") == digest:
            return {
                "state": "submitted",
                "duplicate": True,
                "digest": digest,
                "path": str(target),
                "extension_id": extension_id,
                "source_revision": revision,
            }
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        str(target),
        {
            "schema_version": SUBMISSION_VERSION,
            "digest": digest,
            "submitted_at": utc_now(),
            "submission": submission,
        },
        indent=2,
    )
    return {
        "state": "submitted",
        "duplicate": False,
        "digest": digest,
        "path": str(target),
        "extension_id": extension_id,
        "source_revision": revision,
    }
