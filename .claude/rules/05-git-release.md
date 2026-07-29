# Git and release rules

- Do not commit unrelated changes.
- Never rewrite shared history without explicit approval.
- Commits should represent one coherent purpose and reference the task ID.
- `git push`, merge, tag, release, and public PR actions require approval.
- Before requesting approval, provide `git status`, summary diff, test results, release notes, and rollback.
- Generated files must be reproducible and intentionally included.
- Never commit secrets, production data, local settings, or model binaries unless explicitly approved and safely handled.
