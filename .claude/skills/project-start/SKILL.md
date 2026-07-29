---
name: project-start
description: Initialize work in this repository with minimal context and select the next valid task.
disable-model-invocation: true
---

# project-start


1. Read `CLAUDE.md`, `docs/PROJECT_PROFILE.md`, and `tasks/CURRENT.md`.
2. Run `python scripts/repo_map.py --max-depth 3` and `git status --short`.
3. If no task is active, select the highest-priority unblocked task from `tasks/BACKLOG.md` and create its task file from `.claude/templates/task.md`.
4. Establish baseline commands and risks.
5. Do not implement more than the selected task.

