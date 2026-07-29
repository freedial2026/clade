"""Exacta (2連単) combination probability construction and calibration
(P3-T003).

Combines a first-place probability distribution (P1) with P3-T002's
conditional second-place model into a coherent 30-class exacta
distribution:

    P(first=i, second=j) = P(first=i) * P(second=j | first=i)

for the 6*5 = 30 valid ordered `(i, j)` pairs with `i != j`.

This module deliberately stops at exacta (2 positions). A trifecta
(3連単, 120 = 6*5*4 ordered classes) is **not** built here —
docs/PROJECT_PROFILE.md phases three-way combinations after P0-P3
demonstrate stable value, and this task's acceptance criteria say so
explicitly.

Combinations are represented as a single int code `first*10 + second`
(both single digits 1-6), so this module reuses `calibration.py`'s
existing `int`-typed metrics/ECE functions without widening their type
hints for a tuple label.
"""

from __future__ import annotations

from .baseline import LANES
from .calibration import CalibrationReport
from .calibration import evaluate as evaluate_calibration
from .second_place import ConditionalSecondPlaceModel


class ExactaError(ValueError):
    """Raised for invalid exacta construction/evaluation input."""


ALL_COMBINATIONS: tuple[int, ...] = tuple(
    sorted(first * 10 + second for first in LANES for second in LANES if first != second)
)


def encode_combination(first: int, second: int) -> int:
    if first not in LANES or second not in LANES:
        raise ExactaError(f"lane out of range: first={first!r} second={second!r}")
    if first == second:
        raise ExactaError(f"first and second place cannot be the same lane: {first!r}")
    return first * 10 + second


def decode_combination(code: int) -> tuple[int, int]:
    if code not in ALL_COMBINATIONS:
        raise ExactaError(f"not a valid exacta combination code: {code!r}")
    return divmod(code, 10)


def construct_exacta_probabilities(
    first_place_probabilities: dict[int, float],
    second_place_model: ConditionalSecondPlaceModel,
) -> dict[int, float]:
    """Build the 30-class joint exacta distribution and verify it is
    coherent: it sums to 1.0, and marginalizing over `second` for each
    `first` recovers `first_place_probabilities[first]` exactly."""
    if set(first_place_probabilities) != set(LANES):
        raise ExactaError(f"first_place_probabilities must cover lanes {LANES}")

    combination_probs: dict[int, float] = {}
    for first in LANES:
        conditional = second_place_model.predict(first)
        for second in LANES:
            if second == first:
                continue
            combination_probs[encode_combination(first, second)] = (
                first_place_probabilities[first] * conditional[second]
            )

    _assert_coherent(combination_probs, first_place_probabilities)
    return combination_probs


def _assert_coherent(
    combination_probs: dict[int, float], first_place_probabilities: dict[int, float]
) -> None:
    if set(combination_probs) != set(ALL_COMBINATIONS):
        raise ExactaError("combination_probs must cover exactly the 30 valid exacta combinations")

    total = sum(combination_probs.values())
    if abs(total - 1.0) > 1e-9:
        raise ExactaError(f"combination probabilities must sum to 1.0, got {total!r}")

    for first in LANES:
        marginal = sum(p for code, p in combination_probs.items() if code // 10 == first)
        expected = first_place_probabilities[first]
        if abs(marginal - expected) > 1e-9:
            raise ExactaError(
                f"marginal for first={first} is {marginal!r}, expected {expected!r} "
                "(P(first)*P(second|first) must recover the first-place distribution)"
            )


def evaluate_exacta_calibration(
    actual_combinations: list[int], predicted_probs: dict[int, float]
) -> CalibrationReport:
    """Evaluate calibration of a fixed exacta distribution against
    observed outcomes, reusing `calibration.py`'s metrics (encoded
    combinations are plain ints already)."""
    if not actual_combinations:
        raise ExactaError("actual_combinations must not be empty")
    classes = list(ALL_COMBINATIONS)
    probs_row = [predicted_probs[c] for c in classes]
    probs = [probs_row for _ in actual_combinations]
    return evaluate_calibration(actual_combinations, probs, classes)
