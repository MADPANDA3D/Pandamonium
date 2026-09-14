import csv
import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "inventory_robotics_ebooks.py"
ARTIFACT_PATH = ROOT / "docs" / "inventory" / "robotics_ebooks_inventory.json"
CSV_PATH = ROOT / "docs" / "inventory" / "robotics_ebooks_inventory.csv"


def load_module():
    spec = importlib.util.spec_from_file_location("inventory_robotics_ebooks", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _entry(path, sha, size):
    return {"path": path, "sha": sha, "size": size, "type": "blob", "mode": "100644"}


def test_normalize_title_folds_punctuation_case_and_spacing():
    inv = load_module()

    assert inv.normalize_title("Active Visual Inference of Surface Shape - R. Cipolla") == (
        "active visual inference of surface shape r cipolla"
    )
    assert inv.normalize_title("PDA Robotics  – Douglas") == inv.normalize_title("PDA Robotics - Douglas")
    assert inv.normalize_title("Field and Service Robotics- Recent Advances") != (
        inv.normalize_title("Field and Service Robotics - Corke")
    )


def test_duplicate_analysis_detects_exact_size_and_probable_duplicates():
    inv = load_module()

    analysis = inv.duplicate_analysis([
        _entry("Book One - A.pdf", "a" * 40, 100),
        _entry("Book One - A (copy).pdf", "a" * 40, 100),
        _entry("Book Two (1st Ed).pdf", "b" * 40, 200),
        _entry("Book Two 1st Ed.pdf", "c" * 40, 200),
        _entry("Book Three.pdf", "d" * 40, 300),
    ])

    assert [group["git_blob_sha1"] for group in analysis["exact_groups"]] == ["a" * 40]
    assert analysis["exact_groups"][0]["pdfs"] == 2
    assert [group["size_bytes"] for group in analysis["size_collision_groups"]] == [100, 200]
    assert len(analysis["probable_groups"]) == 1
    assert sorted(analysis["probable_groups"][0]["paths"]) == [
        "Book Two (1st Ed).pdf",
        "Book Two 1st Ed.pdf",
    ]


def test_ocr_need_estimate_is_labeled_low_confidence_metadata_guess():
    inv = load_module()

    tiny = inv.estimate_ocr_need(inv.LIKELY_TEXT_NATIVE_MAX_BYTES - 1)
    mid = inv.estimate_ocr_need(3 * 1024 * 1024)
    huge = inv.estimate_ocr_need(inv.LIKELY_SCANNED_MIN_BYTES)

    assert tiny["estimate"] == "likely_text_native"
    assert mid["estimate"] == "unknown"
    assert huge["estimate"] == "likely_scanned_or_image_heavy"
    assert {tiny["confidence"], mid["confidence"], huge["confidence"]} == {"low"}
    assert "no PDF bytes inspected" in mid["basis"]


def test_committed_inventory_reproduces_pinned_snapshot():
    inventory = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))
    summary = inventory["summary"]

    assert summary["pdf_count"] == 107
    assert summary["pdf_total_bytes"] == 1790948281
    assert summary["count_matches_pinned_expectation"] is True
    assert summary["bytes_match_pinned_expectation"] is True
    assert len(inventory["files"]) == 107
    assert len({row["path"] for row in inventory["files"]}) == 107
    assert {len(row["git_blob_sha1"]) for row in inventory["files"]} == {40}
    assert inventory["source"]["pinned_commit"] == "5441f772ac826b903eda1daf9a9e5adf0e6c3ac2"
    assert inventory["source"]["pinned_tree_sha"] == "ab976a028059075b6ba607cf94350a6f484b03f7"
    assert inventory["source"]["tree_truncated"] is False
    assert inventory["duplicates"]["exact_groups"] == []
    assert inventory["duplicates"]["probable_groups"] == []
    for row in inventory["files"]:
        assert row["license_status"] == "unknown"
        assert row["redistribution_approved"] is False
        assert row["import_recommendation"] == "skip"
        assert row["sha256_expected"] is None
        assert row["classification"]["metadata_only"] is True
        assert row["classification"]["not_determinable_from_tree_metadata"]


def test_committed_csv_row_parity_and_hashes():
    with CSV_PATH.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 107
    assert rows[0]["path"] == "Active Visual Inference of Surface Shape - Roberto Cipolla.pdf"
    assert rows[0]["git_blob_sha1"] == "77f32883c7d7a5a66acc4b787afd9ff863f2a41a"
    assert {row["import_recommendation"] for row in rows} == {"skip"}


def test_library_scan_matches_local_bytes_by_exact_git_blob_id(tmp_path):
    inv = load_module()
    pdf = tmp_path / "acquired-copy.pdf"
    pdf.write_bytes(b"%PDF-1.7\nfixture bytes, not a real ebook")
    blob = inv.git_blob_sha1(pdf)
    inventory = {
        "files": [{
            "path": "Source Title - Author.pdf",
            "filename": "Source Title - Author.pdf",
            "size_bytes": pdf.stat().st_size,
            "git_blob_sha1": blob,
            "normalized_title": "source title author",
        }]
    }

    report = inv.scan_library(tmp_path, inventory)

    assert report["library_pdf_count"] == 1
    assert report["local_files"][0]["git_blob_sha1"] == blob
    assert len(report["local_files"][0]["sha256"]) == 64
    assert report["matches"] == [{
        "source_path": "Source Title - Author.pdf",
        "library_path": "acquired-copy.pdf",
        "match": "exact_git_blob_sha1",
    }]


def test_verify_offline_reproduces_artifact_from_committed_cache():
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--verify", "--offline"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "VERIFY PASS" in result.stdout
    assert "artifact rows match tree (107 files)" in result.stdout
    assert "no tracked PDFs in git index" in result.stdout


def test_no_pdf_was_added_to_the_repository_by_this_audit():
    inventory_dir = ARTIFACT_PATH.parent
    assert not list(inventory_dir.glob("*.pdf"))
    tracked = subprocess.run(
        ["git", "ls-files", "docs/inventory"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if tracked.returncode == 0:
        assert not [name for name in tracked.stdout.splitlines() if name.lower().endswith(".pdf")]