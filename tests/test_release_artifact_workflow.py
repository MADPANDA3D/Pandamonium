import json
import subprocess
import sys
from pathlib import Path

from scripts import build_release_notes


ROOT = Path(__file__).resolve().parents[1]


def test_release_comparison_keeps_commit_with_empty_body(monkeypatch):
    monkeypatch.setattr(
        build_release_notes,
        "git",
        lambda *_args: "a" * 40 + "\x1fMerge release branch\x1f\x1e\n",
    )

    assert build_release_notes.collect_commits("v1.0.32", "HEAD") == [{
        "sha": "a" * 40,
        "subject": "Merge release branch",
        "issues": [],
    }]


def test_release_notes_cover_comparison_and_publish_only_after_verification(tmp_path):
    output = tmp_path / "notes.md"
    comparison = tmp_path / "comparison.json"
    source_notes = tmp_path / "source.md"
    source_notes.write_text((ROOT / ".github/releases/v1.0.33.md").read_text())
    subprocess.run([
        sys.executable,
        str(ROOT / "scripts/build_release_notes.py"),
        "--previous-tag", "v1.0.32",
        "--tag", "v1.0.33",
        "--notes", str(source_notes),
        "--output", str(output),
        "--comparison-output", str(comparison),
    ], cwd=ROOT, check=True)

    provenance = json.loads(comparison.read_text())
    assert provenance["linear_issues"] == ["MAD-842"]
    assert len(provenance["commits"]) == int(subprocess.check_output(
        ["git", "rev-list", "--count", "v1.0.32..v1.0.33"], cwd=ROOT, text=True
    ))
    assert all(commit["sha"] in output.read_text() for commit in provenance["commits"])

    workflow = (ROOT / ".github/workflows/release-artifact.yml").read_text()
    ordered_steps = [
        "Build and validate curated release notes",
        "Create or update draft release",
        "Upload release artifacts",
        "Verify draft body and uploaded artifacts",
        "Publish verified release",
    ]
    assert "--generate-notes" not in workflow
    assert [workflow.index(step) for step in ordered_steps] == sorted(
        workflow.index(step) for step in ordered_steps
    )

    bad_notes = tmp_path / "bad-notes.md"
    bad_notes.write_text(source_notes.read_text().replace("MAD-842 —", "MAD-999 —"))
    rejected = subprocess.run([
        sys.executable,
        str(ROOT / "scripts/build_release_notes.py"),
        "--previous-tag", "v1.0.32",
        "--tag", "v1.0.33",
        "--notes", str(bad_notes),
        "--output", str(tmp_path / "bad-output.md"),
        "--comparison-output", str(tmp_path / "bad-comparison.json"),
    ], cwd=ROOT)
    assert rejected.returncode != 0
