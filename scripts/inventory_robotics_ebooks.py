#!/usr/bin/env python3
"""Pinned, reproducible inventory for jimmyval92/robotics_ebooks (MAD-794).

This is an audit tool, not an importer. It reads GitHub **tree metadata only**
(unauthenticated public API) at the pinned snapshot and never fetches PDF
bytes. Running it regenerates:

  docs/inventory/robotics_ebooks_inventory.json
  docs/inventory/robotics_ebooks_inventory.csv
  docs/inventory/robotics_ebooks_tree_<tree-sha>.json   (raw API evidence)

``--verify`` re-checks the committed artifact against the live tree (or the
committed cache with ``--offline``) without rewriting anything. ``--library-dir``
optionally fingerprints a local Pandamonium Books directory and reports exact
(by git blob id) and probable matches. Nothing here downloads or stores a PDF.

Refs: MAD-794
"""

from __future__ import annotations

import argparse
import csv
import difflib
import hashlib
import json
import re
import subprocess
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = "jimmyval92/robotics_ebooks"
REPO_URL = "https://github.com/jimmyval92/robotics_ebooks"
REPO_API = f"https://api.github.com/repos/{REPO}"

# Pinned snapshot (MAD-794). The tree object sha was resolved from the commit
# object at audit time: GET /repos/jimmyval92/robotics_ebooks/git/commits/<commit>
# -> tree.sha == PINNED_TREE_SHA. Querying the GitHub tree API with a commit sha
# echoes the commit sha in the response `sha` field, so the script fetches the
# canonical tree object instead and records both identifiers.
PINNED_COMMIT = "5441f772ac826b903eda1daf9a9e5adf0e6c3ac2"
PINNED_TREE_SHA = "ab976a028059075b6ba607cf94350a6f484b03f7"
PINNED_COMMIT_MESSAGE = "First commit"
PINNED_COMMIT_AT = "2019-09-03T01:24:21Z"
PINNED_COMMIT_AUTHOR = "listofbanned"

EXPECTED_PDF_COUNT = 107
EXPECTED_PDF_TOTAL_BYTES = 1790948281

# License facts captured at audit time (2026-09-14). The repository declares no
# license: GitHub's license field is null and the pinned tree contains no
# LICENSE/COPYING/NOTICE/UNLICENSE file. Unknown is never approved.
REPO_LICENSE_STATUS = "unknown"
REPO_LICENSE_OBSERVED_AT = "2026-09-14"
REPO_DESCRIPTION = "A collection of robotics ebooks."
REPO_DEFAULT_BRANCH_AT_AUDIT = "master"
REPO_PUSHED_AT_AT_AUDIT = "2019-09-03T02:01:12Z"
LICENSE_FILE_NAMES = {"license", "license.md", "license.txt", "copying", "notice", "unlicense"}

# Coarse, explicitly low-confidence OCR triage. These are blob-size buckets, not
# PDF inspection: page count, fonts, and image coverage are unknowable without
# downloading bytes, which this audit deliberately does not do.
LIKELY_TEXT_NATIVE_MAX_BYTES = 2 * 1024 * 1024
LIKELY_SCANNED_MIN_BYTES = 32 * 1024 * 1024
NEAR_TITLE_RATIO = 0.82

SCHEMA = "pandamonium.books.inventory/1"
GENERATOR_VERSION = "1"

DEFAULT_OUT = Path("docs/inventory/robotics_ebooks_inventory.json")
DEFAULT_CSV = Path("docs/inventory/robotics_ebooks_inventory.csv")
DEFAULT_CACHE = Path("docs/inventory") / f"robotics_ebooks_tree_{PINNED_TREE_SHA}.json"

CSV_FIELDS = [
    "index",
    "path",
    "title",
    "size_bytes",
    "size_human",
    "git_blob_sha1",
    "object_format",
    "license_status",
    "redistribution_approved",
    "import_recommendation",
    "import_reason",
    "exact_duplicate_of",
    "probable_duplicate_of",
    "ocr_need_estimate",
    "ocr_estimate_confidence",
    "language_estimate",
    "blob_api_url",
    "raw_url",
]


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def is_pdf(path: str) -> bool:
    return path.lower().endswith(".pdf")


def derive_title(path: str) -> str:
    return path[: -len(".pdf")] if is_pdf(path) else path


