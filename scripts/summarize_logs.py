from __future__ import annotations

import argparse
import re
from collections import Counter
from pathlib import Path

PATTERN = re.compile(r"(error|exception|failed|failure|traceback|warning|critical)", re.I)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--context", type=int, default=2)
    parser.add_argument("--limit", type=int, default=80)
    args = parser.parse_args()
    lines = args.path.read_text(encoding="utf-8", errors="replace").splitlines()
    indexes = [i for i, line in enumerate(lines) if PATTERN.search(line)]
    selected: set[int] = set()
    for i in indexes:
        selected.update(range(max(0, i - args.context), min(len(lines), i + args.context + 1)))
    print(f"lines={len(lines)} matched={len(indexes)}")
    levels = Counter(m.group(1).lower() for line in lines for m in [PATTERN.search(line)] if m)
    print("keywords=" + ", ".join(f"{k}:{v}" for k, v in levels.most_common()))
    for i in sorted(selected)[: args.limit]:
        print(f"{i + 1}: {lines[i]}")
    if len(selected) > args.limit:
        print(f"... truncated {len(selected) - args.limit} selected lines")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
