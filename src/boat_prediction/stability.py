"""Subgroup stability and uncertainty report (P2-T006).

Assesses whether performance is stable across subgroups — month, venue,
grade, odds band — rather than concentrated in (or an artifact of) one
group (.claude/rules/09-ml-data-science.md: "Evaluate uncertainty,
calibration, drift, subgroup stability, and operational failure
modes"). One generic function computes this for any grouping key —
"by month" / "by venue" / "by grade" / "by odds band" are the same
computation with a different `group_key` callable, not four separate
implementations.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass

# Standard z-scores for common two-sided confidence levels (stdlib-only;
# avoids a scipy dependency just for an inverse-normal-CDF lookup).
_Z_SCORES: dict[float, float] = {0.80: 1.2816, 0.90: 1.6449, 0.95: 1.96, 0.99: 2.5758}


class StabilityError(ValueError):
    """Raised for invalid stability-assessment input."""


@dataclass(frozen=True)
class SubgroupStats:
    group: Hashable
    n: int
    mean: float
    ci_low: float
    ci_high: float
    share_of_total: float
    concentration_flag: bool


@dataclass(frozen=True)
class StabilityReport:
    subgroups: tuple[SubgroupStats, ...]
    confidence_level: float
    concentration_threshold: float

    @property
    def flagged_groups(self) -> tuple[Hashable, ...]:
        return tuple(s.group for s in self.subgroups if s.concentration_flag)

    def to_dict(self) -> dict:
        return {
            "subgroups": [
                {
                    "group": stats.group,
                    "n": stats.n,
                    "mean": stats.mean,
                    "ci_low": stats.ci_low,
                    "ci_high": stats.ci_high,
                    "share_of_total": stats.share_of_total,
                    "concentration_flag": stats.concentration_flag,
                }
                for stats in self.subgroups
            ],
            "confidence_level": self.confidence_level,
            "concentration_threshold": self.concentration_threshold,
        }


def _z_score(confidence_level: float) -> float:
    if confidence_level not in _Z_SCORES:
        raise StabilityError(
            f"unsupported confidence_level {confidence_level!r}; "
            f"supported: {sorted(_Z_SCORES)}"
        )
    return _Z_SCORES[confidence_level]


def assess_subgroup_stability(
    records: Sequence[object],
    group_key: Callable[[object], Hashable],
    value_key: Callable[[object], float],
    *,
    confidence_level: float = 0.95,
    concentration_threshold: float = 0.5,
) -> StabilityReport:
    """Group `records` by `group_key`, average `value_key` per group, and
    report sample size, a normal-approximation confidence interval on the
    mean, and whether the group's share of all records exceeds
    `concentration_threshold` (a caller-supplied fraction, not
    hardcoded)."""
    if not records:
        raise StabilityError("records must not be empty")
    if not (0 < concentration_threshold <= 1):
        raise StabilityError(
            f"concentration_threshold must be in (0, 1]: {concentration_threshold!r}"
        )

    z = _z_score(confidence_level)
    values_by_group: dict[Hashable, list[float]] = {}
    for record in records:
        values_by_group.setdefault(group_key(record), []).append(value_key(record))

    total_n = len(records)
    subgroups = []
    for group, values in values_by_group.items():
        n = len(values)
        mean = sum(values) / n
        if n > 1:
            variance = sum((v - mean) ** 2 for v in values) / (n - 1)
            stderr = math.sqrt(variance / n)
        else:
            stderr = 0.0
        margin = z * stderr
        share = n / total_n
        subgroups.append(
            SubgroupStats(
                group=group,
                n=n,
                mean=mean,
                ci_low=mean - margin,
                ci_high=mean + margin,
                share_of_total=share,
                concentration_flag=share > concentration_threshold,
            )
        )

    subgroups.sort(key=lambda s: (-s.n, str(s.group)))
    return StabilityReport(
        subgroups=tuple(subgroups),
        confidence_level=confidence_level,
        concentration_threshold=concentration_threshold,
    )