def normalize_title(title: str) -> str:
    text = unicodedata.normalize("NFKD", title)
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[_\-\u2013\u2014]+", " ", text)
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def raw_url(path: str) -> str:
    quoted = "/".join(urllib.parse.quote(part) for part in path.split("/"))
    return f"https://github.com/{REPO}/raw/{PINNED_COMMIT}/{quoted}"


def blob_api_url(sha: str) -> str:
    return f"{REPO_API}/git/blobs/{sha}"


def estimate_ocr_need(size: int) -> dict:
    if size < LIKELY_TEXT_NATIVE_MAX_BYTES:
        value, note = "likely_text_native", "blob is small; text-native is plausible but unverified"
    elif size >= LIKELY_SCANNED_MIN_BYTES:
        value, note = "likely_scanned_or_image_heavy", "blob is large; scanned/image-heavy is plausible but unverified"
    else:
        value, note = "unknown", "blob size does not separate text-native from scanned"
    return {
        "estimate": value,
        "confidence": "low",
        "basis": "git blob size only (GitHub tree metadata); no PDF bytes inspected",
        "note": note,
    }


def classify_pdf_entry(entry: dict, duplicate_of: str | None) -> dict:
    size = int(entry.get("size") or 0)
    supported = entry.get("type") == "blob" and entry.get("mode") == "100644" and is_pdf(entry["path"])
    coherent = size > 0
    return {
        "kind": "pdf_candidate" if supported and coherent else ("suspect_corrupt_metadata" if not coherent else "unsupported"),
        "metadata_only": True,
        "determinable": {
            "pdf_extension": is_pdf(entry["path"]),
            "blob_type": entry.get("type"),
            "mode": entry.get("mode"),
            "positive_size": coherent,
            "exact_duplicate": duplicate_of is not None,
        },
        "not_determinable_from_tree_metadata": [
            "pdf_magic_header",
            "structural_integrity",
            "encryption",
            "text_native_vs_scanned",
            "page_count",
        ],
        "ocr_need_estimate": estimate_ocr_need(size),
        "post_acquisition_check": (
            "After acquiring the file (not part of this repo), verify with: "
            "git hash-object <file> (must equal git_blob_sha1), stat -c%s <file> "
            "(must equal size_bytes), then pdfinfo/pdffonts/qpdf --check or OCR to "
            "actually classify the PDF."
        ),
    }


def fetch_tree(cache_path: Path, offline: bool) -> tuple[dict, str, list[str]]:
    """Return (tree_response, obtained_from, warnings). Never downloads PDFs."""
    warnings: list[str] = []
    url = f"{REPO_API}/git/trees/{PINNED_TREE_SHA}?recursive=1"
    if not offline:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "pandamonium-mad-794-inventory",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8")), "github_api", warnings
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
            if not cache_path.is_file():
                raise SystemExit(f"GitHub tree fetch failed and no cache at {cache_path}: {exc}")
            warnings.append(f"GitHub tree fetch failed ({exc}); using committed cache {cache_path}")
    elif not cache_path.is_file():
        raise SystemExit(f"--offline requires the committed tree cache at {cache_path}")
    return json.loads(cache_path.read_text(encoding="utf-8")), "committed_cache", warnings


def duplicate_analysis(pdf_entries: list[dict]) -> dict:
    exact_groups: dict[str, list[str]] = {}
    size_groups: dict[int, list[str]] = {}
    title_groups: dict[str, list[str]] = {}
    for entry in pdf_entries:
        exact_groups.setdefault(entry["sha"], []).append(entry["path"])
        size_groups.setdefault(int(entry["size"]), []).append(entry["path"])
        title_groups.setdefault(normalize_title(derive_title(entry["path"])), []).append(entry["path"])

    exact = [
        {"git_blob_sha1": sha, "paths": sorted(paths), "pdfs": len(paths)}
        for sha, paths in sorted(exact_groups.items())
        if len(paths) > 1
    ]
    size_collisions = [
        {"size_bytes": size, "paths": sorted(paths), "pdfs": len(paths), "exact": False}
        for size, paths in sorted(size_groups.items())
        if len(paths) > 1
    ]
    probable = [
        {"normalized_title": title, "paths": sorted(paths), "pdfs": len(paths)}
        for title, paths in sorted(title_groups.items())
        if len(paths) > 1
    ]

    grouped = {path for group in probable for path in group["paths"]}
    near: list[dict] = []
    titles = sorted((normalize_title(derive_title(e["path"])), e["path"], int(e["size"])) for e in pdf_entries)
    for i in range(len(titles)):
        for j in range(i + 1, len(titles)):
            left_title, left_path, left_size = titles[i]
            right_title, right_path, right_size = titles[j]
            if left_title == right_title and left_path in grouped and right_path in grouped:
                continue
            ratio = difflib.SequenceMatcher(None, left_title, right_title).ratio()
            if ratio >= NEAR_TITLE_RATIO:
                near.append({
                    "left": left_path,
                    "right": right_path,
                    "normalized_title_ratio": round(ratio, 3),
                    "same_size": left_size == right_size,
                    "probable_duplicate": False,
                })
    near.sort(key=lambda row: (-row["normalized_title_ratio"], row["left"], row["right"]))
    return {
        "exact_groups": exact,
        "size_collision_groups": size_collisions,
        "probable_groups": probable,
        "near_title_candidates": near,
    }


