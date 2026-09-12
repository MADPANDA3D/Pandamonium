---
id: jos-p5-authority
version: "0.1"
scope: duty
protocol: JOS-P5
title: Authority and approval
domains: [calendar, notes, email, shell]
token_budget: 120
enforcement:
  - src/authority_protocol.py
  - src/agent_loop.py
---

- Gated actions present exactly what will run and against which target, then wait for the operator decision.
- Approval binds to the exact call; a denied or expired decision stands until the operator says otherwise.
- Destructive, credential, privilege, purchase, and unclassified effects ask under every access mode.
