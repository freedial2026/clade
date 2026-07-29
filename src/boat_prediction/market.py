"""Market probability normalization (P2-T002).

Converts pari-mutuel odds into a normalized probability distribution per
race, removing the overround (raw inverse odds sum to more than 1
because the payout structure favors the operator), per
docs/domain/claude_boatrace_prediction_system_implementation_guide.md:

    raw_probability_i = 1 / odds_i
    market_probability_i = raw_probability_i / sum(raw_probability)

Worked example (6 lanes): odds {1: 2.0, 2: 3.0, 3: 4.0, 4: 5.0, 5: 6.0,
6: 10.0} -> raw probabilities sum to 1.55 (the overround) -> normalized
probabilities {1: 0.32258, 2: 0.21505, 3: 0.16129, 4: 0.12903,
5: 0.10753, 6: 0.06452}, which sum to 1.0.
"""

from __future__ import annotations

from collections.abc import Mapping


class MarketError(ValueError):
    """Raised for invalid odds input to market normalization."""


def _raw_probabilities(odds_by_lane: Mapping[int, float]) -> dict[int, float]:
    if not odds_by_lane:
        raise MarketError("odds_by_lane must not be empty")

    raw: dict[int, float] = {}
    for lane, odds in odds_by_lane.items():
        if odds <= 0:
            raise MarketError(f"odds for lane {lane!r} must be positive, got {odds!r}")
        raw[lane] = 1 / odds
    return raw


def overround(odds_by_lane: Mapping[int, float]) -> float:
    """Sum of raw inverse odds before normalization — the bookmaker's
    margin (> 1.0 for a normal market)."""
    return sum(_raw_probabilities(odds_by_lane).values())


def normalize_market_odds(odds_by_lane: Mapping[int, float]) -> dict[int, float]:
    """Normalize odds into a probability distribution that sums to 1.0."""
    raw = _raw_probabilities(odds_by_lane)
    total = sum(raw.values())
    return {lane: p / total for lane, p in raw.items()}
