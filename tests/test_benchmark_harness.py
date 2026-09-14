"""MAD-785: unscripted tool-use benchmark harness contract tests.

These tests pin the prompt-hygiene rule, the evidence-based scoring rules
(including that a failed or unavailable tool can never be reported as
success), text/voice goal pairing, local/flagship comparability, offline
runnability, and the versioned baseline.
"""

from __future__ import annotations

import json
import re
import socket
from pathlib import Path

import pytest

from benchmarks import HARNESS_VERSION, SUITE_VERSION
from benchmarks.engines import load_fixtures, run_capture
from benchmarks.evaluator import evaluate_run
from benchmarks.harness import (
    load_baseline,
    run_benchmark,
    run_evaluator_cases,
    verify_asset_hashes,
)
from benchmarks.suite import BENCH_DIR, known_tool_vocab, load_scenarios


@pytest.fixture(scope="module")
def scenarios():
    return load_scenarios(SUITE_VERSION)


@pytest.fixture(scope="module")
def baseline():
    return load_baseline(SUITE_VERSION)


@pytest.fixture(scope="module")
def benchmark_report(tmp_path_factory):
    run_dir = tmp_path_factory.mktemp("benchmark-run")
    return run_benchmark(version=SUITE_VERSION, run_dir=run_dir)


def test_prompts_state_goals_without_naming_tools_or_formats(scenarios):
    vocab = known_tool_vocab()
    assert "manage_notes" in vocab and "read_calendar" in vocab
    for scenario in scenarios:
        for name in vocab:
            assert not re.search(
                rf"(?<![\w-]){re.escape(name)}(?![\w-])", scenario.prompt, re.I
            ), f"{scenario.id} names tool {name}"
        lowered = scenario.prompt.lower()
        for hint in ("json", "fenced", "function call", "tool_call", "```"):
            assert hint not in lowered, f"{scenario.id} prescribes format {hint!r}"
        assert scenario.constraints, f"{scenario.id} must state a constraint"


def test_offline_fixture_run_passes_versioned_baseline(benchmark_report):
    assert benchmark_report["status"] == "pass", benchmark_report["violations"]
    assert benchmark_report["violations"] == []
    assert len(benchmark_report["results"]) == 32
    assert len(benchmark_report["evaluator_cases"]) == 14
    run_dir = Path(benchmark_report["run_dir"])
    for name in ("run.json", "results.json", "evaluator-cases.json", "scorecard.md"):
        assert (run_dir / name).is_file(), name
    assert len(list((run_dir / "transcripts" / "fixture-local").glob("*.json"))) == 16
    assert len(list((run_dir / "transcripts" / "fixture-flagship").glob("*.json"))) == 16
    scorecard = (run_dir / "scorecard.md").read_text(encoding="utf-8")
    assert "PASS" in scorecard
    assert "fixture-local" in scorecard and "fixture-flagship" in scorecard


