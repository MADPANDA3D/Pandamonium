"""The approved 30-source list cannot silently become a successful subset."""

import json
import re
from pathlib import Path


def test_complete_inventory_matches_requested_sources_and_published_capabilities():
    root = Path(__file__).resolve().parents[1]
    inventory = json.loads((root / "docs/mad-963-inventory.json").read_text())
    requested = re.findall(
        r"^\| (\d+) \| ([^|]+) \| ([^|]+) \| ([^|]+) \|$",
        (root / "docs/repository-plugin-ingestion-plan.md").read_text(),
        re.MULTILINE,
    )
    entries = inventory["entries"]
    assert len(entries) == len(requested) == 30
    catalog = json.loads((root / "marketplace/catalog.json").read_text())
    published = {e["manifest"]["extension_id"]: e for e in catalog["entries"]}
    accounted = set()
    for entry, (number, name, source, target) in zip(entries, requested):
        assert entry["number"] == int(number)
        assert (entry["name"], entry["requested_source"], entry["target"]) == (
            name.strip(), source.strip(), target.strip()
        )
        assert entry["coverage"] and entry["outstanding"]
        if entry["number"] in {9, 10, 29}:
            assert entry["status"] == "held_out_MAD-964"
            assert not entry["source_revision"] and not entry["packages"]
            continue
        if entry["number"] != 8:
            assert re.fullmatch(r"[a-f0-9]{40}", entry["source_revision"])
        for package in entry["packages"]:
            assert package["temporary_data_removed"]
            assert package["source_revision"] == entry["source_revision"]
            assert package["lifecycle"] == ["install", "disable", "enable", "uninstall"]
            publication = package["publication"]
            if publication["state"] != "published":
                assert publication["reason"]
                continue
            live = published[package["extension_id"]]
            assert live["artifact"]["sha256"] == publication["digest"]
            assert live["manifest"]["version"] == publication["version"]
            caps = live["manifest"]["capabilities"]
            declared = set(caps["descriptor"].get("include", [])) or {
                s["function"]["name"] for s in caps["schemas"]
            }
            assert declared == {
                op.get("tool", op.get("skill")) for op in package["operations"]
            }
            accounted.add(package["extension_id"])
    assert accounted == set(published)
    assert inventory["personal_account_installs"] == 0
