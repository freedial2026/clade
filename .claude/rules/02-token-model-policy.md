# Token and model policy

## Model routing

- **Haiku**: repository mapping, searches, formatting, file classification, small mechanical edits, test-log reduction, task bookkeeping.
- **Sonnet**: ordinary implementation, debugging, tests, code review, API and database work.
- **Opus**: architecture, multi-system tradeoffs, difficult root-cause analysis, security-critical design, irreversible migration strategy. Use only after lower tiers are insufficient or the risk justifies it.

Subagents declare their default model. Do not escalate because a task is merely large; first split it and use Python to reduce the input.

## Context budget

1. Read `tasks/CURRENT.md`, relevant diff, and targeted files first.
2. Use `python scripts/repo_map.py`, `rg`, line ranges, and summaries instead of opening the repository wholesale.
3. Never paste full generated files, lockfiles, data exports, or long logs into model context.
4. Reduce logs with `scripts/summarize_logs.py`.
5. Preserve decisions in task files and ADRs so later sessions need not rediscover them.
6. After a task, update the compact handoff and start the next task in a fresh context when practical.
