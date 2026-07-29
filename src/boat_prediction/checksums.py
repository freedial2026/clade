"""Shared file-checksum helper.

Factored out because `inventory.py`, `ingest.py`, and `model_registry.py`
each hashed files independently with an identical SHA-256/chunked-read
implementation.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

_CHUNK_SIZE = 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()
