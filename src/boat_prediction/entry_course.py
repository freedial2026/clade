"""Entry-course prediction (P3-T001).

**Note on sequencing**: docs/domain/.../implementation_guide.md line
1642 states P3 should start only once P2's forward test has proven
stable. No real data exists in this environment to run that forward
test, so this module — like all of P0-P2 — is built and unit-tested
against synthetic data only, as an explicitly acknowledged exception
(see tasks/P3-T001.md "Decisions and risks").

In Japanese boat racing, the pre-assigned lane number (`waku_ban`, 1-6)
is often different from the actual course a boat enters the first turn
from (`entry_course`) — boats jockey for position during the approach,
per docs/domain/.../implementation_guide.md's "進入コース確率推定".
This module keeps entry-course prediction and lane number as two
clearly separate concepts, and additionally keeps the full probability
distribution (for downstream probability-weighted math) distinct from a
single displayed "expected" course (the argmax, for human-readable
reports) — collapsing the two would silently turn a probability into a
false certainty.

High uncertainty in the entry-course distribution is wired to the
existing `abstention.py` reason code `RC_ENTRY_CHANGE` (§15.1) rather
than inventing a new one, since an uncertain/changed entry course is
exactly what that code already names.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .abstention import RC_ENTRY_CHANGE
from .baseline import LANES as COURSES
from .probability import validate_probability_distribution

UNCERTAINTY_ENTROPY_THRESHOLD_BITS_DEFAULT = 2.0  # uniform over 6 courses is ~2.585 bits


class EntryCourseError(ValueError):
    """Raised for invalid entry-course input."""


def _validate_distribution(probs: dict[int, float]) -> None:
    validate_probability_distribution(probs, COURSES, error_type=EntryCourseError)


def _entropy_bits(probs: dict[int, float]) -> float:
    return -sum(p * math.log2(p) for p in probs.values() if p > 0)


@dataclass(frozen=True)
class EntryCoursePrediction:
    lane_number: int
    course_probabilities: dict[int, float]

    def __post_init__(self) -> None:
        if self.lane_number not in COURSES:
            raise EntryCourseError(f"lane_number out of range: {self.lane_number!r}")
        _validate_distribution(self.course_probabilities)

    @property
    def expected_course(self) -> int:
        """The single course to *display* (argmax) — never a substitute
        for `course_probabilities` in probability-weighted calculations."""
        return max(self.course_probabilities, key=self.course_probabilities.get)

    @property
    def entropy_bits(self) -> float:
        return _entropy_bits(self.course_probabilities)

    @property
    def changed_from_lane(self) -> bool:
        """Whether the displayed expected course differs from the
        assigned lane number — a real, common occurrence in this sport,
        not a data error."""
        return self.expected_course != self.lane_number


def is_uncertain(
    prediction: EntryCoursePrediction,
    *,
    entropy_threshold_bits: float = UNCERTAINTY_ENTROPY_THRESHOLD_BITS_DEFAULT,
) -> bool:
    """True when the entry-course distribution is uncertain enough that
    an abstention policy should consider `RC_ENTRY_CHANGE`. The
    threshold is caller-supplied, not hardcoded into any decision path."""
    return prediction.entropy_bits > entropy_threshold_bits


def entry_course_abstention_reason(
    prediction: EntryCoursePrediction,
    *,
    entropy_threshold_bits: float = UNCERTAINTY_ENTROPY_THRESHOLD_BITS_DEFAULT,
) -> str | None:
    """Returns the `abstention.py` reason code to raise when this
    prediction is too uncertain to use, or None if it's usable."""
    if is_uncertain(prediction, entropy_threshold_bits=entropy_threshold_bits):
        return RC_ENTRY_CHANGE
    return None
