"""Shared multiclass probability metrics.

Factored out because `model_comparison.py` (P1-T004) and
`calibration.py` (P1-T005) both need the same row-wise multiclass
log-loss/Brier-score computation over `(y_true, predicted probability
rows, class order)`.
"""

from __future__ import annotations

import math
from collections.abc import Sequence


class MetricsError(ValueError):
    """Raised for invalid metric inputs."""


def _class_index(classes: Sequence[int]) -> dict[int, int]:
    return {c: i for i, c in enumerate(classes)}


def multiclass_log_loss(
    y_true: Sequence[int], probs: Sequence[Sequence[float]], classes: Sequence[int]
) -> float:
    if not y_true:
        raise MetricsError("y_true must not be empty")
    eps = 1e-12
    index = _class_index(classes)
    total = 0.0
    for label, row in zip(y_true, probs):
        total += -math.log(max(row[index[label]], eps))
    return total / len(y_true)


def multiclass_brier_score(
    y_true: Sequence[int], probs: Sequence[Sequence[float]], classes: Sequence[int]
) -> float:
    """Mean squared error between predicted probabilities and the
    one-hot actual outcome, summed over classes per row (the standard
    multiclass Brier score)."""
    if not y_true:
        raise MetricsError("y_true must not be empty")
    index = _class_index(classes)
    total = 0.0
    for label, row in zip(y_true, probs):
        winner_index = index[label]
        total += sum((p - (1.0 if i == winner_index else 0.0)) ** 2 for i, p in enumerate(row))
    return total / len(y_true)
