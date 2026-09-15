"""MAD-951: repos that mirror the same skills for several agent tools must
still produce one unique capability per name. The real-world repro is
DietrichGebert/ponytail, which ships the same six skills under both
`.openclaw/skills/` and `skills/`.
"""

import shutil
from pathlib import Path

from src.extension_scan import ExtensionStaticScanner

SOURCE_URL = "https://github.com/example/ponytail-mirror.git"
REVISION = "b" * 40


class _CopyGitClient:
    def __init__(self, source_dir: Path, revision: str = REVISION):
        self.source_dir = source_dir
        self.revision = revision

    def resolve_revision(self, source_url, requested_ref):
        return source_url, requested_ref or "HEAD", self.revision

    def checkout(self, source, ref, revision, destination):
        shutil.copytree(self.source_dir, destination)


def _skill(name: str) -> str:
    return (
        "---\n"
        f"name: {name}\n"
        f"description: Review using the {name} workflow\n"
        "---\n\n"
        "## Procedure\n\n1. Follow the reviewed procedure.\n"
    )


def test_mirrored_skill_dirs_produce_unique_capabilities(tmp_path):
    source = tmp_path / "source"
    skills = ("ponytail-audit", "ponytail-review")
    for skill in skills:
        for base in (".openclaw/skills", "skills"):
            skill_dir = source / base / skill
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(_skill(skill), encoding="utf-8")
    (source / "LICENSE").write_text(
        "MIT License\n\nPermission is hereby granted...", encoding="utf-8"
    )

    scanner = ExtensionStaticScanner(
        git_client=_CopyGitClient(source),
        staging_root=tmp_path / "managed",
        data_dir=tmp_path / "scans",
    )
    artifact = scanner.run(SOURCE_URL, "HEAD", operator_id="operator")

    assert artifact["repo_class"] == "skill_bundle"
    names = [item["name"] for item in artifact["capabilities"]]
    assert names == list(skills)
    assert len(names) == len(set(names))
    assert all(
        item["evidence_path"].startswith(".openclaw/skills/")
        for item in artifact["capabilities"]
    )