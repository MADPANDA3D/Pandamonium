"""Benchmark orchestration: run, baseline comparison, and scorecard output."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from benchmarks import HARNESS_VERSION, SUITE_VERSION
from benchmarks.engines import (
    load_fixtures,
    run_capture,
    run_fixture_scenario,
    run_fixture_suite,
)
from benchmarks.evaluator import evaluate_run
from benchmarks.suite import BASELINES_DIR, BENCH_DIR, load_scenarios

BASELINE_NAME = "v1.json"
RUNS_DIR = BENCH_DIR / "runs"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def load_baseline(version: str = SUITE_VERSION) -> Dict[str, Any]:
    path = BASELINES_DIR / f"{version}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if str(data.get("suite_version")) != version:
        raise ValueError(f"baseline {path} does not match suite {version}")
    if str(data.get("harness_version")) != HARNESS_VERSION:
        raise ValueError(
            f"baseline {path} targets harness {data.get('harness_version')!r}, "
            f"this is {HARNESS_VERSION!r}"
        )
    return data


def verify_asset_hashes(baseline: Dict[str, Any]) -> List[str]:
    """Verify the pinned hash of every versioned benchmark asset."""
    mismatches: List[str] = []
    for relative, expected in (baseline.get("asset_sha256") or {}).items():
        path = BENCH_DIR / relative
        if not path.exists():
            mismatches.append(f"missing asset: {relative}")
            continue
        actual = _sha256(path)
        if actual != expected:
            mismatches.append(f"hash mismatch: {relative} expected={expected} actual={actual}")
    return mismatches


def _scenario_from_case(raw: Dict[str, Any]):
    from benchmarks.suite import EvidenceRequirement, Scenario

    return Scenario(
        id=str(raw["id"]),
        goal_id=str(raw.get("goal_id") or raw["id"]),
        category=str(raw.get("category") or "capability_discovery"),
        surface=str(raw.get("surface") or "text"),
        prompt=str(raw.get("prompt") or "case prompt for evaluator contract"),
        constraints=["evaluator contract case"],
        require_tools=[[str(n) for n in group] for group in raw.get("require_tools") or []],
        require_gated=[str(n) for n in raw.get("require_gated") or []],
        forbidden_tools=[str(n) for n in raw.get("forbidden_tools") or []],
        evidence=[
            EvidenceRequirement(
                tool_any_of=[str(n) for n in item.get("tool_any_of") or []],
                match=str(item.get("match") or ""),
                description=str(item.get("description") or ""),
            )
            for item in raw.get("evidence") or []
        ],
        expect=str(raw.get("expect") or "success"),
        min_document_chars=int(raw.get("min_document_chars") or 0),
        max_words=int(raw.get("max_words") or 0),
    )


def run_evaluator_cases(baseline: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Run the negative/contract cases that pin the evaluator's semantics."""
    cases_path = BENCH_DIR / str(baseline.get("evaluator_cases") or "")
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    results = []
    for case in cases.get("cases") or []:
        scenario = _scenario_from_case(dict(case.get("scenario") or {}))
        capture = dict(case.get("capture") or {})
        capture["scenario_id"] = scenario.id
        run = run_capture(capture, [scenario])
        outcome = evaluate_run(run)
        expected = str(case.get("expected_verdict") or "pass")
        results.append(
            {
                "case_id": str(case.get("id")),
                "expected_verdict": expected,
                "actual_verdict": outcome["verdict"],
                "ok": outcome["verdict"] == expected,
                "false_success": outcome["false_success"],
                "failure_reason": outcome["failure_reason"],
            }
        )
    return results


