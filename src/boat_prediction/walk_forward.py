"""Time-based walk-forward validation (P1-T002).

Random train/test splitting is prohibited for this project
(docs/PROJECT_PROFILE.md, .claude/rules/09-ml-data-science.md — "Random
train/test splitting is prohibited for time-dependent prediction"). This
module generates monthly, expanding-window walk-forward folds: for each
calendar month after an initial warm-up window, the training set is
every record strictly before that month's start and the test set is that
month itself. Fold boundaries are plain calendar dates derived
deterministically from the input — no randomness, so re-running with the
same dates always yields identical folds.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


class WalkForwardError(ValueError):
    """Raised for invalid walk-forward configuration or input."""


def _month_start(value: date) -> date:
    return date(value.year, value.month, 1)


def _next_month_start(value: date) -> date:
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)


@dataclass(frozen=True)
class Fold:
    fold_index: int
    train_start: date
    train_end: date  # exclusive
    test_start: date
    test_end: date  # exclusive

    def train_indices(self, dates: list[date]) -> list[int]:
        return [i for i, d in enumerate(dates) if self.train_start <= d < self.train_end]

    def test_indices(self, dates: list[date]) -> list[int]:
        return [i for i, d in enumerate(dates) if self.test_start <= d < self.test_end]


def generate_monthly_folds(dates: list[date], *, min_train_months: int = 1) -> list[Fold]:
    """One fold per calendar month after the first `min_train_months`
    distinct months present in `dates`. Train always starts at the
    earliest date in `dates` and grows (expanding window); test is
    exactly one calendar month, immediately following train."""
    if not dates:
        raise WalkForwardError("dates must not be empty")
    if min_train_months < 1:
        raise WalkForwardError("min_train_months must be >= 1")

    month_starts = sorted({_month_start(d) for d in dates})
    if len(month_starts) <= min_train_months:
        raise WalkForwardError(
            f"not enough distinct months ({len(month_starts)}) for "
            f"min_train_months={min_train_months}"
        )

    overall_start = month_starts[0]
    folds = []
    for fold_index, test_start in enumerate(month_starts[min_train_months:]):
        test_end = _next_month_start(test_start)
        folds.append(
            Fold(
                fold_index=fold_index,
                train_start=overall_start,
                train_end=test_start,
                test_start=test_start,
                test_end=test_end,
            )
        )
    return folds
