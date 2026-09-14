"""Versioned suite loading and prompt-hygiene validation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from benchmarks import SUITE_VERSION

BENCH_DIR = Path(__file__).resolve().parent
SUITES_DIR = BENCH_DIR / "suite"
FIXTURES_DIR = BENCH_DIR / "fixtures"
BASELINES_DIR = BENCH_DIR / "baselines"

SURFACES = {"text", "voice"}
CATEGORIES = {
    "capability_discovery",
    "files",
    "books_rag",
    "calendar",
    "integrations",
    "oracle_read_control",
    "safe_mutation",
    "gated_mutation",
    "failure_recovery",
    "long_response",
}
EXPECTATIONS = {"success", "gated", "recovery"}

# Prompt hygiene: a goal prompt must not name the tool, describe the response
# format, or leak hidden expected steps.  These fragments catch the common
# ways that leaks into a benchmark prompt.
_FORMAT_HINTS = (
    "json",
    "fenced",
    "function call",
    "tool_call",
    "tool call",
    "call the",
    "use the",
    "invoke",
    "code block",
    "```",
    "<invoke",
    "api endpoint",
    "http verb",
    "get /",
    "post /",
)
_HIDDEN_STEP_HINTS = (
    "first,",
    "then,",
    "step 1",
    "step 2",
    "after that",
    "list_calendars",
    "list_tools",
)


def known_tool_vocab() -> set[str]:
    """Return the union of real and mock tool names for prompt-hygiene checks."""
    names: set[str] = set()
    try:
        from src.agent_tools import TOOL_TAGS

        names.update(str(tag) for tag in TOOL_TAGS)
    except Exception:
        pass
    try:
        from benchmarks.mocks import MOCK_TOOL_NAMES

        names.update(MOCK_TOOL_NAMES)
    except Exception:
        pass
    return names


@dataclass
class EvidenceRequirement:
    tool_any_of: List[str]
    match: str
    description: str = ""


@dataclass
class Scenario:
    id: str
    goal_id: str
    category: str
    surface: str
    prompt: str
    constraints: List[str] = field(default_factory=list)
    require_tools: List[List[str]] = field(default_factory=list)
    require_gated: List[str] = field(default_factory=list)
    forbidden_tools: List[str] = field(default_factory=list)
    evidence: List[EvidenceRequirement] = field(default_factory=list)
    expect: str = "success"
    min_document_chars: int = 0
    max_words: int = 0
    unavailable: List[str] = field(default_factory=list)
    failing: Dict[str, str] = field(default_factory=dict)
    mock_state: Dict[str, Any] = field(default_factory=dict)


def suite_path(version: str = SUITE_VERSION) -> Path:
    return SUITES_DIR / version / "suite.json"


def _evidence_from(raw: Any) -> List[EvidenceRequirement]:
    rows = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        tool_any_of = [str(name) for name in (item.get("tool_any_of") or [])]
        match = str(item.get("match") or "")
        if not tool_any_of or not match:
            raise ValueError(f"invalid evidence requirement: {item!r}")
        rows.append(
            EvidenceRequirement(
                tool_any_of=tool_any_of,
                match=match,
                description=str(item.get("description") or ""),
            )
        )
    return rows


def load_scenarios(version: str = SUITE_VERSION) -> List[Scenario]:
    path = suite_path(version)
    data = json.loads(path.read_text(encoding="utf-8"))
    if str(data.get("suite_version")) != version:
        raise ValueError(
            f"suite version mismatch: file says {data.get('suite_version')!r}, requested {version!r}"
        )
    scenarios: List[Scenario] = []
    seen: set[str] = set()
    for raw in data.get("scenarios") or []:
        scenario = Scenario(
            id=str(raw["id"]),
            goal_id=str(raw["goal_id"]),
            category=str(raw["category"]),
            surface=str(raw["surface"]),
            prompt=str(raw["prompt"]),
            constraints=[str(c) for c in raw.get("constraints") or []],
            require_tools=[[str(n) for n in group] for group in raw.get("require_tools") or []],
            require_gated=[str(n) for n in raw.get("require_gated") or []],
            forbidden_tools=[str(n) for n in raw.get("forbidden_tools") or []],
            evidence=_evidence_from(raw.get("evidence")),
            expect=str(raw.get("expect") or "success"),
            min_document_chars=int(raw.get("min_document_chars") or 0),
            max_words=int(raw.get("max_words") or 0),
            unavailable=[str(n) for n in raw.get("unavailable") or []],
            failing={str(k): str(v) for k, v in (raw.get("failing") or {}).items()},
            mock_state=dict(raw.get("mock_state") or {}),
        )
        if scenario.id in seen:
            raise ValueError(f"duplicate scenario id: {scenario.id}")
        seen.add(scenario.id)
        validate_scenario(scenario)
        scenarios.append(scenario)
    if not scenarios:
        raise ValueError("suite contains no scenarios")
    return scenarios


def validate_scenario(scenario: Scenario) -> None:
    if scenario.category not in CATEGORIES:
        raise ValueError(f"{scenario.id}: unknown category {scenario.category!r}")
    if scenario.surface not in SURFACES:
        raise ValueError(f"{scenario.id}: unknown surface {scenario.surface!r}")
    if scenario.expect not in EXPECTATIONS:
        raise ValueError(f"{scenario.id}: unknown expectation {scenario.expect!r}")
    validate_prompt(scenario)


def validate_prompt(scenario: Scenario) -> None:
    """Enforce the unscripted-prompt contract for one scenario."""
    prompt = scenario.prompt
    lowered = prompt.lower()
    vocab = known_tool_vocab()
    leaked = sorted(
        name for name in vocab if re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", prompt, re.I)
    )
    if leaked:
        raise ValueError(f"{scenario.id}: prompt names tools: {leaked}")
    for hint in _FORMAT_HINTS:
        if hint in lowered:
            raise ValueError(f"{scenario.id}: prompt prescribes a format: {hint!r}")
    if len(scenario.prompt.strip()) < 20:
        raise ValueError(f"{scenario.id}: prompt is too short to state a goal")
    if not scenario.constraints:
        raise ValueError(f"{scenario.id}: prompt must carry at least one stated constraint")
    for step in _HIDDEN_STEP_HINTS:
        if step in lowered:
            raise ValueError(f"{scenario.id}: prompt leaks a hidden step: {step!r}")