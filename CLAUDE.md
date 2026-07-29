# Project operating contract

@.claude/rules/00-core.md
@.claude/rules/01-approval-policy.md
@.claude/rules/02-token-model-policy.md
@.claude/rules/03-python-first.md
@.claude/rules/04-workflow.md
@docs/PROJECT_PROFILE.md
@tasks/CURRENT.md

## Session defaults

- Start from the current task, not the entire backlog.
- Inspect only files necessary for the task; use `rg`, `git diff`, and Python summaries before opening large files.
- Make reversible, minimal changes and continue without approval unless the approval policy explicitly requires it.
- Run focused tests before broad tests.
- Report changed files, tests, remaining risks, and approval-required next actions.
