from __future__ import annotations

import argparse
from pathlib import Path

SKIP = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build", "data", "artifacts"}


def walk(root: Path, max_depth: int) -> list[str]:
    lines: list[str] = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(part in SKIP for part in rel.parts):
            continue
        depth = len(rel.parts)
        if depth > max_depth:
            continue
        suffix = "/" if path.is_dir() else ""
        size = f" ({path.stat().st_size} B)" if path.is_file() else ""
        lines.append("  " * (depth - 1) + rel.name + suffix + size)
    return lines


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--max-depth", type=int, default=3)
    args = parser.parse_args()
    print("\n".join(walk(args.root.resolve(), args.max_depth)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