def scorecard_markdown(
    results: List[Dict[str, Any]],
    meta: Dict[str, Any],
    violations: List[str],
    evaluator_cases: List[Dict[str, Any]],
) -> str:
    lines: List[str] = []
    lines.append("# Pandamonium Unscripted Tool-Use Scorecard")
    lines.append("")
    lines.append(f"- run id: `{meta['run_id']}`")
    lines.append(f"- generated: {meta['generated_at']}")
    lines.append(f"- engine: `{meta['engine']}`")
    lines.append(f"- suite: `{meta['suite_version']}` | harness: `{meta['harness_version']}`")
    lines.append(f"- status: **{'PASS' if not violations else 'FAIL'}**")
    lines.append("")
    by_class: Dict[str, List[Dict[str, Any]]] = {}
    for row in results:
        by_class.setdefault(str(row["model_class"]), []).append(row)
    for model_class, rows in sorted(by_class.items()):
        passed = sum(1 for row in rows if row["verdict"] == "pass")
        lines.append(f"## {model_class} — {passed}/{len(rows)} pass")
        lines.append("")
        lines.append(
            "| scenario | category | surface | verdict | model | adv ctx | eff ctx | prompt tok | out tok | latency ms | tools | failure reason |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for row in rows:
            tools = ", ".join(
                f"{call['name']}{'' if call['ok'] else '!'}" for call in row["tool_calls"]
            )
            lines.append(
                "| {id} | {category} | {surface} | {verdict} | {model} | {adv} | {eff} | {ptok} | {otok} | {lat} | {tools} | {reason} |".format(
                    id=row["scenario_id"],
                    category=row["category"],
                    surface=row["surface"],
                    verdict=row["verdict"].upper(),
                    model=row["model"],
                    adv=row["advertised_context"],
                    eff=row["effective_context"],
                    ptok=row["prompt_tokens"],
                    otok=row["output_tokens"],
                    lat=row["latency_ms"],
                    tools=tools or "-",
                    reason=row["failure_reason"] or "",
                )
            )
        lines.append("")
    lines.append("## Evaluator contract cases")
    lines.append("")
    lines.append("| case | expected | actual | ok | false success | reason |")
    lines.append("|---|---|---|---|---|---|")
    for row in evaluator_cases:
        lines.append(
            "| {case} | {exp} | {act} | {ok} | {fs} | {reason} |".format(
                case=row["case_id"],
                exp=row["expected_verdict"],
                act=row["actual_verdict"],
                ok="yes" if row["ok"] else "NO",
                fs="yes" if row["false_success"] else "no",
                reason=row["failure_reason"] or "",
            )
        )
    lines.append("")
    if violations:
        lines.append("## Baseline violations")
        lines.append("")
        for violation in violations:
            lines.append(f"- {violation}")
        lines.append("")
    lines.append("## Raw evidence")
    lines.append("")
    lines.append(f"- run metadata: `{meta['run_dir']}/run.json`")
    lines.append(f"- per-scenario results: `{meta['run_dir']}/results.json`")
    lines.append(f"- per-scenario transcripts: `{meta['run_dir']}/transcripts/`")
    lines.append(f"- this scorecard: `{meta['run_dir']}/scorecard.md`")
    lines.append("")
    lines.append("## CT103 acceptance (operator-run, separate from this offline run)")
    lines.append("")
    lines.append(
        "The offline fixture run above never proves live-model behavior. "
        "For CT103 acceptance, follow `docs/benchmarks/ct103-acceptance.md`: "
        "capture one run per scenario/model with the same goals, save each "
        "capture JSON, then score it offline with "
        "`python scripts/run_benchmark.py --engine capture --capture <file>`."
    )
    lines.append("")
    return "\n".join(lines)


def compare_to_baseline(results: List[Dict[str, Any]], baseline: Dict[str, Any]) -> List[str]:
    violations: List[str] = []
    expectations = baseline.get("expectations") or {}
    for model_class, expected in expectations.items():
        rows = [row for row in results if row["model_class"] == model_class]
        if not rows:
            violations.append(f"baseline expects model class {model_class} but the run produced none")
            continue
        passed = sum(1 for row in rows if row["verdict"] == "pass")
        min_pass = int(expected.get("min_pass") or 0)
        if passed < min_pass:
            violations.append(
                f"{model_class}: {passed} pass < baseline min_pass {min_pass}"
            )
        for scenario_id in expected.get("required_pass") or []:
            row = next((entry for entry in rows if entry["scenario_id"] == scenario_id), None)
            if row is None:
                violations.append(f"{model_class}: baseline requires {scenario_id} but it was not run")
            elif row["verdict"] != "pass":
                violations.append(
                    f"{model_class}: {scenario_id} failed baseline requirement: {row['failure_reason']}"
                )
    return violations


def run_benchmark(
    version: str = SUITE_VERSION,
    model_classes: Optional[List[str]] = None,
    run_dir: Optional[Path] = None,
    engine: str = "fixture",
) -> Dict[str, Any]:
    baseline = load_baseline(version)
    scenarios = load_scenarios(version)
    hash_mismatches = verify_asset_hashes(baseline)
    if hash_mismatches:
        raise ValueError("benchmark asset hash mismatch: " + "; ".join(hash_mismatches))

    if model_classes is None:
        model_classes = sorted((baseline.get("expectations") or {}).keys())
    if not model_classes:
        raise ValueError("no model classes requested and the baseline defines none")

    run_id = f"{_now()}-{os.getpid()}"
    target = Path(run_dir) if run_dir else RUNS_DIR / run_id
    target.mkdir(parents=True, exist_ok=True)

    results: List[Dict[str, Any]] = []
    transcripts_dir = target / "transcripts"
    fixtures = load_fixtures()
    for model_class in model_classes:
        class_dir = transcripts_dir / model_class
        class_dir.mkdir(parents=True, exist_ok=True)
        for run in run_fixture_suite(scenarios, model_class, fixtures):
            outcome = evaluate_run(run)
            results.append(outcome)
            (class_dir / f"{run.scenario.id}.json").write_text(
                json.dumps(
                    {
                        "scenario_id": run.scenario.id,
                        "model": run.model,
                        "model_class": run.model_class,
                        "fixture_origin": run.fixture_origin,
                        "turns": run.turns,
                        "final_answer": run.final_answer,
                        "evaluated": outcome,
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

    evaluator_cases = run_evaluator_cases(baseline) if engine == "fixture" else []
    violations = compare_to_baseline(results, baseline)
    violations.extend(
        f"evaluator case {row['case_id']}: expected {row['expected_verdict']}, got {row['actual_verdict']}"
        for row in evaluator_cases
        if not row["ok"]
    )

    meta = {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "engine": engine,
        "suite_version": version,
        "harness_version": HARNESS_VERSION,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "git_revision": _git_revision(),
        "run_dir": str(target),
        "asset_sha256": baseline.get("asset_sha256") or {},
    }
    (target / "run.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    (target / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    (target / "evaluator-cases.json").write_text(
        json.dumps(evaluator_cases, indent=2), encoding="utf-8"
    )
    scorecard = scorecard_markdown(results, meta, violations, evaluator_cases)
    (target / "scorecard.md").write_text(scorecard, encoding="utf-8")

    return {
        "status": "fail" if violations else "pass",
        "run_id": run_id,
        "run_dir": str(target),
        "results": results,
        "evaluator_cases": evaluator_cases,
        "violations": violations,
        "scorecard": scorecard,
    }


def _git_revision() -> str:
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=str(BENCH_DIR.parent),
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
        )
    except Exception:
        return ""