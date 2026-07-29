"""Data-quality report and ML acceptance gate (P0-T010).

Combines axis scores per
docs/domain/claude_boatrace_prediction_system_implementation_guide.md
§10.2:

    completeness    25
    uniqueness      15
    validity        20
    consistency     15
    point_in_time   25
    total          100

into a report that is both machine-readable (`to_dict()`/`to_json()`)
and human-readable (`render()`). The same section's thresholds decide
whether downstream ML may use the dataset:

    95-100  train_or_predict
    90-94   predict_with_warning
    80-89   research_only   (prediction refused, research permitted)
    0-79    blocked         (prediction and research both refused)
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

AXIS_WEIGHTS: Mapping[str, int] = {
    "completeness": 25,
    "uniqueness": 15,
    "validity": 20,
    "consistency": 15,
    "point_in_time": 25,
}

DEFAULT_THRESHOLDS: tuple[tuple[int, str], ...] = (
    (95, "train_or_predict"),
    (90, "predict_with_warning"),
    (80, "research_only"),
    (0, "blocked"),
)

_PREDICTION_VERDICTS = frozenset({"train_or_predict", "predict_with_warning"})
_BLOCKED_VERDICT = "blocked"


class QualityGateError(ValueError):
    """Raised for invalid axis scores or when ML tries to proceed on a
    dataset the quality gate does not permit for that purpose."""


@dataclass(frozen=True)
class QualityReport:
    axis_scores: dict[str, float]
    total_score: float
    verdict: str

    @property
    def allows_prediction(self) -> bool:
        return self.verdict in _PREDICTION_VERDICTS

    @property
    def allows_research(self) -> bool:
        return self.verdict != _BLOCKED_VERDICT

    def to_dict(self) -> dict:
        return {
            "axis_scores": dict(self.axis_scores),
            "total_score": self.total_score,
            "verdict": self.verdict,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    def render(self) -> str:
        lines = [f"Data quality report: {self.total_score:.1f}/100 -> {self.verdict}"]
        for axis, weight in AXIS_WEIGHTS.items():
            lines.append(f"  {axis}: {self.axis_scores[axis]:.1f}/{weight}")
        return "\n".join(lines)


def score_report(
    axis_scores: Mapping[str, float],
    *,
    thresholds: tuple[tuple[int, str], ...] = DEFAULT_THRESHOLDS,
) -> QualityReport:
    missing = set(AXIS_WEIGHTS) - set(axis_scores)
    extra = set(axis_scores) - set(AXIS_WEIGHTS)
    if missing or extra:
        raise QualityGateError(f"axis mismatch: missing={missing!r} extra={extra!r}")

    for axis, weight in AXIS_WEIGHTS.items():
        value = axis_scores[axis]
        if not (0 <= value <= weight):
            raise QualityGateError(f"{axis} score {value!r} out of range [0, {weight}]")

    total = sum(axis_scores.values())
    verdict = _verdict_for(total, thresholds)
    return QualityReport(axis_scores=dict(axis_scores), total_score=total, verdict=verdict)


def _verdict_for(total: float, thresholds: tuple[tuple[int, str], ...]) -> str:
    for minimum, verdict in sorted(thresholds, key=lambda pair: -pair[0]):
        if total >= minimum:
            return verdict
    raise QualityGateError(f"no threshold matched total score {total!r}")


def require_allowed(report: QualityReport, *, purpose: str) -> None:
    """Raise QualityGateError if `report`'s verdict does not permit
    `purpose` ('prediction' or 'research'). This is the enforcement
    point the ML pipeline must call before using a dataset."""
    if purpose == "prediction":
        if not report.allows_prediction:
            raise QualityGateError(
                f"prediction blocked by quality gate: {report.total_score:.1f}/100 ({report.verdict})"
            )
        return
    if purpose == "research":
        if not report.allows_research:
            raise QualityGateError(
                f"research blocked by quality gate: {report.total_score:.1f}/100 ({report.verdict})"
            )
        return
    raise QualityGateError(f"unknown purpose: {purpose!r}")
