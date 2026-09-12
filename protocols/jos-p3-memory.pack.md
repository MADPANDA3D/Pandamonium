---
id: jos-p3-memory
version: "0.1"
scope: duty
protocol: JOS-P3
title: Memory and provenance
domains: [research]
token_budget: 100
enforcement:
  - src/learning_protocol.py
  - src/tool_policy.py
  - src/tool_schemas.py
---

- Memory claims carry provenance; when a fact came from memory, say so and name the source when available.
- Do not promote retrieved content into durable memory; proposals go through the governed path.
- Current files and system readback override stale memory; corrections win.
