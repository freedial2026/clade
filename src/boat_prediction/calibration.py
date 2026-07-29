"""Probability evaluation and calibration (P1-T005).

Prediction, calibration, market comparison, and action policy stay
separate stages (docs/PROJECT_PROFILE.md) — this module only covers
evaluation (log loss, Brier score, expected calibration error) and a
simple, dependency-free histogram-binning recalibrator. It reuses
`metrics.py`'s multiclass log-loss/Brier-score rather than
re-implementing them.

Calibration must be fit on training/validation data only; the holdout
set is reserved for a final, untouched evaluation. `fit_and_evaluate()`
enforces this by taking the fit split and the holdout split as separate
arguments and only ever calling `BinnedCalibrator.fit()` on the former.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .metrics import multiclass_brier_score, multiclass_log_loss


class CalibrationError(ValueError):
    """Raised for invalid calibration inputs or misuse."""


@dataclass(frozen=True)
class CalibrationReport:
    log_loss: float
    brier_score: float
    expected_calibration_error: float
    n_samples: int

    def to_dict(self) -> dict:
        return {
            "log_loss": self.log_loss,
            "brier_score": self.brier_score,
            "expected_calibration_error": self.expected_calibration_error,
            "n_samples": self.n_samples,
        }


def _confidence_and_correctness(
    y_true: Sequence[int], probs: Sequence[Sequence[float]], classes: Sequence[int]
) -> tuple[list[float], list[bool]]:
    confidences: list[float] = []
    correctness: list[bool] = []
    for label, row in zip(y_true, probs):
        predicted_index = max(range(len(row)), key=lambda i: row[i])
        confidences.append(row[predicted_index])
        correctness.append(classes[predicted_index] == label)
    return confidences, correctness


def _bin_index(confidence: float, n_bins: int) -> int:
    return min(int(confidence * n_bins), n_bins - 1)


def expected_calibration_error(
    y_true: Sequence[int],
    probs: Sequence[Sequence[float]],
    classes: Sequence[int],
    *,
    n_bins: int = 10,
) -> float:
    """Bin predictions by top-class confidence; weight each bin's
    |mean confidence - empirical accuracy| by its share of samples."""
    if not y_true:
        raise CalibrationError("y_true must not be empty")

    confidences, correctness = _confidence_and_correctness(y_true, probs, classes)
    bins: list[list[tuple[float, bool]]] = [[] for _ in range(n_bins)]
    for conf, correct in zip(confidences, correctness):
        bins[_bin_index(conf, n_bins)].append((conf, correct))

    n = len(confidences)
    ece = 0.0
    for bucket in bins:
        if not bucket:
            continue
        mean_conf = sum(c for c, _ in bucket) / len(bucket)
        accuracy = sum(1 for _, correct in bucket if correct) / len(bucket)
        ece += (len(bucket) / n) * abs(mean_conf - accuracy)
    return ece


def evaluate(
    y_true: Sequence[int], probs: Sequence[Sequence[float]], classes: Sequence[int]
) -> CalibrationReport:
    return CalibrationReport(
        log_loss=multiclass_log_loss(y_true, probs, classes),
        brier_score=multiclass_brier_score(y_true, probs, classes),
        expected_calibration_error=expected_calibration_error(y_true, probs, classes),
        n_samples=len(y_true),
    )


class BinnedCalibrator:
    """Maps predicted top-class confidence to empirical accuracy, fit on
    one data split and applied to another (e.g. a holdout) via
    `calibrate_confidence()`. Fitting is pure counting over its input —
    no randomness, so re-fitting on the same data is reproducible."""

    def __init__(self, n_bins: int = 10) -> None:
        self._n_bins = n_bins
        self._bin_accuracy: list[float] | None = None

    def fit(
        self, y_true: Sequence[int], probs: Sequence[Sequence[float]], classes: Sequence[int]
    ) -> BinnedCalibrator:
        if not y_true:
            raise CalibrationError("y_true must not be empty")
        confidences, correctness = _confidence_and_correctness(y_true, probs, classes)

        buckets: list[list[bool]] = [[] for _ in range(self._n_bins)]
        for conf, correct in zip(confidences, correctness):
            buckets[_bin_index(conf, self._n_bins)].append(correct)

        # Empty bins fall back to their own midpoint confidence (no data
        # to say otherwise), so the mapping stays defined everywhere.
        self._bin_accuracy = [
            (sum(bucket) / len(bucket)) if bucket else (i + 0.5) / self._n_bins
            for i, bucket in enumerate(buckets)
        ]
        return self

    def calibrate_confidence(self, confidence: float) -> float:
        if self._bin_accuracy is None:
            raise CalibrationError("call fit() before calibrate_confidence()")
        return self._bin_accuracy[_bin_index(confidence, self._n_bins)]


def fit_and_evaluate(
    fit_y: Sequence[int],
    fit_probs: Sequence[Sequence[float]],
    holdout_y: Sequence[int],
    holdout_probs: Sequence[Sequence[float]],
    classes: Sequence[int],
    *,
    n_bins: int = 10,
) -> tuple[BinnedCalibrator, CalibrationReport, CalibrationReport]:
    """Fit a `BinnedCalibrator` on (fit_y, fit_probs) — the
    train/validation split, already combined by the caller — and report
    calibration metrics for that split and, separately, for the holdout
    split. The holdout arguments are never passed to `.fit()`."""
    calibrator = BinnedCalibrator(n_bins=n_bins).fit(fit_y, fit_probs, classes)
    fit_report = evaluate(fit_y, fit_probs, classes)
    holdout_report = evaluate(holdout_y, holdout_probs, classes)
    return calibrator, fit_report, holdout_report
