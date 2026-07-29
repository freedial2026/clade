from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

STATUS = re.compile(r"^- Status:\s*(.+)$", re.M)


def main() -> int:
    counter: Counter[str] = Counter()
    rows: list[tuple[str, str]] = []
    for path in sorted(Path("tasks").glob("P*-T*.md")):
        text = path.read_text(encoding="utf-8")
        match = STATUS.search(text)
        status = match.group(1).strip() if match else "unknown"
        counter[status] += 1
        rows.append((path.stem, status))
    for task, status in rows:
        print(f"{task}\t{status}")
    print("\nSummary: " + ", ".join(f"{k}={v}" for k, v in sorted(counter.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
