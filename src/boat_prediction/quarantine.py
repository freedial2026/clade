"""Quarantine and replay for invalid records (P0-T008).

Persists records rejected by `validation.py`/`integrity.py` into a JSON
ledger (`data/rejected/quarantine_ledger.json` by default) instead of
discarding them, per
docs/domain/claude_boatrace_prediction_system_implementation_guide.md
§9.3 ("不正行は捨てず、data/rejected/とingestion_errorsへ保存する").

A quarantined entry can later be replayed with a corrected version of the
record: if the corrected version now passes the caller-supplied
`revalidate` check, the entry is marked resolved and the corrected
record is returned. The original failure (record, code, message,
quarantined_at) is never overwritten — only a `corrected_record` and
`replayed_at` are added — so the audit trail stays intact. Replaying an
already-resolved entry again is a no-op: it returns the same resolved
record instead of creating a second accepted copy.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .json_store import read_json_or_default, write_json

STATUS_QUARANTINED = "quarantined"
STATUS_REPLAYED = "replayed"


class QuarantineError(ValueError):
    """Raised when a quarantine/replay operation cannot proceed safely."""


@dataclass(frozen=True)
class QuarantineRecord:
    quarantine_id: str
    original_record: dict
    code: str
    message: str
    quarantined_at: str
    status: str
    corrected_record: dict | None = None
    replayed_at: str | None = None


def _quarantine_id(record: dict, code: str) -> str:
    payload = json.dumps({"record": record, "code": code}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class QuarantineStore:
    def __init__(self, ledger_path: Path) -> None:
        self._ledger_path = ledger_path
        self._entries: dict[str, dict] = self._load()

    def _load(self) -> dict[str, dict]:
        data = read_json_or_default(self._ledger_path, {}, error_type=QuarantineError)
        return dict(data.get("entries", {}))

    def _save(self) -> None:
        write_json(self._ledger_path, {"entries": self._entries})

    def add(
        self, record: dict, code: str, message: str, *, now: datetime | None = None
    ) -> QuarantineRecord:
        """Quarantine a record. Re-adding the same (record, code) is a
        no-op: it returns the existing entry rather than duplicating it."""
        quarantine_id = _quarantine_id(record, code)
        existing = self._entries.get(quarantine_id)
        if existing is not None:
            return QuarantineRecord(**existing)

        entry = QuarantineRecord(
            quarantine_id=quarantine_id,
            original_record=record,
            code=code,
            message=message,
            quarantined_at=(now or datetime.now(UTC)).isoformat(),
            status=STATUS_QUARANTINED,
        )
        self._entries[quarantine_id] = asdict(entry)
        self._save()
        return entry

    def get(self, quarantine_id: str) -> QuarantineRecord:
        entry = self._entries.get(quarantine_id)
        if entry is None:
            raise QuarantineError(f"unknown quarantine_id: {quarantine_id}")
        return QuarantineRecord(**entry)

    def list_pending(self) -> list[QuarantineRecord]:
        return [
            QuarantineRecord(**entry)
            for entry in self._entries.values()
            if entry["status"] == STATUS_QUARANTINED
        ]

    def replay(
        self,
        quarantine_id: str,
        corrected_record: dict[str, Any],
        revalidate: Callable[[dict[str, Any]], bool],
        *,
        now: datetime | None = None,
    ) -> QuarantineRecord:
        """Apply a corrected record to a quarantined entry. Raises if the
        corrected record still fails `revalidate`. Replaying an
        already-replayed entry returns the existing resolved record
        unchanged (idempotent; does not duplicate accepted data)."""
        entry = self.get(quarantine_id)
        if entry.status == STATUS_REPLAYED:
            return entry

        if not revalidate(corrected_record):
            raise QuarantineError(
                f"corrected record still fails validation: {quarantine_id}"
            )

        resolved = QuarantineRecord(
            quarantine_id=entry.quarantine_id,
            original_record=entry.original_record,
            code=entry.code,
            message=entry.message,
            quarantined_at=entry.quarantined_at,
            status=STATUS_REPLAYED,
            corrected_record=corrected_record,
            replayed_at=(now or datetime.now(UTC)).isoformat(),
        )
        self._entries[quarantine_id] = asdict(resolved)
        self._save()
        return resolved
