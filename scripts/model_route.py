from __future__ import annotations

import argparse

HAIKU = {"search", "map", "format", "extract", "rename", "summarize", "bookkeeping", "mechanical"}
OPUS = {"architecture", "irreversible", "security-critical", "cross-system", "major-incident"}


def route(task: str, risk: str) -> str:
    words = set(task.lower().replace("_", "-").split())
    if risk == "high" and words.intersection(OPUS):
        return "opus"
    if risk == "low" and words.intersection(HAIKU):
        return "haiku"
    return "sonnet"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("task")
    parser.add_argument("--risk", choices=["low", "medium", "high"], default="medium")
    args = parser.parse_args()
    print(route(args.task, args.risk))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
