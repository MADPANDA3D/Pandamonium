"""Benchmark engines: offline fixture replay and operator capture scoring.

Neither engine calls a model.  ``FixtureEngine`` replays recorded assistant
turns through the real tool-block parser and real tool-result formatter against
the in-process mock world, so the offline baseline exercises the same parsing
and evidence surface a live run does.  ``CaptureEngine`` scores a transcript
captured elsewhere (for example a CT103 operator run).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from benchmarks.evaluator import ScenarioRun, ToolInvocation, _result_text
from benchmarks.mocks import MOCK_TOOL_NAMES, world_for_scenario
from benchmarks.suite import FIXTURES_DIR, Scenario

FIXTURE_FILE = FIXTURES_DIR / "v1" / "suite-fixtures.json"

# Extension-native capabilities (for example ORACLE's map tools) are declared
# by the installed extension at runtime, so they are not in the static built-in
# TOOL_TAGS the real parser is built from.  The fixture engine recognises the
# mock-declared names too, with the same fenced-block shape.
_MOCK_FENCE_RE = re.compile(
    r"```([A-Za-z_][\w.]*)[ \t]*([{\[][^\n]*?)?[ \t]*(?:\r?\n)?([\s\S]*?)```"
)


class _MockBlock:
    __slots__ = ("name", "args")

    def __init__(self, name: str, args: Dict[str, Any]):
        self.name = name
        self.args = args

_SYSTEM_PREAMBLE = (
    "You are an AI assistant with tool access. Only the tools listed below are "
    "available for this turn. Use them when they materially help the request; "
    "never claim an action succeeded without a tool result."
)


def load_fixtures(path: Optional[Path] = None) -> Dict[str, Any]:
    source = Path(path) if path else FIXTURE_FILE
    data = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"fixture file {source} is not a JSON object")
    return data


def _estimate_prompt_tokens(scenario: Scenario) -> int:
    try:
        from src.model_context import estimate_tokens

        return int(
            estimate_tokens(
                [
                    {"role": "system", "content": _SYSTEM_PREAMBLE},
                    {"role": "user", "content": scenario.prompt},
                ]
            )
        )
    except Exception:
        return max(1, len(scenario.prompt.split()) * 2)


def _estimate_output_tokens(turns: List[str]) -> int:
    try:
        from src.model_context import estimate_tokens

        return int(
            estimate_tokens([{"role": "assistant", "content": turn} for turn in turns])
        )
    except Exception:
        return sum(max(1, len(turn.split()) * 2) for turn in turns)


def _effective_context(advertised: int) -> int:
    try:
        from src.context_budget import compute_input_token_budget

        return int(compute_input_token_budget(0, int(advertised or 0), False))
    except Exception:
        return int(advertised or 0)


def _block_key(name: str, args: Any) -> str:
    try:
        return name + "\x1f" + json.dumps(args or {}, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return name + "\x1f" + str(args)


def _normalize_real_block(block: Any) -> Optional[_MockBlock]:
    """Convert a real ToolBlock (tool_type/content) into a mock block."""
    name = str(
        getattr(block, "tool_type", None)
        or getattr(block, "name", None)
        or ""
    ).strip()
    if not name:
        return None
    raw = getattr(block, "content", None)
    if raw is None:
        raw = getattr(block, "args", None)
    args: Dict[str, Any] = {}
    if isinstance(raw, dict):
        args = dict(raw)
    elif isinstance(raw, str) and raw.strip():
        text = raw.strip()
        if text.startswith("{"):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    args = parsed
            except json.JSONDecodeError:
                args = {"content": text}
        else:
            args = {"content": text}
    return _MockBlock(name, args)


def _parse_blocks(text: str) -> List[Any]:
    blocks: List[_MockBlock] = []
    seen: set = set()
    try:
        # Import the facade first: src.tool_parsing depends on src.agent_tools
        # finishing its wiring, and importing tool_parsing directly in a fresh
        # process hits the documented circular-import edge.
        import src.agent_tools  # noqa: F401

        from src.tool_parsing import parse_tool_blocks

        for raw in parse_tool_blocks(text or ""):
            normalized = _normalize_real_block(raw)
            if normalized is None:
                continue
            key = _block_key(normalized.name, normalized.args)
            if key in seen:
                continue
            seen.add(key)
            blocks.append(normalized)
    except Exception:
        pass
    for match in _MOCK_FENCE_RE.finditer(text or ""):
        name = match.group(1)
        if name not in MOCK_TOOL_NAMES:
            continue
        raw_args = (match.group(3) or "").strip()
        args: Dict[str, Any] = {}
        if raw_args:
            try:
                parsed = json.loads(raw_args)
                if isinstance(parsed, dict):
                    args = parsed
                else:
                    continue
            except json.JSONDecodeError:
                continue
        key = _block_key(name, args)
        if key in seen:
            continue
        seen.add(key)
        blocks.append(_MockBlock(name, args))
    return blocks


def _strip_blocks(text: str) -> str:
    try:
        from src.agent_tools import strip_tool_blocks

        return strip_tool_blocks(text or "")
    except Exception:
        return text or ""


def _format_result(tool_name: str, result: Dict[str, Any]) -> str:
    try:
        from src.tool_execution import format_tool_result

        return format_tool_result(tool_name, result)
    except Exception:
        return _result_text(result)


def _invocation_from_call(call: Any, outcome: Any, latency_ms: int) -> ToolInvocation:
    return ToolInvocation(
        name=str(getattr(call, "name", "") or ""),
        args=dict(getattr(call, "args", {}) or {}),
        ok=bool(outcome.ok),
        result=dict(outcome.result or {}),
        result_text=_format_result(str(getattr(call, "name", "") or ""), dict(outcome.result or {})),
        latency_ms=int(latency_ms),
        gated=bool(outcome.gated),
        error="" if outcome.ok else str((outcome.result or {}).get("error") or ""),
    )


def run_fixture_scenario(
    scenario: Scenario,
    transcript: Dict[str, Any],
) -> ScenarioRun:
    """Replay one recorded transcript through the mock world."""
    world = world_for_scenario(scenario)
    turns = [str(turn) for turn in transcript.get("turns") or []]
    turn_latencies = list(transcript.get("latency_ms") or [])
    invocations: List[ToolInvocation] = []
    for index, turn in enumerate(turns):
        blocks = _parse_blocks(turn)
        calls_this_turn = len(blocks)
        turn_latency = int(turn_latencies[index]) if index < len(turn_latencies) else 0
        for block in blocks:
            outcome = world.dispatch(str(getattr(block, "name", "") or ""), dict(getattr(block, "args", {}) or {}))
            latency = turn_latency // max(1, calls_this_turn)
            invocations.append(_invocation_from_call(block, outcome, latency))

    final_answer = ""
    for turn in turns:
        answer = _strip_blocks(turn).strip()
        if answer:
            final_answer = answer

    advertised = int(transcript.get("advertised_context") or 0)
    return ScenarioRun(
        scenario=scenario,
        model=str(transcript.get("model") or "fixture-model"),
        model_class=str(transcript.get("model_class") or "fixture"),
        advertised_context=advertised,
        effective_context=_effective_context(advertised),
        prompt_tokens=_estimate_prompt_tokens(scenario),
        output_tokens=_estimate_output_tokens(turns),
        latency_ms=sum(int(value) for value in turn_latencies),
        invocations=invocations,
        final_answer=final_answer,
        turns=turns,
        fixture_origin=str(transcript.get("origin") or "fixture"),
    )


def run_fixture_suite(
    scenarios: List[Scenario],
    model_class: str,
    fixtures: Optional[Dict[str, Any]] = None,
) -> List[ScenarioRun]:
    data = fixtures if fixtures is not None else load_fixtures()
    scenario_map = data.get("scenarios") if isinstance(data.get("scenarios"), dict) else data
    runs = []
    for scenario in scenarios:
        per_scenario = scenario_map.get(scenario.id) or {}
        transcript = per_scenario.get(model_class)
        if not transcript:
            raise KeyError(f"fixture missing: {scenario.id}/{model_class}")
        runs.append(run_fixture_scenario(scenario, transcript))
    return runs


def run_capture(capture: Dict[str, Any], scenarios: List[Scenario]) -> ScenarioRun:
    """Score an operator-captured run with the same evaluator contract."""
    scenario_id = str(capture.get("scenario_id") or "")
    scenario = next((row for row in scenarios if row.id == scenario_id), None)
    if scenario is None:
        raise KeyError(f"capture references unknown scenario: {scenario_id}")
    invocations = []
    for raw in capture.get("tool_calls") or []:
        result = dict(raw.get("result") or {})
        invocations.append(
            ToolInvocation(
                name=str(raw.get("name") or ""),
                args=dict(raw.get("args") or {}),
                ok=bool(raw.get("ok")),
                result=result,
                result_text=str(raw.get("result_text") or _result_text(result)),
                latency_ms=int(raw.get("latency_ms") or 0),
                gated=bool(raw.get("gated")),
                error=str(raw.get("error") or ""),
            )
        )
    advertised = int(capture.get("advertised_context") or 0)
    return ScenarioRun(
        scenario=scenario,
        model=str(capture.get("model") or "captured-model"),
        model_class=str(capture.get("model_class") or "capture"),
        advertised_context=advertised,
        effective_context=int(capture.get("effective_context") or _effective_context(advertised)),
        prompt_tokens=int(capture.get("prompt_tokens") or 0),
        output_tokens=int(capture.get("output_tokens") or 0),
        latency_ms=int(capture.get("latency_ms") or 0),
        invocations=invocations,
        final_answer=str(capture.get("final_answer") or ""),
        turns=list(capture.get("turns") or []),
        fixture_origin="capture",
    )