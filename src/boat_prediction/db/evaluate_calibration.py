"""Leakage-safe calibration of `logistic_cards` over real walk-forward folds.

`calibration.py` provides `BinnedCalibrator` and `evaluate()` but nothing
ran them against real predictions -- same gap `evaluate_p1.py` filled for
walk-forward itself. This is that step for calibration specifically.

Why not just calibrate on the training fold
--------------------------------------------

`calibration.py`'s own docstring is explicit: "Calibration must be fit on
training/validation data only; the holdout set is reserved for a final,
untouched evaluation." The trap in a walk-forward setting is which data
counts as training here. A classifier's predictions on the rows it was
just fit on are systematically overconfident (in-sample), so fitting a
calibrator on those same predictions would calibrate away the model's
own overfitting rather than measure it -- understating how miscalibrated
the model actually is out-of-sample.

So each fold is split into three chronological pieces, not two:

    [ core_train ][ calib_valid (1 month) ][ test (1 month) ]

The classifier is fit on `core_train` only. It then predicts on
`calib_valid` -- data it has never seen -- and `BinnedCalibrator` is fit
on *those* predictions. The same classifier (unchanged) then predicts on
`test`, and the calibrator (unchanged) remaps those predictions before
scoring. This costs one month of training data per fold, which is the
correct price for a calibration check that means anything: `docs/PROJECT_
PROFILE.md` requires "prediction, probability calibration, market
comparison, and action policy remain separate stages", and a calibrator
fit on in-sample predictions blurs the first two of those into one.

Reconstructing a full probability vector
-----------------------------------------

`BinnedCalibrator` only remaps the top predicted class's confidence
(`calibrate_confidence()`), because that is all `expected_calibration_
error` needs. Multiclass log-loss needs a full 6-lane vector, so
`_reconstruct_calibrated_vector` rescales the other five lanes to still
sum to `1 - calibrated_confidence`, preserving their relative order --
the standard confidence-then-renormalize approach. If the raw prediction
was (numerically) a near-single point mass, the remaining mass is spread
uniformly rather than divided by a near-zero denominator.

`min_train_months` must be at least 2 here (one month for `calib_valid`,
at least one more to leave `core_train` non-empty) -- one more than
`evaluate_p1`'s default requires.
"""

from __future__ import annotations

import argparse
import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

from ..calibration import BinnedCalibrator, CalibrationReport
from ..calibration import evaluate as evaluate_calibration
from ..walk_forward import generate_monthly_folds
from .dataset import LANES
from .dataset_cache import (
    add_cache_arguments,
    build_dataset_cached,
    cache_options,
    report_to_stderr,
)
from .evaluate_p1 import _sklearn_available, sklearn_logistic_factory
from .session import create_db_engine, create_session_factory


class CalibrationEvalError(ValueError):
    """Raised for invalid input to the calibration evaluation itself."""


def _previous_month_start(value: dt.date) -> dt.date:
    if value.month == 1:
        return dt.date(value.year - 1, 12, 1)
    return dt.date(value.year, value.month - 1, 1)


def _reconstruct_calibrated_vector(
    raw_row: list[float], calibrator: BinnedCalibrator
) -> list[float]:
    top_index = max(range(len(raw_row)), key=lambda i: raw_row[i])
    original_confidence = raw_row[top_index]
    calibrated_confidence = calibrator.calibrate_confidence(original_confidence)

    remaining_original = sum(raw_row) - original_confidence
    remaining_new = 1.0 - calibrated_confidence
    row = list(raw_row)
    if remaining_original <= 1e-9:
        share = remaining_new / (len(raw_row) - 1) if len(raw_row) > 1 else 0.0
        row = [share] * len(raw_row)
    else:
        scale = remaining_new / remaining_original
        row = [value * scale for value in raw_row]
    row[top_index] = calibrated_confidence
    return row


@dataclass
class FoldCalibration:
    fold_index: int
    test_month: str
    core_train_races: int
    calib_valid_races: int
    test_races: int
    raw: CalibrationReport
    calibrated: CalibrationReport


@dataclass
class CalibrationResult:
    n_races: int
    per_fold: list[FoldCalibration] = field(default_factory=list)

    def mean(self, attr: str, which: str) -> float:
        values = [getattr(getattr(f, which), attr) for f in self.per_fold]
        return sum(values) / len(values) if values else float("nan")