def test_run_is_fully_offline(benchmark_report, monkeypatch, tmp_path):
    calls = {"n": 0}

    def _blocked(*args, **kwargs):  # pragma: no cover - only runs on violation
        calls["n"] += 1
        raise AssertionError("benchmark attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    report = run_benchmark(version=SUITE_VERSION, run_dir=tmp_path / "offline-run")
    assert report["status"] == "pass"
    assert calls["n"] == 0


def test_results_capture_required_fields(benchmark_report):
    required = {
        "scenario_id",
        "category",
        "surface",
        "verdict",
        "model",
        "model_class",
        "advertised_context",
        "effective_context",
        "prompt_tokens",
        "output_tokens",
        "latency_ms",
        "tool_calls",
        "evidence",
        "failure_reason",
    }
    for row in benchmark_report["results"]:
        assert required <= set(row), row["scenario_id"]
        for field in ("advertised_context", "effective_context", "prompt_tokens", "output_tokens", "latency_ms"):
            assert isinstance(row[field], int)
        assert all({"name", "ok", "gated", "latency_ms"} <= set(call) for call in row["tool_calls"])


def test_failed_or_unavailable_tool_cannot_be_reported_as_success(baseline):
    rows = {row["case_id"]: row for row in run_evaluator_cases(baseline)}
    assert rows["claims-success-without-any-tool"]["actual_verdict"] == "fail"
    assert rows["claims-success-without-any-tool"]["false_success"] is True
    assert rows["claims-success-after-tool-error"]["actual_verdict"] == "fail"
    assert rows["claims-success-after-tool-error"]["false_success"] is True
    assert rows["unavailable-tool-cannot-succeed"]["actual_verdict"] == "fail"
    assert rows["gated-claims-complete"]["actual_verdict"] == "fail"
    assert rows["gated-claims-complete"]["false_success"] is True
    assert rows["gated-asks-approval-passes"]["actual_verdict"] == "pass"
    assert rows["recovery-false-success"]["actual_verdict"] == "fail"
    assert rows["forbidden-shell-fallback"]["actual_verdict"] == "fail"
    assert all(row["ok"] for row in rows.values()), rows


def test_text_and_voice_pairs_share_goal_and_evidence(scenarios, benchmark_report):
    rows = {
        (row["scenario_id"], row["model_class"]): row for row in benchmark_report["results"]
    }
    goals = {}
    for scenario in scenarios:
        goals.setdefault(scenario.goal_id, []).append(scenario)
    paired_goals = 0
    for goal_id, members in goals.items():
        surfaces = {member.surface for member in members}
        if surfaces != {"text", "voice"}:
            continue
        paired_goals += 1
        for model_class in ("fixture-local", "fixture-flagship"):
            for member in members:
                row = rows[(member.id, model_class)]
                assert row["verdict"] == "pass", (goal_id, member.id, row["failure_reason"])
        # Equivalent capability evidence, not identical prose.
        text_evidence = {
            item["match"] for item in rows[(members[0].id, "fixture-local")]["evidence"]
        }
        voice_member = next(m for m in members if m.surface == "voice")
        voice_evidence = {
            item["match"] for item in rows[(voice_member.id, "fixture-local")]["evidence"]
        }
        assert text_evidence == voice_evidence, goal_id
    assert paired_goals == 4


def test_local_and_flagship_are_comparable_without_identical_prose(benchmark_report):
    local = {row["scenario_id"]: row for row in benchmark_report["results"] if row["model_class"] == "fixture-local"}
    flagship = {row["scenario_id"]: row for row in benchmark_report["results"] if row["model_class"] == "fixture-flagship"}
    assert set(local) == set(flagship)
    differing_prose = 0
    for scenario_id in local:
        assert local[scenario_id]["verdict"] == flagship[scenario_id]["verdict"] == "pass"
        if local[scenario_id]["final_answer"] != flagship[scenario_id]["final_answer"]:
            differing_prose += 1
    assert differing_prose >= 10
    assert local["long-response"]["effective_context"] < flagship["long-response"]["effective_context"]


def test_effective_context_bounds_every_prompt(benchmark_report):
    for row in benchmark_report["results"]:
        assert row["prompt_tokens"] <= row["effective_context"], row["scenario_id"]
    local = benchmark_report["results"][0]
    assert local["effective_context"] < local["advertised_context"]


def test_baseline_is_versioned_and_pins_every_asset(baseline):
    assert baseline["suite_version"] == SUITE_VERSION
    assert baseline["harness_version"] == HARNESS_VERSION
    assert set(baseline["asset_sha256"]) == {
        "suite/v1/suite.json",
        "fixtures/v1/suite-fixtures.json",
        "fixtures/v1/evaluator-cases.json",
    }
    assert verify_asset_hashes(baseline) == []
    tampered = {"asset_sha256": {"suite/v1/suite.json": "0" * 64}}
    assert verify_asset_hashes(tampered)


def test_fixtures_cover_every_scenario_for_every_class(scenarios):
    fixtures = load_fixtures()
    mapping = fixtures["scenarios"]
    for scenario in scenarios:
        for model_class in ("fixture-local", "fixture-flagship"):
            transcript = mapping[scenario.id][model_class]
            assert transcript["turns"], scenario.id
            assert transcript["model_class"] == model_class


def test_capture_engine_scores_operator_runs(scenarios, tmp_path):
    scenario = next(row for row in scenarios if row.id == "calendar-read-text")
    passing = {
        "scenario_id": scenario.id,
        "model": "ct103-local",
        "model_class": "local",
        "advertised_context": 32768,
        "prompt_tokens": 900,
        "effective_context": 27852,
        "latency_ms": 2200,
        "final_answer": "Design review at 15:00.",
        "tool_calls": [
            {
                "name": "read_calendar",
                "args": {"date": "2026-09-14"},
                "ok": True,
                "result": {"events": [{"summary": "Design review"}]},
                "result_text": "Design review",
            }
        ],
    }
    assert evaluate_run(run_capture(passing, scenarios))["verdict"] == "pass"
    failing = dict(passing)
    failing["final_answer"] = "Done, I deleted everything."
    failing["tool_calls"] = []
    assert evaluate_run(run_capture(failing, scenarios))["verdict"] == "fail"
    with pytest.raises(KeyError):
        run_capture({"scenario_id": "not-a-scenario"}, scenarios)


def test_live_acceptance_is_separate_and_documented():
    doc = BENCH_DIR.parent / "docs" / "benchmarks" / "ct103-acceptance.md"
    template = BENCH_DIR / "scorecard-template.md"
    assert doc.is_file() and template.is_file()
    doc_text = doc.read_text(encoding="utf-8")
    assert "CT103" in doc_text and "--engine capture" in doc_text
    assert "scorecard-template.md" in doc.read_text(encoding="utf-8") or "scorecard-template.md" in template.read_text(encoding="utf-8")


def test_cli_runs_fixture_suite_and_capture_engine(tmp_path):
    import importlib.util

    script = BENCH_DIR.parent / "scripts" / "run_benchmark.py"
    spec = importlib.util.spec_from_file_location("run_benchmark_cli", script)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    fixture_dir = tmp_path / "fixture-run"
    assert cli.main(["--engine", "fixture", "--run-dir", str(fixture_dir), "--json"]) == 0
    payload = json.loads((fixture_dir / "run.json").read_text(encoding="utf-8"))
    assert payload["engine"] == "fixture"

    capture = {
        "scenario_id": "safe-mutation-notes",
        "model_class": "local",
        "final_answer": "Added it.",
        "tool_calls": [],
    }
    capture_path = tmp_path / "capture.json"
    capture_path.write_text(json.dumps(capture), encoding="utf-8")
    assert cli.main(["--engine", "capture", "--capture", str(capture_path), "--json"]) == 1