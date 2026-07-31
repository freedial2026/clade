"""Walk-forward evaluation of first-place probability on real data.

P1's parts existed and were tested on fixtures; this runs them over rows
from the database. What it answers is the only question worth asking
first: does anything beat the trivial baselines, measured the way a
time-ordered problem has to be measured?

Method
------

`walk_forward.generate_monthly_folds` gives an expanding train window and
a one-month test window, so every prediction is made from data that
precedes it. Random splitting is prohibited here for exactly the reason
it would flatter the result: races within a 節 share a motor, a boat and
a field, so a shuffled split puts near-duplicates on both sides.

Models
------

- `uniform` -- 1/6 per lane, the floor any model must clear.
- `lane_prior` -- the training window's own lane win frequencies. This
  is the baseline that matters: lane 1 wins far more often than lane 6,
  so anything that fails to beat it has learned nothing beyond "inside
  lanes win".
- Card-feature models are added only if scikit-learn is installed; the
  run reports their absence rather than failing, since the two baselines
  above need no third-party library and already answer the first
  question.

Both baselines predict one distribution for every race in a fold, which
is what makes them baselines. They are wrapped to the fit/predict_proba
shape `model_comparison` expects rather than that module being loosened
to accommodate them.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from ..baseline import LanePriorBaseline, UniformBaseline
from ..model_comparison import run_comparison
from ..walk_forward import generate_monthly_folds
from .dataset import LANES, build_dataset
from .session import create_db_engine, create_session_factory


class _FixedDistribution:
    """Adapts a baseline that predicts one distribution for every race to
    the `ProbabilisticClassifier` shape `run_comparison` fits."""

    def __init__(self, probs_from_winners) -> None:
        self._probs_from_winners = probs_from_winners
        self._row: list[float] = []

    def fit(self, X: Sequence[Sequence[float]], y: Sequence[int]) -> _FixedDistribution:
        del X
        probs = self._probs_from_winners(list(y))
        self._row = [probs[lane] for lane in LANES]
        return self

    def predict_proba(self, X: Sequence[Sequence[float]]) -> list[list[float]]:
        return [self._row for _ in X]


def uniform_factory() -> _FixedDistribution:
    return _FixedDistribution(lambda _winners: UniformBaseline().predict())


def lane_prior_factory() -> _FixedDistribution:
    return _FixedDistribution(lambda winners: LanePriorBaseline().fit(winners).predict())


def per_race_log_loss(
    y_true: Sequence[int], probs: Sequence[Sequence[float]], classes: Sequence[int]
) -> list[float]:
    """`metrics.multiclass_log_loss`'s per-row values rather than their
    mean, for a subgroup breakdown (`evaluate_phase.py`) that needs one
    score per race rather than one aggregate over the whole set. Same
    epsilon floor as `multiclass_log_loss`, so the two agree exactly on
    a full set's mean."""
    eps = 1e-12
    index = {c: i for i, c in enumerate(classes)}
    return [-math.log(max(row[index[label]], eps)) for label, row in zip(y_true, probs)]


class _AlignedProba:
    """Forces a scikit-learn estimator's `predict_proba` into lane order.

    `metrics.multiclass_log_loss` indexes each row by the position of the
    true class in the `classes` list it is given, while scikit-learn
    orders its columns by the classes it saw in *training*. If a fold's
    training window happens to contain no win by some lane, the estimator
    returns five columns and every probability after the gap is read as
    belonging to the wrong lane -- no exception, just a quietly wrong
    score. Missing lanes are emitted as 0.0, which the log-loss floors at
    its epsilon, so an unseen lane winning costs what it should.
    """

    def __init__(self, estimator, classes: Sequence[int]) -> None:
        self._estimator = estimator
        self._classes = list(classes)

    def fit(self, X, y):
        self._estimator.fit(X, y)
        return self

    def predict_proba(self, X) -> list[list[float]]:
        raw = self._estimator.predict_proba(X)
        trained = [int(c) for c in self._estimator.classes_]
        position = {c: i for i, c in enumerate(trained)}
        return [
            [float(row[position[c]]) if c in position else 0.0 for c in self._classes]
            for row in raw
        ]


def sklearn_logistic_factory(**params):
    """Multinomial logistic regression over the card features.

    Scaled first: the raw columns mix win rates near 5, ages near 40 and
    a 1-4 class rank, and an unscaled fit would let the widest column
    dominate the penalty.
    """

    def factory():
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler

        return _AlignedProba(
            make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000, **params)),
            LANES,
        )

    return factory


