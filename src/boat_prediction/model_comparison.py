"""LightGBM/CatBoost vs. baseline comparison harness (P1-T004).

Generic side-by-side comparison of a baseline model against one or more
candidate models over the *same* walk-forward folds
(`walk_forward.Fold`) and the *same* feature matrix
(docs/PROJECT_PROFILE.md: "Compare against simple baselines before
complex models"). A model is anything with `fit(X, y)` and
`predict_proba(X) -> rows of per-class probabilities` — this module has
no hard dependency on lightgbm/catboost. `lightgbm_model_factory()`/
`catboost_model_factory()` below are thin, lazily-importing adapters for
when those libraries (the `ml` extra) are installed; they raise a clear
`ComparisonError` otherwise instead of failing this module's import.

This module only *compares* models — it never combines them. There is no
ensembling/blending code here, and no fixed weight between models is
assumed anywhere.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from .metrics import multiclass_log_loss
from .walk_forward import Fold


class ComparisonError(ValueError):
    """Raised for invalid comparison inputs or an unavailable model library."""


class ProbabilisticClassifier(Protocol):
    def fit(self, X: Sequence[Sequence[float]], y: Sequence[int]) -> Any: ...
    def predict_proba(self, X: Sequence[Sequence[float]]) -> Sequence[Sequence[float]]: ...


ModelFactory = Callable[[], ProbabilisticClassifier]


@dataclass(frozen=True)
class FoldComparisonResult:
    fold_index: int
    baseline_log_loss: float
    candidate_log_losses: dict[str, float]

    def complexity_gain(self, candidate_name: str) -> float:
        """baseline_log_loss - candidate_log_loss for one candidate;
        positive means the candidate improved over the baseline on this
        fold's held-out test set."""
        return self.baseline_log_loss - self.candidate_log_losses[candidate_name]


def average_complexity_gain(results: list[FoldComparisonResult], candidate_name: str) -> float:
    if not results:
        raise ComparisonError("results must not be empty")
    return sum(r.complexity_gain(candidate_name) for r in results) / len(results)


def _take(X, indices: list[int]):
    """`X`'s rows at `indices`, for either a list of lists or a numpy
    array (including a read-only memmap, which fancy indexing copies into
    ordinary memory -- exactly the per-fold slice a worker needs)."""
    if hasattr(X, "take"):
        return X[indices]
    return [X[i] for i in indices]


def _one_fold(
    fold_index: int,
    X,
    y: Sequence[int],
    train_idx: list[int],
    test_idx: list[int],
    classes: Sequence[int],
    baseline_factory: ModelFactory,
    candidate_factories: dict[str, ModelFactory],
) -> FoldComparisonResult:
    """One fold's fits, slicing its own rows out of the shared `X`.

    Module level so a worker process can call it, and it takes indices
    rather than pre-cut slices so the parent hands joblib *one* array to
    memmap instead of one slice per fold. The factories are closures,
    which only survive the trip because joblib's loky backend pickles
    callables with cloudpickle.
    """
    X_train = _take(X, train_idx)
    y_train = [y[i] for i in train_idx]
    X_test = _take(X, test_idx)
    y_test = [y[i] for i in test_idx]

    baseline_model = baseline_factory()
    baseline_model.fit(X_train, y_train)
    baseline_loss = multiclass_log_loss(y_test, baseline_model.predict_proba(X_test), classes)

    candidate_losses: dict[str, float] = {}
    for name, factory in candidate_factories.items():
        model = factory()
        model.fit(X_train, y_train)
        candidate_losses[name] = multiclass_log_loss(y_test, model.predict_proba(X_test), classes)

    return FoldComparisonResult(
        fold_index=fold_index,
        baseline_log_loss=baseline_loss,
        candidate_log_losses=candidate_losses,
    )


