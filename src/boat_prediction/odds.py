"""Timestamped odds snapshots (P2-T001).

Stores odds with a source and an exact availability timestamp
(`TemporalRecord`, reused from `temporal.py`), and distinguishes two
kinds of query:

- prediction-time odds: the latest snapshot available by a given
  `prediction_at` — never a snapshot published after it, since this
  reuses `is_available_for_prediction` so no future odds can leak into
  a historical decision.
- closing odds: the snapshot explicitly marked `is_closing=True` for
  that race/lane (the final odds at post time), independent of any
  particular `prediction_at`.

Both lookups return an explicit `OddsQueryResult` (`found`/`snapshot`/
`reason`) rather than a plain `None`, so "no snapshot found" is a
distinguishable outcome a caller must check, not something that could be
silently mistaken for a zero value.
"""

from __future__ import annotations

from collections.abc import Hashable
from dataclasses import dataclass
from datetime import datetime

from .temporal import TemporalRecord, is_available_for_prediction


class OddsError(ValueError):
    """Raised for invalid odds snapshot input or an ambiguous query."""


@dataclass(frozen=True)
class OddsSnapshot:
    race_key: Hashable
    lane_number: int
    odds: float
    source: str
    temporal: TemporalRecord
    is_closing: bool = False

    def __post_init__(self) -> None:
        if self.odds <= 0:
            raise OddsError(f"odds must be positive: {self.odds!r}")
        if not (1 <= self.lane_number <= 6):
            raise OddsError(f"lane_number out of range 1-6: {self.lane_number!r}")


@dataclass(frozen=True)
class OddsQueryResult:
    found: bool
    snapshot: OddsSnapshot | None
    reason: str | None = None


def _missing(reason: str) -> OddsQueryResult:
    return OddsQueryResult(found=False, snapshot=None, reason=reason)


def get_prediction_time_odds(
    snapshots: list[OddsSnapshot],
    race_key: Hashable,
    lane_number: int,
    prediction_at: datetime,
) -> OddsQueryResult:
    """Latest snapshot for (race_key, lane_number) available by
    prediction_at. Never selects a snapshot whose available_at is after
    prediction_at."""
    candidates = [
        s
        for s in snapshots
        if s.race_key == race_key
        and s.lane_number == lane_number
        and is_available_for_prediction(s.temporal, prediction_at)
    ]
    if not candidates:
        return _missing(
            f"no odds snapshot available for race_key={race_key!r} "
            f"lane_number={lane_number} by prediction_at={prediction_at.isoformat()}"
        )
    latest = max(candidates, key=lambda s: s.temporal.available_at)
    return OddsQueryResult(found=True, snapshot=latest)


def get_closing_odds(
    snapshots: list[OddsSnapshot], race_key: Hashable, lane_number: int
) -> OddsQueryResult:
    """The snapshot explicitly marked as closing odds for this race/lane."""
    candidates = [
        s
        for s in snapshots
        if s.race_key == race_key and s.lane_number == lane_number and s.is_closing
    ]
    if not candidates:
        return _missing(
            f"no closing odds snapshot for race_key={race_key!r} lane_number={lane_number}"
        )
    if len(candidates) > 1:
        raise OddsError(
            f"multiple closing odds snapshots for race_key={race_key!r} "
            f"lane_number={lane_number} — exactly one is expected"
        )
    return OddsQueryResult(found=True, snapshot=candidates[0])
