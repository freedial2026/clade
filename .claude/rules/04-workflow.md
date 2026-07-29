# Standard workflow

1. Read `tasks/CURRENT.md` and the named task file.
2. Establish baseline: `git status`, relevant tests, current behavior.
3. Inspect the smallest relevant code surface.
4. Write a concise implementation plan when more than one file or risk area is involved.
5. Implement the minimal coherent change.
6. Run focused tests, then broader checks only when justified.
7. Review the diff for scope, secrets, generated noise, compatibility, and rollback.
8. Update task status, decisions, and handoff.
9. Report changed files, tests, unresolved risks, and approval-required actions.

Never mark a task complete when required tests fail, acceptance criteria are unverified, or the result cannot be reproduced.
