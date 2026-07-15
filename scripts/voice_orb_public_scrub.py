#!/usr/bin/env python3
"""Fail when the public Voice Orb delta contains known private artifacts."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / "docs/voice-orb/compatibility.json"
MEDIA_MANIFEST = ROOT / "static/voice-orb-media.json"
MEDIA_ROOT = ROOT / "static/media/voice-orb"
MEDIA_ROOT_PATH = PurePosixPath("static/media/voice-orb")
MEDIA_FIELDS = {
    "id", "title", "type", "path", "tags", "license", "source",
    "attribution", "checksum", "available",
}
MEDIA_TYPE_EXTENSIONS = {"video/webm": ".webm"}
MEDIA_LICENSES = {"CC0-1.0"}
MEDIA_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
MEDIA_TAG = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
MEDIA_PLACEHOLDERS = {"", "n/a", "none", "tbd", "unknown", "unlicensed"}
VOICE_AUDIO_SUFFIXES = {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav"}
VOICE_FRAME_SUFFIXES = {".bmp", ".gif", ".heic", ".jpeg", ".jpg", ".png", ".raw", ".webp"}
VOICE_AUDIO_CODEC_IDS = (
    b"A_AAC", b"A_AC3", b"A_DTS", b"A_EAC3", b"A_FLAC", b"A_MPEG",
    b"A_MS/ACM", b"A_OPUS", b"A_PCM", b"A_TRUEHD", b"A_VORBIS",
)
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
    if path.is_relative_to(MEDIA_ROOT_PATH):
        if path.suffix.lower() in VOICE_AUDIO_SUFFIXES:
            return "Voice Orb audio bundle"
        if path.suffix.lower() in VOICE_FRAME_SUFFIXES:
            return "Voice Orb private frame artifact"
    return None


def media_manifest_reasons(manifest: object, assets: dict[str, bytes]) -> list[str]:
    """Validate only the first-party Voice Orb media directory and manifest."""
    reasons: list[str] = []
    if not isinstance(manifest, dict) or set(manifest) != {"version", "media"}:
        return ["manifest must contain only version and media"]
    if manifest.get("version") != 1:
        reasons.append("manifest version must be 1")
    entries = manifest.get("media")
    if not isinstance(entries, list) or not entries:
        reasons.append("manifest media must be a non-empty list")
        entries = []

    declared: set[str] = set()
    seen_ids: set[str] = set()
    for index, entry in enumerate(entries):
        prefix = f"media[{index}]"
        if not isinstance(entry, dict) or set(entry) != MEDIA_FIELDS:
            reasons.append(f"{prefix} fields do not match the public media contract")
            continue
        media_id = entry.get("id")
        if not isinstance(media_id, str) or not MEDIA_ID.fullmatch(media_id):
            reasons.append(f"{prefix} id is invalid")
            continue
        if media_id in seen_ids:
            reasons.append(f"{prefix} id is duplicated")
        seen_ids.add(media_id)

        media_type = entry.get("type")
        extension = MEDIA_TYPE_EXTENSIONS.get(media_type) if isinstance(media_type, str) else None
        if extension is None:
            reasons.append(f"{prefix} MIME type is not allowlisted")
            continue
        expected_path = f"/static/media/voice-orb/{media_id}{extension}"
        if entry.get("path") != expected_path:
            reasons.append(f"{prefix} path is not the canonical same-origin path")
            continue
        asset_path = expected_path.removeprefix("/")
        declared.add(asset_path)

        for field in ("title", "source", "attribution"):
            value = entry.get(field)
            if not isinstance(value, str) or value.strip().lower() in MEDIA_PLACEHOLDERS:
                reasons.append(f"{prefix} {field} is missing or unknown")
        license_id = entry.get("license")
        if not isinstance(license_id, str) or license_id not in MEDIA_LICENSES:
            reasons.append(f"{prefix} license is not allowlisted")
        if entry.get("available") is not True:
            reasons.append(f"{prefix} must be explicitly available")
        tags = entry.get("tags")
        if (
            not isinstance(tags, list)
            or not tags
            or any(not isinstance(tag, str) or not MEDIA_TAG.fullmatch(tag) for tag in tags)
            or len(tags) != len(set(tags))
        ):
            reasons.append(f"{prefix} tags are invalid")
        elif "silent" not in tags:
            reasons.append(f"{prefix} must declare the silent tag")

        data = assets.get(asset_path)
        if data is None:
            reasons.append(f"{prefix} asset is missing")
            continue
        checksum = f"sha256:{hashlib.sha256(data).hexdigest()}"
        if entry.get("checksum") != checksum:
            reasons.append(f"{prefix} checksum does not match the asset")
        if not data.startswith(b"\x1aE\xdf\xa3"):
            reasons.append(f"{prefix} asset is not a WebM file")
        if any(codec_id in data for codec_id in VOICE_AUDIO_CODEC_IDS):
            reasons.append(f"{prefix} asset contains an audio track")

    allowed_files = declared | {"static/media/voice-orb/README.md"}
    for path in sorted(set(assets) - allowed_files):
        reasons.append(f"undeclared Voice Orb media file: {path}")
    if "static/media/voice-orb/README.md" not in assets:
        reasons.append("Voice Orb media provenance README is missing")
    return reasons


def current_media_failures() -> list[str]:
    try:
        manifest = json.loads(MEDIA_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"media manifest is unreadable: {type(exc).__name__}"]
    assets: dict[str, bytes] = {}
    failures: list[str] = []
    for path in MEDIA_ROOT.rglob("*"):
        if path.is_symlink():
            failures.append(f"Voice Orb media symlink is not allowed: {path.relative_to(ROOT).as_posix()}")
        elif path.is_file():
            assets[path.relative_to(ROOT).as_posix()] = path.read_bytes()
    return failures + media_manifest_reasons(manifest, assets)


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

    for reason in current_media_failures():
        failures.add(f"worktree:static/voice-orb-media.json: {reason}")

    return sorted(failures)


def self_test() -> None:
    assert path_reason("notes/.whoami/MEMORY.md")
    assert path_reason("HANDOVER.md")
    assert path_reason("docs/Mark 8 - private.md")
    assert not path_reason("docs/voice-orb/privacy.md")
    assert path_reason("static/media/voice-orb/recording.mp3")
    assert path_reason("static/media/voice-orb/camera-frame.jpg")
    assert not path_reason("static/icons/inherited-upstream.png")
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

    media = b"\x1aE\xdf\xa3silent-webm"
    checksum = f"sha256:{hashlib.sha256(media).hexdigest()}"
    entry = {
        "id": "demo", "title": "First-party demo", "type": "video/webm",
        "path": "/static/media/voice-orb/demo.webm", "tags": ["demo", "silent"],
        "license": "CC0-1.0", "source": "Voice Orb contributors",
        "attribution": "Original first-party asset", "checksum": checksum,
        "available": True,
    }
    manifest = {"version": 1, "media": [entry]}
    assets = {
        "static/media/voice-orb/README.md": b"provenance",
        "static/media/voice-orb/demo.webm": media,
    }
    assert media_manifest_reasons(manifest, assets) == []
    assert media_manifest_reasons(
        {"version": 1, "media": [{**entry, "license": "unknown"}]}, assets
    )
    assert media_manifest_reasons(
        manifest, {**assets, "static/media/voice-orb/voice.wav": b"private"}
    )
    assert media_manifest_reasons(
        manifest, {**assets, "static/media/voice-orb/camera-frame.jpg": b"private"}
    )
    assert media_manifest_reasons(
        manifest,
        {**assets, "static/media/voice-orb/demo.webm": media + b"A_OPUS"},
    )


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
