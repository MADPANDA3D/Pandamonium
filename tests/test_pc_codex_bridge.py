from __future__ import annotations

import importlib.util
from pathlib import Path


BRIDGE_PATH = Path(__file__).parents[1] / "services" / "pc-codex-bridge" / "jarvis_codex_bridge.py"
SPEC = importlib.util.spec_from_file_location("jarvis_codex_bridge", BRIDGE_PATH)
bridge = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(bridge)


def _task(tmp_path: Path):
    bridge.STATE_DIR = tmp_path / "state"
    return bridge.Task({
        "task_id": "task-1",
        "worker": "pc-codex",
        "workspace": "home-lab",
        "cwd": str(tmp_path),
        "status": "running",
        "events": [],
    })


def test_artifact_marker_emits_validated_document_event(tmp_path):
    document = tmp_path / "Mark 5.md"
    document.write_text("# Mark 5\n\nSuccess, slow.", encoding="utf-8")
    task = _task(tmp_path)
    result = bridge._extract_artifacts(
        task,
        'Ready.\n[[ODYSSEUS_ARTIFACT path="Mark 5.md" title="Mark 5 Build"]]',
    )
    assert result == "Ready."
    event = task.data["events"][0]
    assert event["type"] == "artifact"
    assert event["metadata"]["title"] == "Mark 5 Build"
    assert event["metadata"]["content"].startswith("# Mark 5")


def test_artifact_marker_rejects_paths_outside_workspace(tmp_path):
    outside = tmp_path.parent / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    task = _task(tmp_path)
    bridge._extract_artifacts(task, f'[[ODYSSEUS_ARTIFACT path="{outside}"]]')
    assert task.data["events"][0]["type"] == "error"
    assert all(event["type"] != "artifact" for event in task.data["events"])


def test_artifact_marker_rejects_symlink_escape(tmp_path):
    outside = tmp_path.parent / "outside-through-link.md"
    outside.write_text("private", encoding="utf-8")
    (tmp_path / "linked.md").symlink_to(outside)
    task = _task(tmp_path)
    bridge._extract_artifacts(task, '[[ODYSSEUS_ARTIFACT path="linked.md"]]')
    assert task.data["events"][0]["type"] == "error"
    assert all(event["type"] != "artifact" for event in task.data["events"])
