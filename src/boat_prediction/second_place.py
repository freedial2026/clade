"""Conditional second-place model (P3-T002).

Estimates `P(second_place = j | first_place = i)` for every boat `j !=
i`. `j == i` is structurally impossible (the same boat cannot finish
both first and second) and is always exactly `0.0`, never estimated
from data. For a fixed first-place winner, the distribution over the
remaining boats must sum to `1.0`. This is a smoothed conditional-
frequency baseline (docs/PROJECT_PROFILE.md: "Compare against simple
baselines before complex models"), not a full trained classifier.

Any evaluation against historical outcomes must reuse P1-T002's
walk-forward folds rather than a random split
(docs/PROJECT_PROFILE.md: "Random train/test split is prohibited") —
`evaluate_with_folds()` is the only scoring entry point this module
provides, and it takes `Fold`s directly, so there is no way to
accidentally evaluate with a random split through this module's API.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .baseline import LANES
from .metrics import multiclass_log_loss
from .probability import validate_probability_distribution

if TYPE_CHECKING:
    from .walk_forward import Fold


class SecondPlaceError(ValueError):
    """Raised for invalid conditional second-place model input."""


def _validate_conditional_distribution(probs: dict[int, float], first_place: int) -> None:
    validate_probability_distribution(
        probs, LANES, error_type=SecondPlaceError, zero_at=first_place
    )


class ConditionalSecondPlaceModel:
    def __init__(self, smoothing: float = 1.0) -> None:
        self.smoothing = smoothing
        self._counts: dict[int, dict[int, int]] | None = None
        self._totals: dict[int, int] | None = None

    def fit(self, observations: list[tuple[int, int]]) -> ConditionalSecondPlaceModel:
        """observations: (first_place_lane, second_place_lane) pairs."""
        counts: dict[int, dict[int, int]] = {
            first: {j: 0 for j in LANES if j != first} for first in LANES
        }
        totals: dict[int, int] = dict.fromkeys(LANES, 0)

        for first, second in observations:
            if first not in LANES or second not in LANES:
                raise SecondPlaceError(f"lane out of range: first={first!r} second={second!r}")
            if first == second:
                raise SecondPlaceError(
                    f"first and second place cannot be the same lane: {first!r}"
                )
            counts[first][second] += 1
            totals[first] += 1

        self._counts = counts
        self._totals = totals
        return self

    def predict(self, first_place: int) -> dict[int, float]:
        if self._counts is None or self._totals is None:
            raise SecondPlaceError("call fit() before predict()")
        if first_place not in LANES:
            raise SecondPlaceError(f"first_place out of range: {first_place!r}")

        other_lanes = [j for j in LANES if j != first_place]
        denominator = self._totals[first_place] + self.smoothing * len(other_lanes)
        probs = {
            j: (self._counts[first_place].get(j, 0) + self.smoothing) / denominator
            for j in other_lanes
        }
        probs[first_place] = 0.0

        _validate_conditional_distribution(probs, first_place)
        return probs


def evaluate_with_folds(
    folds: list[Fold],
    dates: list,
    observations: list[tuple[int, int]],
    *,
    smoothing: float = 1.0,
) -> list[dict]:
    """Fit and evaluate the conditional model per walk-forward fold
    (P1-T002's `Fold`), never a random split. Returns one dict per fold
    with `fold_index`, `n_test`, and mean `log_loss` on that fold's test
    observations."""
    if not folds:
        raise SecondPlaceError("folds must not be empty")

    results = []
    for fold in folds:
        train_idx = fold.train_indices(dates)
        test_idx = fold.test_indices(dates)
        if not train_idx or not test_idx:
            raise SecondPlaceError(f"fold {fold.fold_index} has an empty train or test split")

        train_obs = [observations[i] for i in train_idx]
        test_obs = [observations[i] for i in test_idx]

        model = ConditionalSecondPlaceModel(smoothing=smoothing).fit(train_obs)
        actual_seconds = [second for _, second in test_obs]
        # each row depends on that observation's own first place, so a
        # fresh distribution is predicted per row (not one fixed row
        # reused, unlike baseline.py's evaluate()).
        probs_rows = [
            [model.predict(first)[lane] for lane in LANES] for first, _ in test_obs
        ]

        results.append(
            {
                "fold_index": fold.fold_index,
                "n_test": len(test_obs),
                "log_loss": multiclass_log_loss(actual_seconds, probs_rows, LANES),
            }
        )
    return results
