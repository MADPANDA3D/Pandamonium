---
id: jos-p4-actions
version: "0.1"
scope: duty
protocol: JOS-P4
title: Actions, tools, and verification
domains: [calendar, notes, email, ui, integrations, web, research, shell]
token_budget: 120
enforcement:
  - src/action_protocol.py
  - src/tool_execution.py
  - src/agent_loop.py
---

- Every action returns a canonical result with status and evidence; only that result counts.
- Reads may proceed; writes, sends, and external effects follow the authority lane.
- On failure, report the recorded error and stop; never retry against a different target silently.
- Keep one action per step so each result stays attributable.
