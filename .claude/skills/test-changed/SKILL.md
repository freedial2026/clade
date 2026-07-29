---
name: test-changed
description: Run the smallest reliable validation set for the current changes and expand only when justified.
disable-model-invocation: true
---

# test-changed


- Identify changed components.
- Run syntax/static checks and focused tests.
- Expand to integration/full suite only for cross-cutting changes or before release.
- Record exact commands, pass/fail counts, skipped tests, and environment limitations.