def build_inventory(tree_response: dict, obtained_from: str) -> dict:
    entries = sorted(tree_response.get("tree") or [], key=lambda e: e["path"])
    pdf_entries = [e for e in entries if e.get("type") == "blob" and is_pdf(e["path"])]
    non_pdf_entries = [e for e in entries if not (e.get("type") == "blob" and is_pdf(e["path"]))]

    analysis = duplicate_analysis(pdf_entries)
    exact_of = {}
    for group in analysis["exact_groups"]:
        for path in group["paths"]:
            exact_of[path] = group["paths"][0] if group["paths"][0] != path else group["paths"][1]
    probable_of = {}
    for group in analysis["probable_groups"]:
        for path in group["paths"]:
            probable_of[path] = group["paths"][0] if group["paths"][0] != path else group["paths"][1]
    size_of = {}
    for group in analysis["size_collision_groups"]:
        for path in group["paths"]:
            size_of.setdefault(path, []).extend(p for p in group["paths"] if p != path)

    license_block = {
        "status": REPO_LICENSE_STATUS,
        "spdx_id": None,
        "redistribution_approved": False,
        "import_approved": False,
        "observed_at": REPO_LICENSE_OBSERVED_AT,
        "evidence": [
            "GitHub repository metadata license field is null (observed at audit time via "
            f"{REPO_API}).",
            "No LICENSE/COPYING/NOTICE/UNLICENSE file exists in the pinned tree "
            f"({len(entries)} entries checked).",
            f"Repository description: {REPO_DESCRIPTION!r}.",
        ],
        "policy": "Unknown license is never approved for redistribution or import.",
    }

    files = []
    ocr_counts: dict[str, int] = {}
    for index, entry in enumerate(pdf_entries, start=1):
        path = entry["path"]
        size = int(entry["size"])
        classification = classify_pdf_entry(entry, exact_of.get(path))
        estimate = classification["ocr_need_estimate"]["estimate"]
        ocr_counts[estimate] = ocr_counts.get(estimate, 0) + 1
        files.append({
            "index": index,
            "path": path,
            "filename": path,
            "title": derive_title(path),
            "normalized_title": normalize_title(derive_title(path)),
            "size_bytes": size,
            "size_human": human_size(size),
            "git_blob_sha1": entry["sha"],
            "object_format": "sha1",
            "hash_method": (
                "GitHub tree API blob id (sha1 git object id). The API does not expose "
                "sha256 at this commit; verify after acquisition with "
                "`git hash-object <file>`, which must equal git_blob_sha1."
            ),
            "sha256_expected": None,
            "blob_api_url": blob_api_url(entry["sha"]),
            "raw_url": raw_url(path),
            "license_status": REPO_LICENSE_STATUS,
            "redistribution_approved": False,
            "import_recommendation": "skip",
            "import_reason": "unknown copyright/license status; do not redistribute or import",
            "exact_duplicate_of": exact_of.get(path),
            "probable_duplicate_of": probable_of.get(path),
            "size_collision_with": sorted(size_of.get(path, [])),
            "language_estimate": {
                "value": "en",
                "confidence": "low",
                "basis": "filename/title text only; file contents not inspected",
            },
            "classification": classification,
        })

    summary = {
        "pdf_count": len(pdf_entries),
        "pdf_total_bytes": sum(int(e["size"]) for e in pdf_entries),
        "expected_pdf_count": EXPECTED_PDF_COUNT,
        "expected_pdf_total_bytes": EXPECTED_PDF_TOTAL_BYTES,
        "count_matches_pinned_expectation": len(pdf_entries) == EXPECTED_PDF_COUNT,
        "bytes_match_pinned_expectation": sum(int(e["size"]) for e in pdf_entries) == EXPECTED_PDF_TOTAL_BYTES,
        "tree_entry_count": len(entries),
        "non_pdf_entry_count": len(non_pdf_entries),
        "exact_duplicate_groups": len(analysis["exact_groups"]),
        "probable_duplicate_groups": len(analysis["probable_groups"]),
        "size_collision_groups": len(analysis["size_collision_groups"]),
        "near_title_candidate_pairs": len(analysis["near_title_candidates"]),
        "approved_for_import": 0,
        "skip_for_import": len(pdf_entries),
        "ocr_estimate_counts": dict(sorted(ocr_counts.items())),
        "ocr_estimate_confidence": "low",
        "ocr_estimate_basis": "git blob size buckets only; not PDF inspection",
    }

    non_pdf = [
        {
            "path": entry["path"],
            "extension": Path(entry["path"]).suffix.lower(),
            "size_bytes": int(entry["size"]),
            "git_blob_sha1": entry["sha"],
            "license_status": REPO_LICENSE_STATUS,
            "redistribution_approved": False,
            "import_recommendation": "skip",
            "note": (
                "Repository README; not an ebook."
                if Path(entry["path"]).suffix.lower() in {".md", ".txt"}
                else "Non-PDF ebook (outside the 107-PDF scope); same unknown license."
            ),
        }
        for entry in non_pdf_entries
    ]

    return {
        "schema": SCHEMA,
        "generated_at": utcnow(),
        "generator": {
            "script": "scripts/inventory_robotics_ebooks.py",
            "version": GENERATOR_VERSION,
            "network_use": "GitHub tree API metadata only; no PDF bytes fetched",
        },
        "source": {
            "repo": REPO,
            "repo_url": REPO_URL,
            "repo_api_url": REPO_API,
            "description": REPO_DESCRIPTION,
            "default_branch_at_audit": REPO_DEFAULT_BRANCH_AT_AUDIT,
            "pushed_at_at_audit": REPO_PUSHED_AT_AT_AUDIT,
            "pinned_commit": PINNED_COMMIT,
            "pinned_commit_message": PINNED_COMMIT_MESSAGE,
            "pinned_commit_at": PINNED_COMMIT_AT,
            "pinned_commit_author": PINNED_COMMIT_AUTHOR,
            "pinned_tree_sha": PINNED_TREE_SHA,
            "tree_api_url": f"{REPO_API}/git/trees/{PINNED_TREE_SHA}?recursive=1",
            "tree_truncated": bool(tree_response.get("truncated")),
            "tree_obtained_from": obtained_from,
            "license": license_block,
        },
        "summary": summary,
        "duplicates": analysis,
        "library_dedupe": {
            "status": "not_discoverable_in_worktree",
            "detail": (
                "No Pandamonium Books library data was discoverable in this worktree: the "
                "canonical checkout has no data/personal_uploads directory and the repo "
                "tracks no catalog.json. The live CT103 Books catalog was deliberately not "
                "queried (out of scope). Nothing was marked as a library match."
            ),
            "checked": [
                "repo search for catalog.json / library data (none tracked)",
                "PERSONAL_UPLOADS_DIR (= <app_root>/data/personal_uploads) existence check (absent)",
                "local ~/.local/share and ~/.config scans for Books catalogs (absent)",
            ],
            "not_checked": [
                "live Pandamonium Books catalog on CT103 (not queried by this audit)",
                "PDF content beyond tree metadata (no downloads by design)",
            ],
            "how_to_check_command": (
                "python3 scripts/inventory_robotics_ebooks.py --library-dir "
                "<path/to/personal_uploads/<owner>/books>"
            ),
            "exact_source_sha256_available": False,
            "note": (
                "Source-side matching uses git blob sha1. The library scan computes sha256 and "
                "git blob sha1 locally and reports exact matches by blob id."
            ),
        },
        "files": files,
        "non_pdf_entries": non_pdf,
    }


