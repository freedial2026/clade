"""Baseline multiclass first-place probability models (P1-T001).

Two baselines to compare complex models against later
(docs/PROJECT_PROFILE.md: "Compare against simple baselines before
complex models"):

- `UniformBaseline`: 1/6 for every lane — a sanity floor.
- `LanePriorBaseline`: Laplace-smoothed empirical per-lane win frequency
  from historical race winners. Lane number is the strongest known prior
  in Japanese boat racing (inside lanes have a large structural
  advantage), so this is a meaningfully stronger baseline than uniform.
  Fitting is pure counting over the input list — deterministic, so
  re-fitting on the same data always reproduces the same probabilities.

No real race results exist in this environment yet; tests exercise both
baselines against small, explicitly-synthetic fixtures (see
tasks/P1-T001.md for the caveat this implies).
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .metrics import multiclass_log_loss
from .probability import validate_probability_distribution

LANES: tuple[int, ...] = (1, 2, 3, 4, 5, 6)


class BaselineError(ValueError):
    """Raised for invalid inputs to a baseline model."""


def _validate_probabilities(probs: dict[int, float]) -> None:
    validate_probability_distribution(probs, LANES, error_type=BaselineError)


@dataclass(frozen=True)
class UniformBaseline:
    def predict(self) -> dict[int, float]:
        probs = {lane: 1 / len(LANES) for lane in LANES}
        _validate_probabilities(probs)
        return probs


class LanePriorBaseline:
    def __init__(self, smoothing: float = 1.0) -> None:
        self.smoothing = smoothing
        self._win_counts: dict[int, int] | None = None
        self._total_races = 0

    def fit(self, race_winners: list[int]) -> LanePriorBaseline:
        """race_winners: the winning lane_number (1-6) for each historical race."""
        for lane in race_winners:
            if lane not in LANES:
                raise BaselineError(f"winner lane {lane!r} not in {LANES}")
        self._win_counts = dict(Counter(race_winners))
        self._total_races = len(race_winners)
        return self

    def predict(self) -> dict[int, float]:
        if self._win_counts is None:
            raise BaselineError("call fit() before predict()")
        denominator = self._total_races + self.smoothing * len(LANES)
        probs = {
            lane: (self._win_counts.get(lane, 0) + self.smoothing) / denominator for lane in LANES
        }
        _validate_probabilities(probs)
        return probs


@dataclass(frozen=True)
class BaselineMetrics:
    log_loss: float
    top1_accuracy: float
    n_races: int

    def to_dict(self) -> dict:
        return {
            "log_loss": self.log_loss,
            "top1_accuracy": self.top1_accuracy,
            "n_races": self.n_races,
        }


def evaluate(predicted_probs: dict[int, float], actual_winners: list[int]) -> BaselineMetrics:
    """Evaluate one fixed probability distribution (what a baseline
    predicts identically for every race) against a list of actual
    winning lanes."""
    if not actual_winners:
        raise BaselineError("actual_winners must not be empty")
    _validate_probabilities(predicted_probs)
    for winner in actual_winners:
        if winner not in LANES:
            raise BaselineError(f"winner lane {winner!r} not in {LANES}")

    probs_row = [predicted_probs[lane] for lane in LANES]
    log_loss = multiclass_log_loss(actual_winners, [probs_row] * len(actual_winners), LANES)

    predicted_top1 = max(predicted_probs, key=predicted_probs.get)
    correct = sum(1 for winner in actual_winners if winner == predicted_top1)

    return BaselineMetrics(
        log_loss=log_loss,
        top1_accuracy=correct / len(actual_winners),
        n_races=len(actual_winners),
    )


def write_metrics_report(metrics: BaselineMetrics, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metrics.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
