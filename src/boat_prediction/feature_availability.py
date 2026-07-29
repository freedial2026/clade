"""Feature availability tests and gate (P1-T003).

Every feature used as a model input must prove it was available at
prediction time (docs/PROJECT_PROFILE.md: `available_at <= prediction_at`
for every feature — enforced here via `temporal.py`'s
`is_available_for_prediction` rather than re-implemented). Features
derived from a race's own result additionally must never be used before
that race is finalized
(docs/domain/claude_boatrace_prediction_system_implementation_guide.md
§10.3: "結果確定前に結果由来特徴量を参照しない").
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .temporal import TemporalRecord, is_available_for_prediction

# Feature Set V1 in-race base features (docs/domain/.../implementation_guide.md
# §11.1) — all sourced from pre-race listed data, never from the race's own
# result, so none of them are result-derived.
FEATURE_SET_V1_NAMES: frozenset[str] = frozenset(
    {
        "lane_number",
        "listed_class",
        "listed_national_win_rate",
        "listed_local_win_rate",
        "listed_average_st",
        "listed_motor_second_rate",
        "listed_boat_second_rate",
    }
)


class FeatureAvailabilityError(ValueError):
    """Raised when a feature fails the availability/leakage gate."""


@dataclass(frozen=True)
class FeatureLineage:
    """Documents where one feature value came from and when it became usable."""

    feature_name: str
    source: str
    temporal: TemporalRecord
    is_result_derived: bool = False


def check_feature_availability(
    lineage: FeatureLineage,
    prediction_at: datetime,
    *,
    race_finalized_at: datetime | None = None,
) -> None:
    """Raise FeatureAvailabilityError if `lineage` may not be used for a
    prediction made at `prediction_at`."""
    if not is_available_for_prediction(lineage.temporal, prediction_at):
        raise FeatureAvailabilityError(
            f"{lineage.feature_name} not available until "
            f"{lineage.temporal.available_at.isoformat()}, "
            f"prediction_at={prediction_at.isoformat()}"
        )

    if lineage.is_result_derived and (
        race_finalized_at is None or race_finalized_at > prediction_at
    ):
        raise FeatureAvailabilityError(
            f"{lineage.feature_name} is result-derived and the race is not "
            f"finalized by prediction_at={prediction_at.isoformat()}"
        )


def check_feature_set_availability(
    lineages: list[FeatureLineage],
    prediction_at: datetime,
    *,
    race_finalized_at: datetime | None = None,
) -> None:
    """Check every feature in a feature set. Raises on the first
    violation found, in input order (deterministic)."""
    for lineage in lineages:
        check_feature_availability(lineage, prediction_at, race_finalized_at=race_finalized_at)
