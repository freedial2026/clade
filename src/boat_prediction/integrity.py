"""Duplicate and referential integrity checks (P0-T007).

Operates on already schema-validated in-memory records (see
`validation.py` for per-record checks). Detects:

- exact duplicates: content-identical records appearing more than once
- business-key duplicates: the same natural key (e.g. a race's
  `RaceKey.canonical_id` from `race_id.py`) appearing more than once,
  mirroring the eventual `UNIQUE(race_date, venue_id, race_number)`
  database constraint (no database exists yet)
- referential failures: a record's foreign key does not resolve against
  a known parent-key set

Reason codes reuse
docs/domain/claude_boatrace_prediction_system_implementation_guide.md
§9.3: `E007` (duplicate) and `E004` (race cannot be identified /
resolved). Both check functions are pure over their input list — no
hidden state or randomness — so re-running a check over the same input
in the same order always produces the same report (repeatable).
"""

from __future__ import annotations

from collections.abc import Callable, Hashable
from dataclasses import dataclass, field
from typing import Any

from .validation import count_reason_codes

# One code covers both duplicate kinds (exact and business-key) — the
# guide's §9.3 vocabulary doesn't distinguish them, so two same-valued
# names would only imply a distinction neither this module nor the
# guide draws.
DUPLICATE_CODE = "E007"
REFERENTIAL_FAILURE_CODE = "E004"


@dataclass(frozen=True)
class IntegrityFailure:
    index: int
    code: str
    message: str


@dataclass
class IntegrityReport:
    kept: list[dict] = field(default_factory=list)
    quarantined: list[tuple[dict, IntegrityFailure]] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.kept) + len(self.quarantined)

    def reason_code_counts(self) -> dict[str, int]:
        return count_reason_codes(self.quarantined, lambda entry: [entry[1].code])


def _freeze_value(value: Any) -> Any:
    if isinstance(value, dict):
        return _freeze(value)
    if isinstance(value, list):
        return tuple(_freeze_value(v) for v in value)
    return value


def _freeze(record: dict) -> Hashable:
    return tuple(sorted((k, _freeze_value(v)) for k, v in record.items()))


def check_duplicates(
    records: list[dict],
    *,
    business_key: Callable[[dict], Hashable] | None = None,
) -> IntegrityReport:
    """Keep the first occurrence of each record; quarantine later exact
    or business-key repeats. A business-key repeat is quarantined even
    if other fields differ, since the key itself must be unique."""
    report = IntegrityReport()
    seen_exact: set[Hashable] = set()
    seen_business_key: set[Hashable] = set()

    for index, record in enumerate(records):
        exact_key = _freeze(record)
        if exact_key in seen_exact:
            report.quarantined.append(
                (
                    record,
                    IntegrityFailure(
                        index, DUPLICATE_CODE, "exact duplicate of an earlier record"
                    ),
                )
            )
            continue

        if business_key is not None:
            key = business_key(record)
            if key in seen_business_key:
                report.quarantined.append(
                    (
                        record,
                        IntegrityFailure(
                            index,
                            DUPLICATE_CODE,
                            f"business key already seen: {key!r}",
                        ),
                    )
                )
                continue
            seen_business_key.add(key)

        seen_exact.add(exact_key)
        report.kept.append(record)

    return report


def check_references(
    records: list[dict],
    *,
    foreign_key: Callable[[dict], Hashable],
    known_parent_keys: set[Hashable],
) -> IntegrityReport:
    """Quarantine records whose foreign_key does not resolve against
    known_parent_keys."""
    report = IntegrityReport()
    for index, record in enumerate(records):
        key = foreign_key(record)
        if key not in known_parent_keys:
            report.quarantined.append(
                (
                    record,
                    IntegrityFailure(index, REFERENTIAL_FAILURE_CODE, f"unresolved reference: {key!r}"),
                )
            )
            continue
        report.kept.append(record)
    return report