def _sklearn_available() -> bool:
    try:
        import sklearn  # noqa: F401
    except ImportError:
        return False
    return True


@dataclass
class EvaluationResult:
    n_races: int
    n_folds: int
    first_date: dt.date | None
    last_date: dt.date | None
    mean_log_loss: dict[str, float]
    per_fold: list[dict]
    dataset_stats: str
    skipped_models: list[str]

    def to_dict(self) -> dict:
        return {
            "n_races": self.n_races,
            "n_folds": self.n_folds,
            "first_date": self.first_date.isoformat() if self.first_date else None,
            "last_date": self.last_date.isoformat() if self.last_date else None,
            "mean_log_loss": self.mean_log_loss,
            "per_fold": self.per_fold,
            "dataset_stats": self.dataset_stats,
            "skipped_models": self.skipped_models,
        }


def evaluate(
    session, *, start_date: dt.date, end_date: dt.date, min_train_months: int = 6
) -> EvaluationResult:
    data = build_dataset(session, start_date=start_date, end_date=end_date)
    if not len(data):
        raise ValueError("no usable races in the requested range")

    folds = generate_monthly_folds(data.dates, min_train_months=min_train_months)

    candidates = {"lane_prior": lane_prior_factory}
    skipped = []
    if _sklearn_available():
        candidates["logistic_cards"] = sklearn_logistic_factory()
    else:
        skipped.append("logistic_cards (scikit-learn not installed)")

    results = run_comparison(
        folds,
        data.dates,
        data.X,
        data.y,
        list(LANES),
        baseline_factory=uniform_factory,
        candidate_factories=candidates,
    )

    per_fold = []
    totals: dict[str, float] = {"uniform": 0.0}
    for fold, result in zip(folds, results):
        row = {
            "fold": result.fold_index,
            "test_month": fold.test_start.isoformat(),
            "train_races": len(fold.train_indices(data.dates)),
            "test_races": len(fold.test_indices(data.dates)),
            "uniform": result.baseline_log_loss,
        }
        totals["uniform"] += result.baseline_log_loss
        for name, loss in result.candidate_log_losses.items():
            row[name] = loss
            totals[name] = totals.get(name, 0.0) + loss
        per_fold.append(row)

    mean = {name: total / len(results) for name, total in totals.items()}
    return EvaluationResult(
        n_races=len(data),
        n_folds=len(folds),
        first_date=min(data.dates),
        last_date=max(data.dates),
        mean_log_loss=mean,
        per_fold=per_fold,
        dataset_stats=str(data.stats),
        skipped_models=skipped,
    )


def render(result: EvaluationResult) -> str:
    lines = [
        f"walk-forward P1 evaluation  {result.first_date} .. {result.last_date}",
        f"races={result.n_races}  folds={result.n_folds}",
        f"dataset: {result.dataset_stats}",
        "",
    ]
    names = [k for k in result.per_fold[0] if k not in ("fold", "test_month", "train_races", "test_races")]
    header = f"{'month':<10}{'train':>9}{'test':>8}" + "".join(f"{n:>18}" for n in names)
    lines.append(header)
    for row in result.per_fold:
        line = f"{row['test_month']:<10}{row['train_races']:>9}{row['test_races']:>8}"
        line += "".join(f"{row[n]:>18.5f}" for n in names)
        lines.append(line)
    lines.append("")
    lines.append("mean log-loss (lower is better):")
    for name, value in sorted(result.mean_log_loss.items(), key=lambda kv: kv[1]):
        lines.append(f"  {name:<18}{value:.5f}")
    baseline = result.mean_log_loss.get("uniform")
    if baseline:
        lines.append("")
        for name, value in sorted(result.mean_log_loss.items(), key=lambda kv: kv[1]):
            if name != "uniform":
                lines.append(f"  {name} vs uniform: {100 * (1 - value / baseline):+.2f}%")
    for skipped in result.skipped_models:
        lines.append(f"  [skipped] {skipped}")
    return "\n".join(lines)


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--start-date", type=dt.date.fromisoformat, required=True)
    parser.add_argument("--end-date", type=dt.date.fromisoformat, required=True)
    parser.add_argument("--min-train-months", type=int, default=6)
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args(argv)

    engine = create_db_engine(args.database_url)
    try:
        with create_session_factory(engine)() as session:
            result = evaluate(
                session,
                start_date=args.start_date,
                end_date=args.end_date,
                min_train_months=args.min_train_months,
            )
    finally:
        engine.dispose()

    print(render(result))
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(result.to_dict(), indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
        print(f"\nwrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
