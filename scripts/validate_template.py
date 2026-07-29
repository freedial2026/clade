from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REQUIRED = [
    "CLAUDE.md",
    ".claude/settings.json",
    ".claude/rules/00-core.md",
    ".claude/agents/implementer.md",
    ".claude/skills/project-start/SKILL.md",
    "docs/PROJECT_PROFILE.md",
    "tasks/CURRENT.md",
]


def main() -> int:
    root = Path.cwd()
    errors: list[str] = []
    for rel in REQUIRED:
        if not (root / rel).is_file():
            errors.append(f"missing: {rel}")

    try:
        settings = json.loads((root / ".claude/settings.json").read_text(encoding="utf-8"))
        if "permissions" not in settings:
            errors.append("settings: permissions missing")
        if "hooks" not in settings:
            errors.append("settings: hooks missing")
    except Exception as exc:
        errors.append(f"settings invalid: {exc}")

    for path in (root / ".claude/agents").glob("*.md"):
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---\n") or not re.search(r"^model: (haiku|sonnet|opus|inherit)$", text, re.M):
            errors.append(f"agent frontmatter invalid: {path}")

    for path in (root / ".claude/skills").glob("*/SKILL.md"):
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---\n") or "description:" not in text:
            errors.append(f"skill frontmatter invalid: {path}")

    if errors:
        print("FAILED")
        for error in errors:
            print(f"- {error}")
        return 1
    print("OK: Claude Code template structure is valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
