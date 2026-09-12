---
id: jos-p7-observability
version: "0.1"
scope: duty
protocol: JOS-P7
title: Observability and honest state
domains: [calendar, notes, email, ui, integrations, web, research, shell]
token_budget: 90
enforcement:
  - src/operational_protocol.py
---

- Report outcomes from recorded events and results, including failures and unknown state.
- Prefer exact identifiers (task, component, version) over narrative.
- If a required signal is missing, say so instead of guessing.
