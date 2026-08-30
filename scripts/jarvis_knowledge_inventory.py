#!/usr/bin/env python3
"""Read-only JOS P3 inventory for a KarpathyWiki output tree."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.knowledge_source_policy import inventory_markdown


def _plugin_version(path: Path) -> str | None:
    try:
        return str(json.loads(path.read_text(encoding="utf-8")).get("version") or "") or None
    except (OSError, json.JSONDecodeError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-root", type=Path, required=True)
    parser.add_argument("--wiki-root", type=Path, required=True)
    parser.add_argument("--plugin-manifest", type=Path)
    args = parser.parse_args()

    version = _plugin_version(args.plugin_manifest) if args.plugin_manifest else None
    records = inventory_markdown(
        args.wiki_root.rglob("*.md"),
        root=args.vault_root,
        wiki_root=args.wiki_root,
        generation_version=version,
    )
    reasons = Counter(record["exclusion_reason"] or "indexable" for record in records)
    print(json.dumps({
        "vault_root": str(args.vault_root.resolve()),
        "wiki_root": str(args.wiki_root.resolve()),
        "generation_version": version,
        "total": len(records),
        "indexable": sum(1 for record in records if record["indexable"]),
        "excluded": sum(1 for record in records if not record["indexable"]),
        "reasons": dict(sorted(reasons.items())),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
