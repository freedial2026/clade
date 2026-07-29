from __future__ import annotations

import argparse
from pathlib import Path

TEMPLATE = Path(".claude/templates/task.md")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("task_id")
    parser.add_argument("title")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    destination = Path("tasks") / f"{args.task_id}.md"
    if destination.exists():
        raise SystemExit(f"Task already exists: {destination}")
    text = TEMPLATE.read_text(encoding="utf-8")
    text = text.replace("TASK-ID: Title", f"{args.task_id}: {args.title}")
    if args.dry_run:
        print(text)
    else:
        destination.write_text(text, encoding="utf-8")
        print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
