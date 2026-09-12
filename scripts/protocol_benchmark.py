#!/usr/bin/env python3
"""Deterministic protocol mount benchmark (MAD-893).

Stub mode measures mounted packs, declared protocol budgets, and rendered
protocol tokens per scenario without calling a model. Live comparison is
operator-run: use the same scenario prompts in Pandamonium and compare tool
selection, completion, evidence, latency, and token use.

    python scripts/protocol_benchmark.py --engine stub
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SCENARIOS = (
    ("trivial_chat", "hello there", []),
    ("shell_action", "run uptime on the server", ["shell"]),
    ("web_research", "research the latest DeepSeek pricing", ["research"]),
    ("calendar_action", "add lunch to my calendar tomorrow", ["calendar"]),
)


def _body_tokens(body: str) -> int:
    from src.model_context import estimate_tokens

    return estimate_tokens([{"role": "system", "content": body}])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", choices=("stub", "live"), default="stub")
    parser.add_argument("--endpoint", default="")
    parser.add_argument("--model", default="")
    args = parser.parse_args()

    if args.engine == "live":
        print(
            json.dumps(
                {
                    "status": "operator-run",
                    "endpoint": args.endpoint,
                    "model": args.model,
                    "note": (
                        "Run the scenario prompts in Pandamonium and compare tool selection, "
                        "completion, evidence, latency, and token use against the stub report."
                    ),
                },
                indent=2,
            )
        )
        return 0

    from src.model_context import estimate_tokens
    from src.protocol_registry import mounted_protocol_packs, render_protocol_block

    report = {"engine": "stub", "scenarios": []}
    failed = False
    for name, _prompt, domains in SCENARIOS:
        packs = mounted_protocol_packs(domains)
        block = render_protocol_block(packs)
        protocol_tokens = estimate_tokens([{"role": "system", "content": block}])
        declared = sum(pack.token_budget for pack in packs)
        measured_bodies = {pack.id: _body_tokens(pack.body) for pack in packs}
        within = all(
            measured_bodies[pack.id] <= pack.token_budget for pack in packs
        )
        failed = failed or not within
        report["scenarios"].append(
            {
                "scenario": name,
                "domains": domains,
                "mounted": [f"{pack.protocol or pack.id}@{pack.version}" for pack in packs],
                "declared_token_budget": declared,
                "measured_body_tokens": measured_bodies,
                "rendered_protocol_tokens": protocol_tokens,
                "scaffolding_tokens": protocol_tokens - sum(measured_bodies.values()),
                "within_budget": within,
            }
        )
    report["status"] = "fail" if failed else "pass"
    print(json.dumps(report, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
