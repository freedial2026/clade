"""Temporal timestamp semantics for the P0 data-audit stage.

See docs/temporal-model.md for the meaning of each field. All timestamps
are stored as timezone-aware UTC `datetime` objects; naive datetimes are
rejected so "UTC storage" is not left implicit. `for_display()` is the
only place a stored value is converted to another timezone.

The core guarantee this module exists to enforce (docs/PROJECT_PROFILE.md,
docs/domain/.../implementation_guide.md §8.1):

    available_at <= prediction_at

for every record used in a prediction query — i.e. no information that
was not yet available at prediction time may leak into it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo


class TemporalError(ValueError):
    """Raised when timestamps are naive or logically inconsistent."""


def _require_utc_aware(name: str, value: datetime) -> None:
    if value.tzinfo is None:
        raise TemporalError(f"{name} must be timezone-aware (UTC), got naive datetime")
    if value.utcoffset() != UTC.utcoffset(None):
        raise TemporalError(f"{name} must be stored in UTC, got offset {value.utcoffset()}")


def to_utc(value: datetime) -> datetime:
    """Convert any aware datetime to UTC for storage. Rejects naive input."""
    if value.tzinfo is None:
        raise TemporalError("cannot convert a naive datetime to UTC")
    return value.astimezone(UTC)


def for_display(value: datetime, tz: ZoneInfo) -> datetime:
    """Convert a UTC-stored timestamp to a display timezone. Storage stays UTC."""
    _require_utc_aware("value", value)
    return value.astimezone(tz)


@dataclass(frozen=True)
class TemporalRecord:
    """One record's full time provenance.

    - event_time: when the real-world event happened.
    - published_at: when the source provider published it.
    - collected_at: when this system acquired it.
    - available_at: when it became usable for prediction queries.
    - valid_from: start of the period this value is considered current.
    - valid_to: end of that period, or None if still current.
    """

    event_time: datetime
    published_at: datetime
    collected_at: datetime
    available_at: datetime
    valid_from: datetime
    valid_to: datetime | None = None

    def __post_init__(self) -> None:
        for name in (
            "event_time",
            "published_at",
            "collected_at",
            "available_at",
            "valid_from",
        ):
            _require_utc_aware(name, getattr(self, name))
        if self.valid_to is not None:
            _require_utc_aware("valid_to", self.valid_to)

        if not (self.event_time <= self.published_at <= self.collected_at <= self.available_at):
            raise TemporalError(
                "timestamps must satisfy "
                "event_time <= published_at <= collected_at <= available_at"
            )
        if self.valid_to is not None and self.valid_from > self.valid_to:
            raise TemporalError("valid_from must be <= valid_to")


def is_available_for_prediction(record: TemporalRecord, prediction_at: datetime) -> bool:
    """The guide's leakage-prevention predicate: available_at <= prediction_at."""
    _require_utc_aware("prediction_at", prediction_at)
    return record.available_at <= prediction_at


def is_valid_at(record: TemporalRecord, as_of: datetime) -> bool:
    """Whether the record's valid_from/valid_to window covers `as_of`."""
    _require_utc_aware("as_of", as_of)
    if as_of < record.valid_from:
        return False
    return record.valid_to is None or as_of < record.valid_to


def filter_available(
    records: list[TemporalRecord], prediction_at: datetime
) -> list[TemporalRecord]:
    """Point-in-time query: only records available by `prediction_at`."""
    return [r for r in records if is_available_for_prediction(r, prediction_at)]
