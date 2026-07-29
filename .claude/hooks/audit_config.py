from __future__ import annotations

import json
from datetime import datetime, timezone
from common import project_root, read_input


def main() -> int:
    payload = read_input()
    root = project_root(payload)
    state = root / ".claude" / "state"
    state.mkdir(parents=True, exist_ok=True)
    record = {
        "at": datetime.now(timezone.utc).isoformat(),
        "source": payload.get("source"),
        "file_path": payload.get("file_path"),
        "session_id": payload.get("session_id"),
    }
    with (state / "config-audit.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
