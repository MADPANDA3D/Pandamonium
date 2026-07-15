#!/usr/bin/env python3
"""Fail when the public Voice Orb delta contains known private artifacts."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / "docs/voice-orb/compatibility.json"
PRIVATE_CONTENT = {
    "absolute user home path": re.compile(
        rb"(?:/home/[A-Za-z0-9][A-Za-z0-9._-]*/|/Users/[A-Za-z0-9][A-Za-z0-9._-]*/|[A-Za-z]:\\Users\\[A-Za-z0-9][A-Za-z0-9._-]*\\)",
        re.I,
    ),
    "RFC1918 address": re.compile(
        rb"(?<![0-9])(?:10(?:\.[0-9]{1,3}){3}|172\.(?:1[6-9]|2[0-9]|3[01])(?:\.[0-9]{1,3}){2}|192\.168(?:\.[0-9]{1,3}){2})(?![0-9])"
    ),
    "Tailscale CGNAT address": re.compile(
        rb"(?<![0-9])100\.(?:6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])(?:\.[0-9]{1,3}){2}(?![0-9])"
    ),
    "private mutation compatibility flag": re.compile(
        rb"(?:ODYSSEUS|JARVIS_CODEX)_PRIVATE_WORKER_MUTATIONS", re.I
    ),
}


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=ROOT)


def path_reason(raw_path: str) -> str | None:
    path = PurePosixPath(raw_path)
    lowered = [part.lower() for part in path.parts]
    if ".whoami" in lowered:
        return ".whoami artifact"
    if path.name.lower() in {"handover.md", "bugs.md", "import.md"}:
        return "private handoff artifact"
    if re.search(r"(?:^|/)mark[ _-]*[0-9]+(?:[ _-]|\.|$)", raw_path, re.I):
        return "private Mark document"
    return None


def content_reasons(data: bytes) -> list[str]:
    if b"\0" in data:
        return []
    return [label for label, pattern in PRIVATE_CONTENT.items() if pattern.search(data)]


def scan_blob(label: str, path: str, data: bytes, failures: set[str]) -> None:
    reason = path_reason(path)
    if reason:
        failures.add(f"{label}:{path}: {reason}")
    if path == "scripts/voice_orb_public_scrub.py":
        return
    for content_reason in content_reasons(data):
        failures.add(f"{label}:{path}: contains {content_reason}")


def scan() -> list[str]:
    base = json.loads(RECORD.read_text(encoding="utf-8"))["upstream_commit"]
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", base, "HEAD"],
        cwd=ROOT,
        check=True,
    )
    failures: set[str] = set()
    seen_blobs: set[tuple[str, str]] = set()

    for line in git("rev-list", "--objects", f"{base}..HEAD").decode().splitlines():
        object_id, _, path = line.partition(" ")
        if not path or git("cat-file", "-t", object_id).strip() != b"blob":
            continue
        key = (object_id, path)
        if key in seen_blobs:
            continue
        seen_blobs.add(key)
        scan_blob(object_id[:12], path, git("cat-file", "-p", object_id), failures)

    for raw in git("diff", "--name-only", "-z", base, "--").split(b"\0"):
        if not raw:
            continue
        path = raw.decode("utf-8", "surrogateescape")
        file_path = ROOT / path
        if file_path.is_file():
            scan_blob("worktree", path, file_path.read_bytes(), failures)

    return sorted(failures)


def self_test() -> None:
    assert path_reason("notes/.whoami/MEMORY.md")
    assert path_reason("HANDOVER.md")
    assert path_reason("docs/Mark 8 - private.md")
    assert not path_reason("docs/voice-orb/privacy.md")
    assert content_reasons(b"path=/home/example/private")
    assert content_reasons(rb"path=C:\Users\example\private")
    assert content_reasons(b"host=10.20.30.40")
    assert content_reasons(b"host=172.31.2.9")
    assert content_reasons(b"host=192.168.50.10")
    assert content_reasons(b"host=100.64.20.4")
    assert content_reasons(b"ODYSSEUS_PRIVATE_WORKER_MUTATIONS=true")
    assert not content_reasons(b"http://127.0.0.1:7000")
    assert not content_reasons(b"https://github.com/MADPANDA3D/odysseus")
    assert not content_reasons(b"ODYSSEUS_PC_CODEX_ENABLED=false")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        self_test()
        print("public scrub self-test passed")
        raise SystemExit(0)
    found = scan()
    if found:
        print("Public Voice Orb scrub failed:", file=sys.stderr)
        for item in found:
            print(f"- {item}", file=sys.stderr)
        raise SystemExit(1)
    print("Public Voice Orb scrub passed")
