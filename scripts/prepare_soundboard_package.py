"""Prepare the reviewed Myinstants recipe without calling any model or executing source."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.atomic_io import atomic_write_json
from src.extension_installer import GitSourceClient
from src.extension_intake import Proposal, validate_proposal
from src.extension_package import build_prepared_package, package_tree_digest
from src.extension_scan import _source_secrets


def prepare(output: Path) -> dict:
    recipe = Path(__file__).resolve().parents[1] / "integrations/repository-packages/myinstants-api/proposal.json"
    proposal = Proposal.model_validate_json(recipe.read_text())
    source = proposal.manifest["source"]
    output.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix="soundboard-package-") as directory:
        root = Path(directory) / "source"
        GitSourceClient().checkout(source["url"], source["revision"], source["revision"], root)
        # Only remove acquisition metadata in this fresh disposable checkout.
        shutil.rmtree(root / ".git")
        excerpts = {}
        for path in root.rglob("*"):
            if path.is_symlink():
                raise ValueError("Pinned soundboard source cannot contain symlinks")
            if path.is_file():
                text = path.read_text(errors="replace")
                if _source_secrets(path, text):
                    raise ValueError("Pinned soundboard source failed secret audit")
                excerpts[path.relative_to(root).as_posix()] = text
        generated = validate_proposal(proposal, excerpts, root, source["url"], source["revision"])
        for name, content in generated["files"].items():
            if _source_secrets(Path(name), content):
                raise ValueError("Reviewed soundboard recipe failed secret audit")
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        integration = {key: value for key, value in generated.items() if key not in {"files", "manifest"}}
        atomic_write_json(str(root / ".pandamonium/integration.json"), integration)
        atomic_write_json(str(root / "jarvis-extension.json"), generated["manifest"])
        archive = output / "package.tar.gz"
        build_prepared_package(root, archive)
        receipt = {"source": source, "manifest": generated["manifest"], "model_calls": 0,
                   "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                   "size_bytes": archive.stat().st_size, "tree_digest": package_tree_digest(root)}
        atomic_write_json(str(output / "receipt.json"), receipt, indent=2)
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    print(json.dumps(prepare(parser.parse_args().output), indent=2))