def run_comparison(
    folds: list[Fold],
    dates: list,
    X: Sequence[Sequence[float]],
    y: Sequence[int],
    classes: Sequence[int],
    baseline_factory: ModelFactory,
    candidate_factories: dict[str, ModelFactory],
    n_jobs: int = 1,
) -> list[FoldComparisonResult]:
    """Fit and evaluate `baseline_factory` and every entry in
    `candidate_factories` on the identical train/test row indices for
    each fold, so any log-loss difference reflects the model only.

    `n_jobs` > 1 (or -1 for every core) runs the folds in worker
    processes. The folds are independent -- each fits from scratch on its
    own row indices and returns only its losses -- so this changes
    scheduling and nothing else; results are reassembled in fold order
    and are identical to the serial path. `n_jobs=1` stays on the
    original code path and requires neither numpy nor joblib.

    Two things make the parallel path worth its complexity, and both are
    easy to get wrong:

    - **The rows are shared, not copied.** `X` is converted to one numpy
      array so joblib memmaps it once and every worker maps the same
      file. Sent as a list of lists it would instead be pickled per
      task, and at the P1 window's size (198,264 x 54) that transfer
      costs more than the fits it is trying to overlap.
    - **Nested threads are pinned to one.** scikit-learn's BLAS is
      already threaded, so N processes x M threads oversubscribes the
      machine. Measured on the runtime host (12 cores, 31 expanding folds
      at the P1 window's shape) this costs nothing where it does not help
      and helps substantially in the middle of the range:

      | | pinned | unpinned |
      |---|---|---|
      | `n_jobs=10` | 3.6 s | 3.6 s |
      | `n_jobs=6` | 3.9 s | **6.0 s** |

      At `n_jobs=10` the process count alone saturates the machine, so
      the threads have nowhere to go either way; at 6 the unpinned run
      spawns ~72 threads for 12 cores and loses half its advantage.
      Pinning is also the better-behaved choice on a host shared with
      other tenants, which this one is.

    Expanding-window folds are not equal in size -- the last trains on
    the whole window and the first on a fraction of it -- so wall-clock
    is bounded by the largest fold, well short of a linear speed-up.
    The same measurement gives **21.4 s serial -> 3.6 s at `n_jobs=10`,
    5.9x on 12 cores**, with every fold's score identical to serial.
    """
    if not folds:
        raise ComparisonError("folds must not be empty")
    if not candidate_factories:
        raise ComparisonError("candidate_factories must not be empty")

    splits = []
    for fold in folds:
        train_idx = fold.train_indices(dates)
        test_idx = fold.test_indices(dates)
        if not train_idx or not test_idx:
            raise ComparisonError(f"fold {fold.fold_index} has an empty train or test split")
        splits.append((fold.fold_index, train_idx, test_idx))

    if n_jobs == 1:
        return [
            _one_fold(
                fold_index,
                X,
                y,
                train_idx,
                test_idx,
                classes,
                baseline_factory,
                candidate_factories,
            )
            for fold_index, train_idx, test_idx in splits
        ]

    try:
        import numpy as np
        from joblib import Parallel, delayed, parallel_config
    except ImportError as exc:
        raise ComparisonError(
            f"n_jobs={n_jobs} needs numpy and joblib; install the 'ml' extra or pass n_jobs=1"
        ) from exc

    # `y` stays a plain list: it is one small column, and the baselines
    # count winners with `collections.Counter`, which reads more clearly
    # over ints than over numpy scalars.
    X_array = np.asarray(X, dtype=np.float64)
    y_list = list(y)
    with parallel_config(backend="loky", inner_max_num_threads=1):
        return list(
            Parallel(n_jobs=n_jobs)(
                delayed(_one_fold)(
                    fold_index,
                    X_array,
                    y_list,
                    train_idx,
                    test_idx,
                    classes,
                    baseline_factory,
                    candidate_factories,
                )
                for fold_index, train_idx, test_idx in splits
            )
        )


def lightgbm_model_factory(**params: Any) -> ModelFactory:
    def factory() -> ProbabilisticClassifier:
        try:
            from lightgbm import LGBMClassifier
        except ImportError as exc:
            raise ComparisonError(
                "lightgbm is not installed; install the 'ml' extra to use this adapter"
            ) from exc
        return LGBMClassifier(**params)

    return factory


def catboost_model_factory(**params: Any) -> ModelFactory:
    def factory() -> ProbabilisticClassifier:
        try:
            from catboost import CatBoostClassifier
        except ImportError as exc:
            raise ComparisonError(
                "catboost is not installed; install the 'ml' extra to use this adapter"
            ) from exc
        return CatBoostClassifier(verbose=False, **params)

    return factory
