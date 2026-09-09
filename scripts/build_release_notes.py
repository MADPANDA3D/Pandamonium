#!/usr/bin/env python3
"""Validate curated release notes and append exact Git/Linear provenance."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path


ISSUE_RE = re.compile(r"\bMAD-\d+\b", re.IGNORECASE)
ISSUE_HEADING_RE = re.compile(r"^## (MAD-\d+) — (.+)$", re.MULTILINE)
REQUIRED_SECTIONS = (
    "Problem",
    "Implementation",
    "Product value and UI visibility",
    "Verification",
    "Configuration, compatibility, and limitations",
    "References",
)


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, text=True, capture_output=True
    ).stdout


def collect_commits(previous_tag: str, tag: str) -> list[dict]:
    raw = git(
        "log", "--reverse", "--format=%H%x1f%s%x1f%b%x1e",
        f"{previous_tag}..{tag}",
    )
    commits = []
    for record in raw.split("\x1e"):
        fields = record.strip("\r\n").split("\x1f", 2)
        if len(fields) != 3:
            continue
        sha, subject, body = fields
        sha = sha.strip()
        subject = subject.strip()
        issues = sorted({key.upper() for key in ISSUE_RE.findall(f"{subject}\n{body}")})
        commits.append({"sha": sha, "subject": subject, "issues": issues})
    if not commits:
        raise ValueError(f"release comparison {previous_tag}..{tag} has no commits")
    return commits


def validate_curated_notes(notes: str, tag: str, released_issues: set[str]) -> None:
    if notes.splitlines()[:1] != [f"# Pandamonium {tag}"]:
        raise ValueError(f"curated notes must start with '# Pandamonium {tag}'")
    first_outcome = ISSUE_HEADING_RE.search(notes)
    opening = notes[notes.find("\n") + 1:first_outcome.start()].strip() if first_outcome else ""
    if len(opening) < 20:
        raise ValueError("curated notes require a substantive opening summary")
    headings = list(ISSUE_HEADING_RE.finditer(notes))
    heading_keys = [match.group(1).upper() for match in headings]
    if len(heading_keys) != len(set(heading_keys)):
        raise ValueError("each released MAD issue must have exactly one outcome heading")
    missing = sorted(released_issues - set(heading_keys))
    extra = sorted(set(heading_keys) - released_issues)
    if missing or extra:
        raise ValueError(f"Linear issue coverage mismatch: missing={missing}, extra={extra}")
    if re.search(r"\b(?:TBD|TODO|MERGE_COMMIT)\b", notes):
        raise ValueError("curated notes contain an unfinished placeholder")
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(notes)
        outcome = notes[heading.end():end]
        for section in REQUIRED_SECTIONS:
            if not re.search(rf"^### {re.escape(section)}\s*$", outcome, re.MULTILINE):
                raise ValueError(f"{heading.group(1)} is missing the '{section}' section")
        references = outcome.split("### References", 1)[1]
        if "linear.app/" not in references or "github.com/" not in references:
            raise ValueError(f"{heading.group(1)} references must link Linear and GitHub")


def render(notes: str, previous_tag: str, tag: str, commits: list[dict], repo_url: str) -> str:
    issues = sorted({key for commit in commits for key in commit["issues"]})
    lines = [
        notes.rstrip(),
        "",
        "## Release comparison",
        "",
        f"Compared [`{previous_tag}...{tag}`]({repo_url}/compare/{previous_tag}...{tag}).",
        "",
        "### Included commits",
        "",
    ]
    for commit in commits:
        coverage = ", ".join(commit["issues"]) or "no Linear key"
        lines.append(
            f"- [`{commit['sha'][:12]}`]({repo_url}/commit/{commit['sha']}) "
            f"{commit['subject']} — {coverage}"
        )
    lines.extend(["", "### Linear issue coverage", ""])
    lines.extend(
        f"- [{key}](https://linear.app/madpanda3d/issue/{key})"
        for key in issues
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--previous-tag", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--notes", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--comparison-output", type=Path, required=True)
    parser.add_argument(
        "--repository-url",
        default=f"https://github.com/{os.getenv('GITHUB_REPOSITORY', 'MADPANDA3D/Pandamonium')}",
    )
    args = parser.parse_args()

    commits = collect_commits(args.previous_tag, args.tag)
    released_issues = {key for commit in commits for key in commit["issues"]}
    if not released_issues:
        raise SystemExit("release comparison contains no MAD-* Linear issue")
    notes = args.notes.read_text(encoding="utf-8")
    validate_curated_notes(notes, args.tag, released_issues)
    body = render(
        notes, args.previous_tag, args.tag, commits, args.repository_url.rstrip("/")
    )
    comparison = {
        "schema_version": "pandamonium.release-comparison.v1",
        "previous_tag": args.previous_tag,
        "tag": args.tag,
        "commits": commits,
        "linear_issues": sorted(released_issues),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(body, encoding="utf-8")
    args.comparison_output.write_text(
        json.dumps(comparison, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
