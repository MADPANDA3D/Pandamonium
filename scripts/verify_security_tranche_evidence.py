#!/usr/bin/env python3
"""Verify the MAD-799 tranche-one evidence and selection documents.

Offline ``--verify`` validates the structured evidence, enforces the tranche-one
policy (read-only, unprivileged, credential-free, plugin taxonomy), checks the
selection/threat-model documents against the evidence, and rejects secret-like
content in the evidence file.

``--online`` additionally re-fetches upstream GitHub/PyPI metadata for the
pinned releases so the version/license claims can be reproduced. It never
downloads packages or binaries and never scans a target. Network failures
degrade to a warning exit code instead of fabricated results.

Refs: MAD-799
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EVIDENCE = REPO_ROOT / "docs" / "security" / "mad-799-tranche-one-evidence.json"
SELECTION_DOC = REPO_ROOT / "docs" / "security" / "mad-799-tranche-one-selection.md"
THREAT_MODEL_DOC = REPO_ROOT / "docs" / "security" / "mad-799-tranche-one-threat-model.md"

SCHEMA = "pandamonium.security.tranche-one-evidence/1"
TASK = "MAD-799"
CANDIDATE_STATUSES = {"proposed_tranche_one", "conditional", "deferred"}
CLASSIFICATION_KEYS = {"target_effect", "mutating", "privileged", "network_active", "dual_use"}
REQUIRED_EXCLUSION_CLASSES = {
    "active exploitation",
    "credential attacks",
    "persistence",
    "evasion",
    "destructive response",
    "unrestricted scanning",
}

SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b(?:password|passwd|secret|token|api[_-]?key|bearer)\b\s*[:=]\s*['\"]?[A-Za-z0-9/+=_\-]{12,}"),
    re.compile(r"(?i)\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)\bsk-[A-Za-z0-9]{20,}"),
]


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _is_https(value: str) -> bool:
    parsed = urllib.parse.urlparse(value)
    return parsed.scheme == "https" and bool(parsed.netloc)


def validate_evidence(evidence: dict) -> list[str]:
    failures: list[str] = []
    if evidence.get("schema") != SCHEMA:
        failures.append(f"unexpected schema {evidence.get('schema')!r}")
    if evidence.get("task") != TASK:
        failures.append(f"unexpected task {evidence.get('task')!r}")
    try:
        datetime.fromisoformat(str(evidence.get("captured_at", "")).replace("Z", "+00:00"))
    except ValueError:
        failures.append("captured_at is not an ISO-8601 timestamp")

    candidates = evidence.get("candidates") or []
    if not candidates:
        failures.append("no candidates recorded")
    seen_ids: set[str] = set()
    for candidate in candidates:
        cid = str(candidate.get("candidate_id") or "")
        if not cid:
            failures.append("candidate without candidate_id")
            continue
        if cid in seen_ids:
            failures.append(f"duplicate candidate_id {cid}")
        seen_ids.add(cid)
        status = candidate.get("status")
        if status not in CANDIDATE_STATUSES:
            failures.append(f"{cid}: invalid status {status!r}")
        license_block = candidate.get("license") or {}
        if not license_block.get("spdx"):
            failures.append(f"{cid}: missing SPDX license")
        if status in {"proposed_tranche_one", "conditional"} and license_block.get("verified") is not True:
            failures.append(f"{cid}: license not verified")
        upstream = candidate.get("upstream") or {}
        if not upstream.get("repo_url", "").startswith("https://"):
            failures.append(f"{cid}: upstream repo_url must be https")
        if not upstream.get("observed_version"):
            failures.append(f"{cid}: missing observed_version")
        classification = candidate.get("classification") or {}
        missing = CLASSIFICATION_KEYS - set(classification)
        if missing:
            failures.append(f"{cid}: classification missing {sorted(missing)}")
        if str(classification.get("target_effect")) != "read-only":
            failures.append(f"{cid}: tranche policy requires target_effect read-only")
        if classification.get("mutating") is not False:
            failures.append(f"{cid}: tranche policy forbids mutating candidates")
        if classification.get("privileged") is not False:
            failures.append(f"{cid}: tranche policy forbids privileged candidates")

        plan = candidate.get("manifest_plan") or {}
        if plan.get("package_type") != "plugin":
            failures.append(f"{cid}: manifest package_type must be plugin")
        if plan.get("credentials_required") is not False:
            failures.append(f"{cid}: tranche one forbids credential requirements")
        network = plan.get("network_allowlist")
        if network is None:
            failures.append(f"{cid}: missing network_allowlist")
        elif any(not _is_https(str(url)) for url in network):
            failures.append(f"{cid}: network_allowlist entries must be https URLs")
        if classification.get("network_active") and not network and plan.get("network_allowlist_review_required") is not True:
            failures.append(f"{cid}: network-active candidate needs an allowlist or review_required flag")
        if classification.get("network_active") is False and network:
            failures.append(f"{cid}: offline candidate must not declare a network allowlist")

    exclusions = evidence.get("exclusion_classes") or []
    present = {str(item.get("class")) for item in exclusions}
    missing_classes = REQUIRED_EXCLUSION_CLASSES - present
    if missing_classes:
        failures.append(f"missing exclusion classes: {sorted(missing_classes)}")
    for item in exclusions:
        if not item.get("reason"):
            failures.append(f"exclusion {item.get('class')!r} has no reason")
    return failures


def validate_documents(evidence: dict, selection_text: str, threat_text: str) -> list[str]:
    failures: list[str] = []
    for candidate in evidence.get("candidates") or []:
        plugin_id = str(candidate.get("plugin_id") or "")
        if not plugin_id:
            failures.append("candidate missing plugin_id for document cross-check")
            continue
        if plugin_id not in selection_text:
            failures.append(f"{plugin_id}: not mentioned in the selection document")
        if candidate.get("status") in {"proposed_tranche_one", "conditional"} and plugin_id not in threat_text:
            failures.append(f"{plugin_id}: not mentioned in the threat model")
    for required in ("approve for adaptation", "conditional", "deferred", "excluded"):
        if required.lower() not in selection_text.lower():
            failures.append(f"selection document missing recommendation term {required!r}")
    for required in REQUIRED_EXCLUSION_CLASSES:
        if required not in threat_text.lower():
            failures.append(f"threat model missing exclusion class {required!r}")
    return failures


def scan_for_secrets(raw_text: str) -> list[str]:
    failures = []
    for pattern in SECRET_PATTERNS:
        if pattern.search(raw_text):
            failures.append(f"secret-like content matches {pattern.pattern!r}")
    return failures


def _fetch_json(url: str, headers: dict | None = None) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "pandamonium-mad-799-evidence-verify",
            **(headers or {}),
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def online_checks(evidence: dict) -> tuple[list[str], list[str]]:
    """Return (failures, warnings). Never downloads a package or scans a target."""
    failures: list[str] = []
    warnings: list[str] = []
    for candidate in evidence.get("candidates") or []:
        if candidate.get("status") not in {"proposed_tranche_one", "conditional"}:
            continue
        cid = candidate.get("candidate_id")
        upstream = candidate.get("upstream") or {}
        repo_url = str(upstream.get("repo_url") or "")
        if not repo_url.startswith("https://github.com/"):
            warnings.append(f"{cid}: no GitHub repo metadata check available")
            continue
        repo = repo_url.removeprefix("https://github.com/").removesuffix(".git")
        try:
            meta = _fetch_json(f"https://api.github.com/repos/{repo}")
            latest = _fetch_json(f"https://api.github.com/repos/{repo}/releases/latest")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
            warnings.append(f"{cid}: upstream metadata unavailable ({exc})")
            continue

        expected_spdx = candidate["license"].get("github_spdx_alias") or candidate["license"]["spdx"]
        observed_spdx = (meta.get("license") or {}).get("spdx_id")
        if observed_spdx and observed_spdx != expected_spdx:
            failures.append(f"{cid}: license drift {observed_spdx!r} != recorded {expected_spdx!r}")
        else:
            print(f"[PASS] {cid}: license {observed_spdx}")

        tag = upstream.get("observed_version")
        try:
            release = _fetch_json(f"https://api.github.com/repos/{repo}/releases/tags/{urllib.parse.quote(str(tag), safe='')}")
        except urllib.error.HTTPError as exc:
            failures.append(f"{cid}: pinned release {tag} no longer resolvable (HTTP {exc.code})")
            continue
        observed_published = release.get("published_at")
        if observed_published != upstream.get("release_published_at"):
            failures.append(
                f"{cid}: release date drift {observed_published!r} != recorded {upstream.get('release_published_at')!r}"
            )
        else:
            print(f"[PASS] {cid}: pinned release {tag} published {observed_published}")
        if isinstance(latest, dict) and latest.get("tag_name") and latest.get("tag_name") != str(tag):
            warnings.append(f"{cid}: newer upstream release available: {latest.get('tag_name')} (recorded {tag})")
    return failures, warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--verify", action="store_true", help="run validation (required)")
    parser.add_argument("--online", action="store_true", help="re-fetch upstream metadata for pinned releases")
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE, help="evidence JSON path")
    args = parser.parse_args(argv)
    if not args.verify:
        parser.print_help()
        return 2

    if not args.evidence.is_file():
        print(f"[FAIL] evidence missing: {args.evidence}")
        return 1
    raw_text = args.evidence.read_text(encoding="utf-8")
    failures = scan_for_secrets(raw_text)
    print(f"[{'FAIL' if failures else 'PASS'}] evidence secret scan")
    try:
        evidence = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        print(f"[FAIL] evidence JSON does not parse: {exc}")
        return 1
    print("[PASS] evidence JSON parses")

    structural = validate_evidence(evidence)
    for failure in structural:
        print(f"[FAIL] {failure}")
    if not structural:
        print(f"[PASS] evidence structure and tranche policy ({len(evidence['candidates'])} candidates)")

    selection_text = SELECTION_DOC.read_text(encoding="utf-8") if SELECTION_DOC.is_file() else ""
    threat_text = THREAT_MODEL_DOC.read_text(encoding="utf-8") if THREAT_MODEL_DOC.is_file() else ""
    if not selection_text or not threat_text:
        structural.append("selection or threat-model document is missing")
        print("[FAIL] selection or threat-model document is missing")
    else:
        doc_failures = validate_documents(evidence, selection_text, threat_text)
        failures.extend(doc_failures)
        for failure in doc_failures:
            print(f"[FAIL] {failure}")
        if not doc_failures:
            print("[PASS] selection and threat-model documents match the evidence")

    failures.extend(structural)

    if args.online:
        online_failures, warnings = online_checks(evidence)
        failures.extend(online_failures)
        for warning in warnings:
            print(f"[WARN] {warning}")
        if warnings and not online_failures:
            print("VERIFY PARTIAL — upstream metadata incomplete")
            print(f"VERIFY {'PASS' if not failures else f'FAIL ({len(failures)})'}")
            return 1 if failures else 4
    else:
        print("[PASS] offline verification only (use --online for upstream reproduction)")

    print("VERIFY " + ("PASS" if not failures else f"FAIL ({len(failures)})"))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
