from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def read_input() -> dict[str, Any]:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def decision(value: str, reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": value,
            "permissionDecisionReason": reason,
        }
    }, ensure_ascii=False))


def project_root(payload: dict[str, Any]) -> Path:
    cwd = payload.get("cwd")
    return Path(cwd).resolve() if isinstance(cwd, str) and cwd else Path.cwd().resolve()
