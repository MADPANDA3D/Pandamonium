#!/usr/bin/env python3
"""Publish a prepared package or atomically restore a signed catalog snapshot."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.marketplace_publish import _key, publish_package, update_catalog


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--package", type=Path)
    source.add_argument("--rollback", type=Path)
    parser.add_argument("--revision")
    parser.add_argument("--version")
    parser.add_argument("--owner", default="marketplace-validation")
    args = parser.parse_args()
    if args.rollback:
        result = update_catalog(
            None, _key(), rollback=json.loads(args.rollback.read_text())
        )
        print(
            json.dumps(
                {
                    "state": "restored",
                    "generated_at": result["generated_at"],
                    "entries": len(result["entries"]),
                }
            )
        )
    else:
        result = publish_package(
            args.package.read_bytes(),
            owner=args.owner,
            source_revision=args.revision,
            version=args.version,
            progress=lambda message: print(message, file=sys.stderr),
        )
        print(json.dumps(result))


if __name__ == "__main__":
    main()
