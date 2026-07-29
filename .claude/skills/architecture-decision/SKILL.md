---
name: architecture-decision
description: Evaluate a material architecture decision and create an ADR.
disable-model-invocation: true
---

# architecture-decision


Decision: `$ARGUMENTS`

- Gather constraints with Sonnet-level analysis first.
- Use `architect` only when the decision is cross-system, irreversible, or high-risk.
- Compare at least two viable options, including operational cost and rollback.
- Write an ADR; do not silently implement the decision.

