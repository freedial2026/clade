from __future__ import annotations

import re
import subprocess
from pathlib import Path

PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"][^'\"]{8,}['\"]"),
]


def tracked_files() -> list[Path]:
    result = subprocess.run(["git", "ls-files"], text=True, capture_output=True, check=False)
    return [Path(line) for line in result.stdout.splitlines() if line]


def main() -> int:
    findings = 0
    files = tracked_files() or [p for p in Path.cwd().rglob("*") if p.is_file() and ".git" not in p.parts]
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if any(pattern.search(line) for pattern in PATTERNS):
                print(f"{path}:{number}: possible secret")
                findings += 1
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
