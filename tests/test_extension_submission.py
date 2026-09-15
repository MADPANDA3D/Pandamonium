"""Marketplace submission bundles for installed extensions (MAD-956)."""

import hashlib
import json
from pathlib import Path

import pytest

from src.extension_registry import MANIFEST_VERSION, ExtensionRegistry
from src.extension_submission import SubmissionError, build_submission_bundle

FIXTURES = Path(__file__).parent / "fixtures" / "extensions"
REVISION = "f3a3da54cdea7845adbbcf48f0a8b5a278b85c45"


def _manifest() -> dict:
    manifest = json.loads(
        (FIXTURES / "oracle.manifest.json").read_text(encoding="utf-8")
    )
    manifest["configuration"] = [
        {
            "key": "SUBMISSION_API_TOKEN",
            "description": "Owner-supplied token reference",
            "required": False,
            "secret": True,
        }
    ]
    return manifest


def _resolved_catalog(manifest: dict) -> dict:
    return {
        "protocol_version": MANIFEST_VERSION,
        "extension_id": manifest["extension_id"],
        "version": manifest["version"],
        "source_revision": REVISION,
        "tools": [
            {
                "name": "inspect_globe",
                "description": "Run inspect_globe",
                "parameters": {
                    "type": "object",
                    "properties": {"target": {"type": "string"}},
                    "required": ["target"],
                    "additionalProperties": False,
                },
            }
        ],
    }


def _registry(tmp_path: Path) -> ExtensionRegistry:
    registry = ExtensionRegistry(tmp_path / "extensions.json")
    manifest = _manifest()
    registry.register(
        manifest,
        _resolved_catalog(manifest),
        source_revision=REVISION,
        health_available=True,
    )
    return registry


def test_submission_bundle_is_deterministic_and_idempotent(tmp_path):
    registry = _registry(tmp_path)
    root = tmp_path / "submissions"

    first = build_submission_bundle(
        registry, "oracle", operator_id="operator", submissions_root=root
    )
    second = build_submission_bundle(
        registry, "oracle", operator_id="operator", submissions_root=root
    )

    assert first["state"] == "submitted"
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert first["digest"] == second["digest"]
    assert first["source_revision"] == REVISION

    bundle = json.loads(Path(first["path"]).read_text(encoding="utf-8"))
    assert bundle["digest"] == first["digest"]
    canonical = json.dumps(
        bundle["submission"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    assert first["digest"] == "sha256:" + hashlib.sha256(canonical).hexdigest()
    assert bundle["submission"]["source"]["revision"] == REVISION
    assert bundle["submission"]["review"]["publishing"].startswith("offline")
    assert bundle["submission"]["review"]["requested_by"] == "operator"


def test_submission_bundle_carries_declarations_not_secret_values(tmp_path):
    registry = _registry(tmp_path)
    result = build_submission_bundle(
        registry,
        "oracle",
        operator_id="operator",
        submissions_root=tmp_path / "submissions",
    )

    bundle = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    submission = bundle["submission"]
    assert submission["configuration"] == [
        {
            "key": "SUBMISSION_API_TOKEN",
            "description": "Owner-supplied token reference",
            "required": False,
            "secret": True,
        }
    ]
    assert submission["capabilities"]["inventory"] == [
        {
            "name": "inspect_globe",
            "kind": "tool",
            "descriptor": "live_catalog",
            "permission_mode": "read_only",
        }
    ]
    dumped = json.dumps(bundle)
    assert '"value"' not in dumped
    assert "private_key" not in dumped
    assert "signature" not in dumped


def test_submission_requires_installed_and_enabled_extension(tmp_path):
    registry = _registry(tmp_path)
    root = tmp_path / "submissions"

    with pytest.raises(SubmissionError, match="extension_not_installed"):
        build_submission_bundle(
            registry, "missing", operator_id="operator", submissions_root=root
        )

    registry.disable("oracle")
    with pytest.raises(SubmissionError, match="extension_submission_unavailable"):
        build_submission_bundle(
            registry,
            "oracle",
            operator_id="operator",
            submissions_root=root,
        )
    assert not root.exists() or not any(root.rglob("submission.json"))
