"""Odds-deviation detection: predicted vs. actual closing odds.

Compares odds this project *expected* (from a model trained on
pre-race, morning-available features -- see `odds_source.py` for why
only closing odds exist historically) against the odds the market
*actually* settled on, and turns a large gap into abstention reason
codes for `abstention.py`.

**What this signal means, and what it does not.** The market's closing
odds embed information this project's features do not have: late money,
crowd knowledge, on-site observation. When actual odds diverge sharply
from what pre-race features predict, the most defensible reading is
"the market knows something we do not" -- which is a reason to *stand
down*, not a detected mispricing. This module therefore only ever emits
abstention reasons; it never emits a buy signal.

Correspondingly, an expected-value computed from *predicted* odds is
not a market comparison at all: predicted odds and model win
probability would both be functions of the same feature vector, so
their disagreement measures model choice rather than market
inefficiency. Only real observed odds belong in an EV calculation
(`expected_value.py`).

**Metric.** Odds are multiplicative, so deviation is measured as
`log(actual / predicted)`, not a difference: 20.0 vs 10.0 is the same
magnitude of surprise as 2.0 vs 1.0. The sign is kept because the two
directions mean different things -- positive means the market priced
the boat *longer* (less likely) than expected, negative means money
came in on it -- while thresholding uses the absolute value.

**Reason code.** The guide's §15.1 list has `OD_ODDS_SHARP_CHANGE`, but
that describes odds *moving* over time, which needs an odds time series
and therefore cannot be evaluated on historical data at all (only one
closing observation per race is retained). What this module detects is
different -- odds *differing from prediction* at a single point in time
-- so it uses its own code rather than quietly overloading the guide's,
following the precedent set by `abstention.EV_CONSERVATIVE_BELOW_THRESHOLD`.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from .abstention import DQ_MISSING_REQUIRED_DATA, OD_ODDS_UNEXPECTED_VS_MODEL

ODDS_DEVIATION_VERSION = "odds_deviation_v1"

__all__ = [
    "ODDS_DEVIATION_VERSION",
    # re-exported from abstention.py, which owns all reason codes, so
    # callers of this module need not import from two places
    "OD_ODDS_UNEXPECTED_VS_MODEL",
    "LaneOddsDeviation",
    "OddsDeviationError",
    "RaceOddsDeviation",
    "compute_race_deviation",
    "deviation_abstention_reasons",
]


class OddsDeviationError(ValueError):
    """Raised for invalid odds-deviation input."""


@dataclass(frozen=True)
class LaneOddsDeviation:
    lane_number: int
    predicted_odds: float | None
    actual_odds: float | None
    log_ratio: float | None
    """`log(actual / predicted)`. Positive: the market priced this boat
    longer (less likely to win) than predicted. Negative: money came in
    on it. `None` when either side is missing."""

    @property
    def abs_log_ratio(self) -> float | None:
        return None if self.log_ratio is None else abs(self.log_ratio)


@dataclass(frozen=True)
class RaceOddsDeviation:
    lanes: tuple[LaneOddsDeviation, ...]
    max_abs_log_ratio: float | None
    mean_abs_log_ratio: float | None
    missing_lanes: tuple[int, ...]
    """Lanes where predicted or actual odds were absent, so no deviation
    could be computed. Reported explicitly rather than dropped, so a
    caller can tell "no surprise" apart from "could not tell"."""
    version: str = ODDS_DEVIATION_VERSION

    def for_lane(self, lane_number: int) -> LaneOddsDeviation | None:
        for lane in self.lanes:
            if lane.lane_number == lane_number:
                return lane
        return None

    def to_dict(self) -> dict:
        return {
            "lanes": [
                {
                    "lane_number": lane.lane_number,
                    "predicted_odds": lane.predicted_odds,
                    "actual_odds": lane.actual_odds,
                    "log_ratio": lane.log_ratio,
                }
                for lane in self.lanes
            ],
            "max_abs_log_ratio": self.max_abs_log_ratio,
            "mean_abs_log_ratio": self.mean_abs_log_ratio,
            "missing_lanes": list(self.missing_lanes),
            "version": self.version,
        }


def _validate_odds(value: float | None, label: str, lane_number: int) -> float | None:
    if value is None:
        return None
    if value <= 0:
        raise OddsDeviationError(
            f"{label} odds for lane {lane_number} must be positive, got {value!r}"
        )
    return float(value)


def compute_race_deviation(
    predicted_odds: Mapping[int, float | None],
    actual_odds: Mapping[int, float | None],
) -> RaceOddsDeviation:
    """Compare predicted against actual odds for every lane mentioned by
    either side. A lane missing from one side yields a `LaneOddsDeviation`
    with `log_ratio=None` and is listed in `missing_lanes` — never
    silently dropped, and never treated as zero deviation."""
    lane_numbers = sorted(set(predicted_odds) | set(actual_odds))
    if not lane_numbers:
        raise OddsDeviationError("no lanes supplied in either predicted or actual odds")

    lanes: list[LaneOddsDeviation] = []
    missing: list[int] = []
    ratios: list[float] = []

    for lane_number in lane_numbers:
        if not 1 <= lane_number <= 6:
            raise OddsDeviationError(f"lane_number out of range 1-6: {lane_number!r}")

        predicted = _validate_odds(predicted_odds.get(lane_number), "predicted", lane_number)
        actual = _validate_odds(actual_odds.get(lane_number), "actual", lane_number)

        if predicted is None or actual is None:
            missing.append(lane_number)
            log_ratio = None
        else:
            log_ratio = math.log(actual / predicted)
            ratios.append(abs(log_ratio))

        lanes.append(
            LaneOddsDeviation(
                lane_number=lane_number,
                predicted_odds=predicted,
                actual_odds=actual,
                log_ratio=log_ratio,
            )
        )

    return RaceOddsDeviation(
        lanes=tuple(lanes),
        max_abs_log_ratio=max(ratios) if ratios else None,
        mean_abs_log_ratio=(sum(ratios) / len(ratios)) if ratios else None,
        missing_lanes=tuple(missing),
    )


def deviation_abstention_reasons(
    deviation: RaceOddsDeviation,
    *,
    max_abs_log_ratio: float,
    target_lane: int | None = None,
) -> tuple[str, ...]:
    """Reason codes contributed by odds deviation, for merging into an
    `abstention.evaluate_abstention` result.

    `max_abs_log_ratio` is supplied by the caller (e.g. from config),
    never hardcoded here, matching `abstention.AbstentionThresholds`.

    When `target_lane` is given, only that lane's deviation is judged —
    the intended bet is what matters, and one wild outsider elsewhere in
    the field should not veto the race. Otherwise the whole field is
    judged via `max_abs_log_ratio`. A deviation that could not be
    computed abstains (`DQ_MISSING_REQUIRED_DATA`) rather than passing,
    matching `abstention.py`'s treatment of missing input.
    """
    if max_abs_log_ratio < 0:
        raise OddsDeviationError(
            f"max_abs_log_ratio must be non-negative, got {max_abs_log_ratio!r}"
        )

    reasons: list[str] = []

    if target_lane is not None:
        lane = deviation.for_lane(target_lane)
        if lane is None or lane.abs_log_ratio is None:
            reasons.append(DQ_MISSING_REQUIRED_DATA)
        elif lane.abs_log_ratio > max_abs_log_ratio:
            reasons.append(OD_ODDS_UNEXPECTED_VS_MODEL)
    else:
        if deviation.max_abs_log_ratio is None:
            reasons.append(DQ_MISSING_REQUIRED_DATA)
        elif deviation.max_abs_log_ratio > max_abs_log_ratio:
            reasons.append(OD_ODDS_UNEXPECTED_VS_MODEL)

    return tuple(dict.fromkeys(reasons))
