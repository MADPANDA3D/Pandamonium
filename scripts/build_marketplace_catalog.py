#!/usr/bin/env python3
"""Build a signed Pandamonium marketplace catalog from pinned packages.

Usage:
    python scripts/build_marketplace_catalog.py \
        --packages marketplace/packages.json \
        --private-key ~/.config/pandamonium/marketplace-signing.pem \
        --key-id pandamonium-marketplace-2026 \
        --artifact-base-url https://github.com/MADPANDA3D/Pandamonium/releases/download/marketplace-v1 \
        --out dist-marketplace

Each package spec pins a public repository and an immutable tag; the script
clones it, validates `jarvis-extension.json`, archives the exact tree,
hashes and Ed25519-signs the archive, then emits `catalog.json` and
`trusted_keys.json`. No secret values or private keys are written into the
output. See `docs/marketplace-publishing-runbook.md`.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: E402
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from src.extension_registry import (  # noqa: E402
    ExtensionContractError,
    IMMUTABLE_REVISION_PATTERN,
    validate_extension_manifest,
)
from src.extension_installer import normalize_git_source_url  # noqa: E402
from src.extension_package import build_prepared_package  # noqa: E402

CATALOG_VERSION = "pandamonium.extension-catalog.v1"
DEFAULT_MANIFEST = "jarvis-extension.json"
MAX_ARTIFACT_BYTES = 512 * 1024 * 1024
SEMVER_PATTERN = r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"


class CatalogPublishError(SystemExit):
    pass


def canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def load_private_key(path: Path) -> Ed25519PrivateKey:
    try:
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    except (OSError, ValueError) as exc:
        raise CatalogPublishError(f"cannot read signing key {path}: {exc}") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise CatalogPublishError("catalog signing key must be Ed25519")
    return key


def public_key_b64(private_key: Ed25519PrivateKey) -> str:
    raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(raw).decode()


def sign_b64(private_key: Ed25519PrivateKey, payload: bytes) -> str:
    return base64.b64encode(private_key.sign(payload)).decode()


def _run(argv: list[str], *, cwd: Path | None = None) -> str:
    environment = dict(os.environ)
    environment["GIT_TERMINAL_PROMPT"] = "0"
    result = subprocess.run(
        argv,
        cwd=str(cwd) if cwd else None,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise CatalogPublishError(
            f"command failed ({' '.join(argv)}): {result.stderr.strip()[:400]}"
        )
    return result.stdout.strip()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_package_specs(specs: Any) -> list[dict[str, Any]]:
    if not isinstance(specs, list) or not specs:
        raise CatalogPublishError("packages file must contain a non-empty list")
    seen: set[str] = set()
    for spec in specs:
        if not isinstance(spec, Mapping):
            raise CatalogPublishError("each package spec must be an object")
        for field in ("source_url", "ref", "summary", "categories", "license", "publisher", "compatibility"):
            if field not in spec:
                raise CatalogPublishError(f"package spec missing '{field}'")
        if not str(spec["source_url"]).startswith("https://"):
            raise CatalogPublishError("package source_url must be https")
        if not any(char.isalnum() for char in str(spec["ref"])):
            raise CatalogPublishError("package ref must be a pinned tag or revision")
        package_id = str(spec.get("extension_id") or "").strip() or None
        if package_id:
            if package_id in seen:
                raise CatalogPublishError(f"duplicate package id: {package_id}")
            seen.add(package_id)
    return [dict(spec) for spec in specs]


def build_artifact(
    spec: Mapping[str, Any],
    workdir: Path,
) -> dict[str, Any]:
    source_url = str(spec["source_url"])
    ref = str(spec["ref"])
    prepared = spec.get("prepared_path")
    if prepared:
        clone_dir = Path(str(prepared)).resolve()
        if not clone_dir.is_dir() or not IMMUTABLE_REVISION_PATTERN.fullmatch(ref):
            raise CatalogPublishError("prepared package requires a directory and an immutable source revision")
        if spec.get("manifest_path", DEFAULT_MANIFEST) != DEFAULT_MANIFEST:
            raise CatalogPublishError("prepared manifest must be at the package root")
        revision = ref
    else:
        clone_dir = workdir / "src"
        if clone_dir.exists():
            shutil.rmtree(clone_dir)
        _run(["git", "clone", "--quiet", "--depth", "1", "--branch", ref, source_url, str(clone_dir)])
        revision = _run(["git", "rev-parse", "HEAD"], cwd=clone_dir)
    manifest_path = clone_dir / str(spec.get("manifest_path") or DEFAULT_MANIFEST)
    if not manifest_path.resolve().is_relative_to(clone_dir.resolve()) or manifest_path.is_symlink():
        raise CatalogPublishError("package manifest path must stay inside its source tree")
    if not manifest_path.is_file():
        raise CatalogPublishError(f"{ref}: missing {DEFAULT_MANIFEST}")
    try:
        manifest = validate_extension_manifest(
            json.loads(manifest_path.read_text(encoding="utf-8"))
        )
    except (ValueError, ExtensionContractError) as exc:
        raise CatalogPublishError(f"{ref}: invalid manifest ({exc})") from exc
    if (normalize_git_source_url(manifest["source"]["url"], check_public=False)
            != normalize_git_source_url(source_url, check_public=False)
            or manifest["source"]["revision"] not in {"self", revision}):
        raise CatalogPublishError("package source does not match its manifest")
    if prepared and manifest["source"]["revision"] != revision:
        raise CatalogPublishError("prepared manifest must record the immutable upstream revision")
    extension_id = str(spec.get("extension_id") or manifest["extension_id"])
    if extension_id != manifest["extension_id"]:
        raise CatalogPublishError("package extension_id does not match its manifest")
    version = manifest["version"]
    filename = str(spec.get("artifact_filename") or f"pandamonium-plugin-{extension_id}-{version}.tar.gz")
    if Path(filename).name != filename or "\\" in filename or not filename.endswith(".tar.gz"):
        raise CatalogPublishError("artifact filename must be a local .tar.gz filename")
    tar_path = workdir / filename
    if prepared:
        build_prepared_package(clone_dir, tar_path)
    else:
        _run(
            ["git", "archive", "--format=tar.gz", f"--prefix={extension_id}-{version}/", "-o", str(tar_path), "HEAD"],
            cwd=clone_dir,
        )
    size = tar_path.stat().st_size
    if size <= 0 or size > MAX_ARTIFACT_BYTES:
        raise CatalogPublishError(f"{ref}: artifact size out of bounds ({size})")
    return {
        "extension_id": extension_id,
        "version": version,
        "revision": revision,
        "manifest": manifest,
        "filename": filename,
        "path": tar_path,
        "sha256": sha256_file(tar_path),
        "size_bytes": size,
    }


def build_entry(
    spec: Mapping[str, Any],
    artifact: Mapping[str, Any],
    *,
    private_key: Ed25519PrivateKey,
    key_id: str,
    artifact_base_url: str,
) -> dict[str, Any]:
    artifact_url = f"{artifact_base_url.rstrip('/')}/{artifact['filename']}"
    manifest = dict(artifact["manifest"])
    manifest["source"] = {**manifest["source"], "revision": artifact["revision"]}
    manifest = validate_extension_manifest(manifest)
    configuration = [
        {
            "key": str(item["key"])[:128],
            "description": str(item["description"])[:500],
            "required": bool(item.get("required")),
            "secret": bool(item.get("secret")),
        }
        for item in (manifest.get("configuration") or [])[:64]
    ]
    publisher_spec = spec["publisher"]
    publisher = {
        "id": str(publisher_spec["id"]),
        "name": str(publisher_spec["name"]),
        "url": str(publisher_spec["url"]),
        "key_id": key_id,
    }
    signature = {
        "algorithm": "ed25519",
        "key_id": key_id,
        "value": sign_b64(private_key, f"sha256:{artifact['sha256']}".encode()),
    }
    return {
        "package_type": "plugin",
        "manifest": manifest,
        "summary": str(spec["summary"])[:500],
        "categories": [str(item)[:40] for item in spec["categories"]][:16],
        "license": str(spec["license"])[:100],
        "publisher": publisher,
        "artifact": {
            "url": artifact_url,
            "sha256": artifact["sha256"],
            "size_bytes": artifact["size_bytes"],
            "signature": signature,
        },
        "compatibility": dict(spec["compatibility"]),
        "dependencies": list(spec.get("dependencies") or [])[:64],
        "configuration": configuration,
        "restart_required": str(spec.get("restart_required") or "none"),
        "review": {
            "status": "active",
            "reviewed_at": str(spec.get("reviewed_at") or datetime.now(timezone.utc).isoformat()),
            "reviewer": str(spec.get("reviewer") or "pandamonium-security")[:200],
            "security_advisories": [],
        },
    }


def build_catalog(
    entries: list[dict[str, Any]],
    *,
    private_key: Ed25519PrivateKey,
    key_id: str,
    catalog_id: str,
    generated_at: str,
    expires_at: str,
) -> dict[str, Any]:
    catalog: dict[str, Any] = {
        "schema_version": CATALOG_VERSION,
        "catalog_id": catalog_id,
        "generated_at": generated_at,
        "expires_at": expires_at,
        "entries": entries,
    }
    catalog["signature"] = {
        "algorithm": "ed25519",
        "key_id": key_id,
        "value": sign_b64(private_key, canonical(catalog)),
    }
    return catalog


def write_outputs(out_dir: Path, catalog: Mapping[str, Any], trusted: Mapping[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "catalog.json").write_text(
        json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (out_dir / "trusted_keys.json").write_text(
        json.dumps(trusted, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def build_from_specs(
    specs: list[dict[str, Any]],
    *,
    private_key: Ed25519PrivateKey,
    key_id: str,
    artifact_base_url: str,
    catalog_id: str,
    generated_at: str,
    expires_at: str,
    workdir: Path,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    for spec in validate_package_specs(specs):
        artifact = build_artifact(spec, workdir)
        entries.append(
            build_entry(
                spec,
                artifact,
                private_key=private_key,
                key_id=key_id,
                artifact_base_url=artifact_base_url,
            )
        )
        artifacts.append(artifact)
    catalog = build_catalog(
        entries,
        private_key=private_key,
        key_id=key_id,
        catalog_id=catalog_id,
        generated_at=generated_at,
        expires_at=expires_at,
    )
    trusted = {key_id: public_key_b64(private_key)}
    return catalog, trusted, artifacts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packages", required=True, type=Path)
    parser.add_argument("--private-key", required=True, type=Path)
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--artifact-base-url", required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--catalog-id", default="pandamonium-community")
    parser.add_argument("--expires-days", type=int, default=90)
    args = parser.parse_args(argv)

    specs = json.loads(args.packages.read_text(encoding="utf-8"))
    private_key = load_private_key(args.private_key)
    now = datetime.now(timezone.utc)
    generated_at = now.isoformat()
    expires_at = (now + timedelta(days=max(args.expires_days, 1))).isoformat()

    with tempfile.TemporaryDirectory(prefix="pandamonium-marketplace-") as tmp:
        workdir = Path(tmp)
        catalog, trusted, artifacts = build_from_specs(
            specs,
            private_key=private_key,
            key_id=args.key_id,
            artifact_base_url=args.artifact_base_url,
            catalog_id=args.catalog_id,
            generated_at=generated_at,
            expires_at=expires_at,
            workdir=workdir,
        )
        write_outputs(args.out, catalog, trusted)
        for artifact in artifacts:
            shutil.copy2(artifact["path"], args.out / artifact["filename"])

    print(f"catalog: {args.out / 'catalog.json'} ({len(catalog['entries'])} entries)")
    print(f"trusted keys: {args.out / 'trusted_keys.json'}")
    for artifact in artifacts:
        print(
            f"  {artifact['filename']} sha256:{artifact['sha256']} "
            f"size={artifact['size_bytes']} revision={artifact['revision'][:12]}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
