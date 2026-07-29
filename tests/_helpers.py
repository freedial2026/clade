"""Shared test-only fixture helpers.

Factored out because test_temporal.py, test_odds.py, and
test_feature_availability.py each independently defined an identical
`_utc(*args)` shortcut, and test_odds.py/test_feature_availability.py
each independently built a `TemporalRecord` from a single `available_at`
using the same fixed-offset pattern (event/published/collected 30/20/10
minutes earlier, valid_from a day earlier). Not a production module —
tests only.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from boat_prediction.temporal import TemporalRecord


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def make_temporal_record(available_at: datetime, **overrides: datetime | None) -> TemporalRecord:
    """A valid `TemporalRecord` available at `available_at`, with
    event/published/collected times 30/20/10 minutes earlier and a
    `valid_from` a day earlier. Override any field via keyword."""
    defaults = dict(
        event_time=available_at - timedelta(minutes=30),
        published_at=available_at - timedelta(minutes=20),
        collected_at=available_at - timedelta(minutes=10),
        available_at=available_at,
        valid_from=available_at - timedelta(days=1),
        valid_to=None,
    )
    defaults.update(overrides)
    return TemporalRecord(**defaults)
