#!/usr/bin/env python3
"""Build one signed, checksummed immutable Pandamonium release archive."""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import hmac
import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_KEY_PATH = ROOT / "config" / "pandamonium-release.pub"


def run(*args: str) -> str:
    return subprocess.run(
        args, cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_bytes(manifest: dict) -> bytes:
    return json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()


def build(tag: str, out_dir: Path, private_key_path: Path) -> tuple[Path, Path]:
    if not tag.startswith("v"):
        raise SystemExit("release tag must start with v")
    version = tag[1:]
    commit = run("git", "rev-parse", f"{tag}^{{commit}}")
    constants = run("git", "show", f"{commit}:src/constants.py")
    if f'APP_VERSION = "{version}"' not in constants:
        raise SystemExit("release tag does not match APP_VERSION")
    requirements = subprocess.run(
        ["git", "show", f"{commit}:requirements.txt"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    out_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / f"pandamonium-{version}.tar.gz"
    with tempfile.TemporaryDirectory() as tmp:
        raw_tar = Path(tmp) / "release.tar"
        subprocess.run(
            [
                "git",
                "archive",
                "--format=tar",
                "--prefix=pandamonium/",
                "-o",
                str(raw_tar),
                commit,
            ],
            cwd=ROOT,
            check=True,
        )
        with tarfile.open(raw_tar, "a") as tar:
            revision = f"{commit}\n".encode()
            info = tarfile.TarInfo("pandamonium/SOURCE_REVISION")
            info.mode = 0o644
            info.mtime = 0
            info.size = len(revision)
            tar.addfile(info, io.BytesIO(revision))
        with (
            raw_tar.open("rb") as source,
            archive.open("wb") as target,
            gzip.GzipFile(
                filename="", mode="wb", fileobj=target, mtime=0
            ) as compressed,
        ):
            shutil.copyfileobj(source, compressed)
    unsigned = {
        "schema_version": "pandamonium.release.v1",
        "version": version,
        "tag": tag,
        "commit": commit,
        "channel": "prerelease" if "-" in version else "stable",
        "published_at": datetime.now(timezone.utc).isoformat(),
        "compatibility": {
            "minimum_version": "1.0.10",
            "minimum_python": "3.11",
            "migration_entrypoint": "core.database",
            "migration_version": version,
            "data_restore_required": True,
        },
        "artifact": {
            "name": archive.name,
            "size": archive.stat().st_size,
            "sha256": sha256(archive),
            "requirements_sha256": hashlib.sha256(requirements).hexdigest(),
        },
    }
    private_key = serialization.load_pem_private_key(
        private_key_path.read_bytes(), password=None
    )
    if not isinstance(private_key, Ed25519PrivateKey):
        raise SystemExit("release signing key must be Ed25519")
    embedded_public_key = base64.b64decode(
        PUBLIC_KEY_PATH.read_text(encoding="utf-8").strip(), validate=True
    )
    signing_public_key = private_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    if not hmac.compare_digest(embedded_public_key, signing_public_key):
        raise SystemExit("release signing key does not match the embedded public key")
    signature = private_key.sign(canonical_bytes(unsigned))
    private_key.public_key().verify(signature, canonical_bytes(unsigned))
    manifest = {
        **unsigned,
        "signature": {
            "algorithm": "ed25519",
            "key_id": "pandamonium-release-2026",
            "value": base64.b64encode(signature).decode(),
        },
    }
    manifest_path = out_dir / "pandamonium-release.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return archive, manifest_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument("--out", type=Path, default=ROOT / "dist-release")
    parser.add_argument("--private-key", type=Path)
    args = parser.parse_args()
    key = args.private_key or Path(
        os.environ.get("PANDAMONIUM_RELEASE_SIGNING_KEY_FILE", "")
    )
    if not str(key) or not key.is_file():
        raise SystemExit("release signing key file is required")
    archive, manifest = build(args.tag, args.out, key)
    print(json.dumps({"archive": str(archive), "manifest": str(manifest)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
