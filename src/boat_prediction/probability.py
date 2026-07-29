"""Shared probability-distribution validation.

Factored out because `baseline.py`, `entry_course.py`, and
`second_place.py` each independently checked "this dict covers exactly
a fixed key set, every value is in [0, 1], and the values sum to 1.0
(within tolerance)" — `second_place.py` additionally required one key's
value to be exactly zero (an impossible outcome), which `zero_at`
covers here too.
"""

from __future__ import annotations

from collections.abc import Hashable, Iterable


def validate_probability_distribution(
    probs: dict[Hashable, float],
    keys: Iterable[Hashable],
    *,
    error_type: type[Exception],
    tolerance: float = 1e-9,
    zero_at: Hashable | None = None,
) -> None:
    """Raise `error_type` unless `probs` covers exactly `keys`, every
    value is in [0, 1], the values sum to 1.0 within `tolerance`, and
    (when `zero_at` is given) `probs[zero_at]` is exactly 0.0."""
    key_set = set(keys)
    if set(probs) != key_set:
        raise error_type(
            f"probabilities must cover {sorted(key_set, key=str)}, got {sorted(probs, key=str)}"
        )
    for key, value in probs.items():
        if not (0 <= value <= 1):
            raise error_type(f"probability for {key!r} out of [0, 1]: {value!r}")
    if zero_at is not None and probs[zero_at] != 0.0:
        raise error_type(f"probability for {zero_at!r} must be exactly 0.0, got {probs[zero_at]!r}")

    total = sum(probs.values())
    if abs(total - 1.0) > tolerance:
        raise error_type(f"probabilities must sum to 1.0, got {total!r}")
