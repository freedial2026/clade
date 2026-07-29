"""Conservative expected value (P2-T003).

docs/domain/claude_boatrace_prediction_system_implementation_guide.md
§14.3-14.4:

    conservative_probability = max(0, calibrated_probability - uncertainty_margin)
    conservative_ev = conservative_probability * odds

`uncertainty_margin` must not be a fixed constant — it is derived from
calibration error, sample size, and model variance (§14.3: "固定値で決めず、
校正誤差・標本数・モデル分散から算出する"). The guide specifies the
inputs but not the exact combination, so the combination used here is a
documented, versioned formula (`UNCERTAINTY_MARGIN_FORMULA_VERSION`):

    margin = calibration_error
             + sample_size_weight / sqrt(n_samples)
             + variance_weight * model_std

All three terms shrink the usable probability more when calibration is
poor, evidence is thin, or models disagree; `sample_size_weight`/
`variance_weight` are caller-supplied, not hardcoded.

This module only computes numbers to report (§14.4: "P2初期ではEVを表示
するだけとし、自動購入に接続しない" — P2 initially only displays EV, it
is not wired to automated purchasing, and no abstention threshold is
decided here either — that is P2-T004's job).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

UNCERTAINTY_MARGIN_FORMULA_VERSION = "uncertainty_margin_v1"


class ExpectedValueError(ValueError):
    """Raised for invalid inputs."""


def uncertainty_margin(
    calibration_error: float,
    n_samples: int,
    model_std: float = 0.0,
    *,
    sample_size_weight: float = 1.0,
    variance_weight: float = 1.0,
) -> float:
    if calibration_error < 0:
        raise ExpectedValueError(f"calibration_error must be >= 0: {calibration_error!r}")
    if n_samples <= 0:
        raise ExpectedValueError(f"n_samples must be > 0: {n_samples!r}")
    if model_std < 0:
        raise ExpectedValueError(f"model_std must be >= 0: {model_std!r}")

    return (
        calibration_error
        + sample_size_weight / math.sqrt(n_samples)
        + variance_weight * model_std
    )


@dataclass(frozen=True)
class ExpectedValueReport:
    raw_probability: float
    conservative_probability: float
    odds: float
    raw_ev: float
    conservative_ev: float
    uncertainty_margin: float
    formula_version: str

    def to_dict(self) -> dict:
        return {
            "raw_probability": self.raw_probability,
            "conservative_probability": self.conservative_probability,
            "odds": self.odds,
            "raw_ev": self.raw_ev,
            "conservative_ev": self.conservative_ev,
            "uncertainty_margin": self.uncertainty_margin,
            "formula_version": self.formula_version,
        }


def compute_conservative_ev(
    calibrated_probability: float,
    odds: float,
    calibration_error: float,
    n_samples: int,
    model_std: float = 0.0,
    *,
    sample_size_weight: float = 1.0,
    variance_weight: float = 1.0,
) -> ExpectedValueReport:
    if not (0 <= calibrated_probability <= 1):
        raise ExpectedValueError(
            f"calibrated_probability must be in [0, 1]: {calibrated_probability!r}"
        )
    if odds <= 0:
        raise ExpectedValueError(f"odds must be positive: {odds!r}")

    margin = uncertainty_margin(
        calibration_error,
        n_samples,
        model_std,
        sample_size_weight=sample_size_weight,
        variance_weight=variance_weight,
    )
    conservative_probability = max(0.0, calibrated_probability - margin)

    return ExpectedValueReport(
        raw_probability=calibrated_probability,
        conservative_probability=conservative_probability,
        odds=odds,
        raw_ev=calibrated_probability * odds,
        conservative_ev=conservative_probability * odds,
        uncertainty_margin=margin,
        formula_version=UNCERTAINTY_MARGIN_FORMULA_VERSION,
    )
