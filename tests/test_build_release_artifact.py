from __future__ import annotations

import base64
import subprocess
import tarfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scripts import build_release_artifact


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def test_release_builder_strips_group_and_other_write_bits(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.name", "Release Test")
    _git(root, "config", "user.email", "release@example.invalid")
    (root / "src").mkdir()
    (root / "src" / "constants.py").write_text(
        'APP_VERSION = "1.0.14"\n', encoding="utf-8"
    )
    (root / "requirements.txt").write_text("cryptography\n", encoding="utf-8")
    (root / "config.py").write_text("proof = True\n", encoding="utf-8")
    executable = root / "run"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o775)
    _git(root, "add", ".")
    _git(root, "commit", "-m", "test release")
    _git(root, "tag", "v1.0.14")

    private_key = Ed25519PrivateKey.generate()
    key_path = tmp_path / "release-key.pem"
    key_path.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    public_key_path = root / "release.pub"
    public_key_path.write_text(
        base64.b64encode(
            private_key.public_key().public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            )
        ).decode(),
        encoding="utf-8",
    )
    monkeypatch.setattr(build_release_artifact, "ROOT", root)
    monkeypatch.setattr(build_release_artifact, "PUBLIC_KEY_PATH", public_key_path)

    archive, _manifest = build_release_artifact.build(
        "v1.0.14", tmp_path / "dist", key_path
    )

    with tarfile.open(archive, "r:gz") as release:
        modes = {member.name: member.mode & 0o777 for member in release}
    assert modes["pandamonium/config.py"] == 0o644
    assert modes["pandamonium/run"] == 0o755
    assert all(not mode & 0o022 for mode in modes.values())