def evaluate(
    session, *, start_date: dt.date, end_date: dt.date, min_train_months: int = 8,
    cache_dir: Path | None = None,
    refresh_cache: bool = False,
) -> CalibrationResult:
    if min_train_months < 2:
        raise CalibrationEvalError(
            f"min_train_months must be >= 2 (1 for calib_valid, 1+ for core_train), "
            f"got {min_train_months}"
        )
    if not _sklearn_available():
        raise CalibrationEvalError("scikit-learn is not installed; nothing to calibrate")

    data = build_dataset_cached(
        session,
        start_date=start_date,
        end_date=end_date,
        cache_dir=cache_dir,
        refresh=refresh_cache,
        on_event=report_to_stderr,
    )
    if not len(data):
        raise CalibrationEvalError("no usable races in the requested range")

    folds = generate_monthly_folds(data.dates, min_train_months=min_train_months)
    classes = list(LANES)
    factory = sklearn_logistic_factory()

    result = CalibrationResult(n_races=len(data))
    for fold in folds:
        calib_start = _previous_month_start(fold.test_start)
        core_train_idx = [
            i for i in fold.train_indices(data.dates) if data.dates[i] < calib_start
        ]
        calib_valid_idx = [
            i
            for i in fold.train_indices(data.dates)
            if calib_start <= data.dates[i] < fold.test_start
        ]
        test_idx = fold.test_indices(data.dates)
        if not core_train_idx or not calib_valid_idx or not test_idx:
            continue

        model = factory()
        model.fit([data.X[i] for i in core_train_idx], [data.y[i] for i in core_train_idx])

        calib_probs = model.predict_proba([data.X[i] for i in calib_valid_idx])
        calibrator = BinnedCalibrator().fit(
            [data.y[i] for i in calib_valid_idx], calib_probs, classes
        )

        test_y = [data.y[i] for i in test_idx]
        raw_test_probs = model.predict_proba([data.X[i] for i in test_idx])
        calibrated_test_probs = [
            _reconstruct_calibrated_vector(list(row), calibrator) for row in raw_test_probs
        ]

        result.per_fold.append(
            FoldCalibration(
                fold_index=fold.fold_index,
                test_month=fold.test_start.isoformat(),
                core_train_races=len(core_train_idx),
                calib_valid_races=len(calib_valid_idx),
                test_races=len(test_idx),
                raw=evaluate_calibration(test_y, raw_test_probs, classes),
                calibrated=evaluate_calibration(test_y, calibrated_test_probs, classes),
            )
        )

    if not result.per_fold:
        raise CalibrationEvalError(
            "no fold had a non-empty core_train/calib_valid/test split; "
            "widen the date range or lower min_train_months"
        )
    return result


def render(result: CalibrationResult) -> str:
    lines = [
        f"logistic_cards calibration  races={result.n_races}  folds={len(result.per_fold)}",
        "",
        (
            f"{'month':<10}{'core':>8}{'calib':>7}{'test':>7}"
            f"{'raw_ll':>10}{'cal_ll':>10}{'raw_ece':>10}{'cal_ece':>10}"
        ),
    ]
    for f in result.per_fold:
        lines.append(
            f"{f.test_month:<10}{f.core_train_races:>8}{f.calib_valid_races:>7}{f.test_races:>7}"
            f"{f.raw.log_loss:>10.4f}{f.calibrated.log_loss:>10.4f}"
            f"{f.raw.expected_calibration_error:>10.4f}"
            f"{f.calibrated.expected_calibration_error:>10.4f}"
        )
    lines.append("")
    lines.append(
        f"mean log-loss:  raw={result.mean('log_loss', 'raw'):.5f}  "
        f"calibrated={result.mean('log_loss', 'calibrated'):.5f}"
    )
    lines.append(
        f"mean ECE:       raw={result.mean('expected_calibration_error', 'raw'):.5f}  "
        f"calibrated={result.mean('expected_calibration_error', 'calibrated'):.5f}"
    )
    return "\n".join(lines)


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--start-date", type=dt.date.fromisoformat, required=True)
    parser.add_argument("--end-date", type=dt.date.fromisoformat, required=True)
    parser.add_argument("--min-train-months", type=int, default=8)
    add_cache_arguments(parser)
    args = parser.parse_args(argv)

    engine = create_db_engine(args.database_url)
    try:
        with create_session_factory(engine)() as session:
            result = evaluate(
                session,
                start_date=args.start_date,
                end_date=args.end_date,
                min_train_months=args.min_train_months,
                **cache_options(args),
            )
    finally:
        engine.dispose()

    print(render(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