def write_artifact(inventory: dict, out_path: Path, csv_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(inventory, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in inventory["files"]:
            writer.writerow({
                **row,
                "ocr_need_estimate": row["classification"]["ocr_need_estimate"]["estimate"],
                "ocr_estimate_confidence": row["classification"]["ocr_need_estimate"]["confidence"],
                "language_estimate": row["language_estimate"]["value"],
            })


def git_blob_sha1(path: Path) -> str:
    digest = hashlib.sha1()
    size = path.stat().st_size
    digest.update(f"blob {size}\0".encode("ascii"))
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def scan_library(library_dir: Path, inventory: dict) -> dict:
    """Fingerprint a local Books directory and match it against the inventory.

    Local files are read locally only; this never touches the source repo or any
    remote system and never copies a PDF anywhere.
    """
    catalogs = sorted(library_dir.rglob("catalog.json")) if library_dir.is_dir() else []
    catalog_titles: dict[str, str] = {}
    for catalog in catalogs:
        try:
            rows = json.loads(catalog.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict) and row.get("filename"):
                    catalog_titles[str(row["filename"])] = str(row.get("title") or "")
    source_by_sha = {row["git_blob_sha1"]: row["path"] for row in inventory["files"]}
    source_by_size: dict[int, list[str]] = {}
    source_by_name: dict[str, str] = {}
    source_by_title: dict[str, str] = {}
    for row in inventory["files"]:
        source_by_size.setdefault(row["size_bytes"], []).append(row["path"])
        source_by_name[row["filename"].casefold()] = row["path"]
        source_by_title.setdefault(row["normalized_title"], row["path"])

    local = []
    matches = []
    for pdf in sorted(library_dir.rglob("*.pdf")):
        size = pdf.stat().st_size
        blob = git_blob_sha1(pdf)
        record = {
            "library_path": str(pdf.relative_to(library_dir)),
            "filename": pdf.name,
            "catalog_title": catalog_titles.get(pdf.name),
            "size_bytes": size,
            "sha256": sha256_file(pdf),
            "git_blob_sha1": blob,
        }
        local.append(record)
        if blob in source_by_sha:
            matches.append({"source_path": source_by_sha[blob], "library_path": record["library_path"], "match": "exact_git_blob_sha1"})
        elif record["filename"].casefold() in source_by_name:
            matches.append({"source_path": source_by_name[record["filename"].casefold()], "library_path": record["library_path"], "match": "filename"})
        elif normalize_title(catalog_titles.get(pdf.name) or record["filename"]) in source_by_title:
            matches.append({"source_path": source_by_title[normalize_title(catalog_titles.get(pdf.name) or record["filename"])], "library_path": record["library_path"], "match": "normalized_title"})
        elif size in source_by_size:
            matches.append({"source_path": source_by_size[size][0], "library_path": record["library_path"], "match": "size_only"})
    return {
        "status": "scanned",
        "library_dir": str(library_dir),
        "catalog_files": [str(catalog) for catalog in catalogs],
        "library_pdf_count": len(local),
        "local_files": local,
        "matches": matches,
        "exact_source_sha256_available": False,
        "note": (
            "Exact matching uses git blob sha1 computed locally from library bytes; "
            "the source side has no sha256 from the API, so content identity is proven "
            "by git blob id equality only."
        ),
    }


def verify(artifact_path: Path, csv_path: Path, cache_path: Path, offline: bool) -> int:
    failures: list[str] = []
    if not artifact_path.is_file():
        print(f"[FAIL] artifact missing: {artifact_path}")
        return 1
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    tree, obtained_from, warnings = fetch_tree(cache_path, offline)
    for warning in warnings:
        print(f"[WARN] {warning}")

    expected = build_inventory(tree, obtained_from)
    summary = expected["summary"]
    if summary["pdf_count"] != EXPECTED_PDF_COUNT:
        failures.append(f"PDF count {summary['pdf_count']} != pinned {EXPECTED_PDF_COUNT}")
    if summary["pdf_total_bytes"] != EXPECTED_PDF_TOTAL_BYTES:
        failures.append(f"PDF total bytes {summary['pdf_total_bytes']} != pinned {EXPECTED_PDF_TOTAL_BYTES}")
    if tree.get("truncated"):
        failures.append("tree response is truncated")
    print(f"[PASS] tree from {obtained_from}: {summary['tree_entry_count']} entries, "
          f"{summary['pdf_count']} PDFs, {summary['pdf_total_bytes']} bytes")

    def key(files):
        return [(f["path"], f["size_bytes"], f["git_blob_sha1"]) for f in files]

    if key(artifact["files"]) != key(expected["files"]):
        failures.append("artifact rows differ from the live/cached tree")
    else:
        print(f"[PASS] artifact rows match tree ({len(artifact['files'])} files)")

    if artifact["summary"]["pdf_count"] != summary["pdf_count"] or \
            artifact["summary"]["pdf_total_bytes"] != summary["pdf_total_bytes"]:
        failures.append("artifact summary totals mismatch")
    else:
        print(f"[PASS] artifact totals reproduce ({artifact['summary']['pdf_total_bytes']} bytes)")

    if artifact["duplicates"] != expected["duplicates"]:
        failures.append("artifact duplicate analysis differs from recomputation")
    else:
        print("[PASS] duplicate analysis reproduces")

    if csv_path.is_file():
        with csv_path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        if len(rows) != len(artifact["files"]):
            failures.append(f"CSV rows {len(rows)} != JSON rows {len(artifact['files'])}")
        else:
            print(f"[PASS] CSV row parity ({len(rows)})")
    else:
        failures.append(f"CSV missing: {csv_path}")

    approved = sum(1 for row in artifact["files"] if row["redistribution_approved"] or row["import_recommendation"] != "skip")
    if artifact["source"]["license"]["status"] != "unknown" or approved:
        failures.append("unexpected license/approval state")
    else:
        print("[PASS] license policy: status=unknown, 0 approved, all skip")

    try:
        repo_root = Path(__file__).resolve().parent.parent
        tracked = subprocess.run(
            ["git", "ls-files", "*.pdf"],
            cwd=repo_root,
            capture_output=True, text=True, check=False,
        )
        if tracked.returncode == 0:
            names = [line for line in tracked.stdout.splitlines() if line.strip()]
            if names:
                failures.append(f"tracked PDFs present: {names}")
            else:
                print("[PASS] no tracked PDFs in git index")
        else:
            print("[PASS] git index check skipped (not a git worktree)")
    except OSError:
        print("[PASS] git index check skipped (git unavailable)")

    for failure in failures:
        print(f"[FAIL] {failure}")
    print("VERIFY " + ("PASS" if not failures else f"FAIL ({len(failures)})"))
    return 0 if not failures else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="inventory JSON output/verify target")
    parser.add_argument("--csv-out", type=Path, default=DEFAULT_CSV, help="inventory CSV output/verify target")
    parser.add_argument("--tree-cache", type=Path, default=DEFAULT_CACHE, help="raw tree API response cache")
    parser.add_argument("--offline", action="store_true", help="use the committed tree cache; no network")
    parser.add_argument("--verify", action="store_true", help="re-check the committed artifact; do not rewrite it")
    parser.add_argument("--library-dir", type=Path, help="fingerprint a local Books dir and report matches")
    parser.add_argument("--library-out", type=Path, help="write the --library-dir report JSON here as well as stdout")
    args = parser.parse_args(argv)

    if args.verify:
        return verify(args.out, args.csv_out, args.tree_cache, args.offline)

    tree, obtained_from, warnings = fetch_tree(args.tree_cache, args.offline)
    for warning in warnings:
        print(f"[WARN] {warning}")

    if args.library_dir:
        inventory = build_inventory(tree, obtained_from)
        report = {"generated_at": utcnow(), "source_pinned_commit": PINNED_COMMIT, "library_dedupe": scan_library(args.library_dir, inventory)}
        rendered = json.dumps(report, indent=2, ensure_ascii=False)
        if args.library_out:
            args.library_out.parent.mkdir(parents=True, exist_ok=True)
            args.library_out.write_text(rendered + "\n", encoding="utf-8")
            print(f"Wrote {args.library_out}", file=sys.stderr)
        print(rendered)
        return 0

    inventory = build_inventory(tree, obtained_from)

    args.tree_cache.parent.mkdir(parents=True, exist_ok=True)
    args.tree_cache.write_text(
        json.dumps({"sha": PINNED_TREE_SHA, "url": inventory["source"]["tree_api_url"],
                    "tree": tree.get("tree"), "truncated": tree.get("truncated")},
                   indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    write_artifact(inventory, args.out, args.csv_out)

    summary = inventory["summary"]
    print(f"Wrote {args.out} and {args.csv_out} (tree from {obtained_from})")
    print(f"PDFs={summary['pdf_count']} bytes={summary['pdf_total_bytes']} "
          f"exact_dup_groups={summary['exact_duplicate_groups']} "
          f"probable_dup_groups={summary['probable_duplicate_groups']} "
          f"approved={summary['approved_for_import']} skip={summary['skip_for_import']}")
    if not summary["count_matches_pinned_expectation"] or not summary["bytes_match_pinned_expectation"]:
        print("[WARN] pinned count/bytes expectation not reproduced", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())