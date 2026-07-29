"""Canonical race identifier for the P0 data-audit stage.

Natural key per docs/domain/claude_boatrace_prediction_system_implementation_guide.md
(7.1): `race_date + venue_code + race_number`. This module defines that key
as a validated value object plus an in-memory uniqueness check that mirrors
the `UNIQUE(race_date, venue_id, race_number)` constraint the eventual
`races` table will enforce in the database (not created yet at this stage).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

VALID_VENUE_CODES = frozenset(f"{i:02d}" for i in range(1, 25))
MIN_RACE_NUMBER = 1
MAX_RACE_NUMBER = 12


class InvalidRaceKeyError(ValueError):
    """Raised when a race_date/venue_code/race_number combination is invalid."""


@dataclass(frozen=True)
class RaceKey:
    race_date: date
    venue_code: str
    race_number: int

    def __post_init__(self) -> None:
        if not isinstance(self.race_date, date):
            raise InvalidRaceKeyError(f"race_date must be a date: {self.race_date!r}")
        if self.venue_code not in VALID_VENUE_CODES:
            raise InvalidRaceKeyError(f"unknown venue_code: {self.venue_code!r}")
        if not (MIN_RACE_NUMBER <= self.race_number <= MAX_RACE_NUMBER):
            raise InvalidRaceKeyError(
                "race_number out of range "
                f"[{MIN_RACE_NUMBER}, {MAX_RACE_NUMBER}]: {self.race_number!r}"
            )

    @property
    def canonical_id(self) -> str:
        return f"{self.race_date.isoformat()}-{self.venue_code}-{self.race_number:02d}"

    def __str__(self) -> str:
        return self.canonical_id


class RaceKeyRegistry:
    """In-memory uniqueness enforcement for `RaceKey` instances.

    Stands in for the database UNIQUE constraint until a real schema
    exists; the check is on `canonical_id` so it stays valid once that
    constraint is added.
    """

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def register(self, key: RaceKey) -> None:
        if key.canonical_id in self._seen:
            raise InvalidRaceKeyError(f"duplicate race key: {key.canonical_id}")
        self._seen.add(key.canonical_id)

    def __contains__(self, key: RaceKey) -> bool:
        return key.canonical_id in self._seen
