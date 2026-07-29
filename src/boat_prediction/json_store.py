"""Shared JSON ledger/manifest file I/O.

Factored out because `inventory.py`, `ingest.py`, `quarantine.py`, and
`model_registry.py` each independently reimplemented "read this JSON
file or return a default if it doesn't exist yet (raising a clear error
on genuinely invalid JSON)" and "mkdir -p, then write deterministic
indented JSON." Each module keeps its own on-disk shape (a sorted list
vs. a keyed dict, one top-level key vs. two) — only the file I/O
boilerplate is shared here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_json_or_default(path: Path, default: Any, *, error_type: type[Exception]) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise error_type(f"{path.name} is not valid JSON: {path}") from exc


def write_json(path: Path, payload: Any, *, sort_keys: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=sort_keys) + "\n",
        encoding="utf-8",
    )
