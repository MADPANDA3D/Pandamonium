#!/usr/bin/env python3
"""Run the Pandamonium unscripted tool-use benchmark (MAD-785).

Offline (default): replays the versioned deterministic fixtures against the
in-process mock services and writes a scorecard plus raw evidence.

    python scripts/run_benchmark.py --engine fixture

Capture (CT103/live acceptance): score transcripts captured elsewhere with the
same hidden rubrics.

    python scripts/run_benchmark.py --engine capture --capture run.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks import HARNESS_VERSION, SUITE_VERSION
from benchmarks.engines import run_capture
from benchmarks.evaluator import evaluate_run
from benchmarks.harness import load_baseline, run_benchmark
from benchmarks.suite import load_scenarios


def _load_captures(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("captures"), list):
        return data["captures"]
    return [data]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=("fixture", "capture"), default="fixture")
    parser.add_argument("--suite", default=SUITE_VERSION)
    parser.add_argument(
        "--model-class",
        action="append",
        default=None,
        help="fixture model class to run (repeatable; default: all baseline classes)",
    )
    parser.add_argument("--run-dir", default=None, help="output directory for run artifacts")
    parser.add_argument("--capture", default=None, help="capture JSON for --engine capture")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    args = parser.parse_args(argv)

    if args.engine == "capture":
        if not args.capture:
            parser.error("--engine capture requires --capture FILE")
        scenarios = load_scenarios(args.suite)
        results = []
        for raw in _load_captures(Path(args.capture)):
            outcome = evaluate_run(run_capture(raw, scenarios))
            results.append(outcome)
        failed = [row for row in results if row["verdict"] != "pass"]
        if args.json:
            print(json.dumps(results, indent=2, ensure_ascii=False))
        else:
            for row in results:
                line = f"{row['scenario_id']}: {row['verdict'].upper()}"
                if row["failure_reason"]:
                    line += f" ({row['failure_reason']})"
                print(line)
        return 1 if failed else 0

    report = run_benchmark(
        version=args.suite,
        model_classes=args.model_class,
        run_dir=Path(args.run_dir) if args.run_dir else None,
        engine="fixture",
    )
    if args.json:
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "run_id": report["run_id"],
                    "run_dir": report["run_dir"],
                    "violations": report["violations"],
                    "harness_version": HARNESS_VERSION,
                },
                indent=2,
            )
        )
    else:
        print(report["scorecard"])
        print(f"\nstatus: {report['status']} (run dir: {report['run_dir']})")
    return 1 if report["status"] != "pass" else 0


if __name__ == "__main__":
    raise SystemExit(main())