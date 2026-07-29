from __future__ import annotations

import subprocess


def run(*args: str) -> str:
    return subprocess.run(args, text=True, capture_output=True, check=False).stdout.strip()


def main() -> int:
    print("# Status")
    print(run("git", "status", "--short") or "clean or not a git repository")
    print("\n# Diff stat")
    print(run("git", "diff", "--stat") or "no unstaged diff")
    print("\n# Staged diff stat")
    print(run("git", "diff", "--cached", "--stat") or "no staged diff")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
