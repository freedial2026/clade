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


def run_comparison(
    folds: list[Fold],
    dates: list,
    X: Sequence[Sequence[float]],
    y: Sequence[int],
    classes: Sequence[int],
    baseline_factory: ModelFactory,
    candidate_factories: dict[str, ModelFactory],
) -> list[FoldComparisonResult]:
    """Fit and evaluate `baseline_factory` and every entry in
    `candidate_factories` on the identical train/test row indices for
    each fold, so any log-loss difference reflects the model only."""
    if not folds:
        raise ComparisonError("folds must not be empty")
    if not candidate_factories:
        raise ComparisonError("candidate_factories must not be empty")

    results = []
    for fold in folds:
        train_idx = fold.train_indices(dates)
        test_idx = fold.test_indices(dates)
        if not train_idx or not test_idx:
            raise ComparisonError(f"fold {fold.fold_index} has an empty train or test split")

        X_train = [X[i] for i in train_idx]
        y_train = [y[i] for i in train_idx]
        X_test = [X[i] for i in test_idx]
        y_test = [y[i] for i in test_idx]

        baseline_model = baseline_factory()
        baseline_model.fit(X_train, y_train)
        baseline_loss = multiclass_log_loss(y_test, baseline_model.predict_proba(X_test), classes)

        candidate_losses: dict[str, float] = {}
        for name, factory in candidate_factories.items():
            model = factory()
            model.fit(X_train, y_train)
            candidate_losses[name] = multiclass_log_loss(
                y_test, model.predict_proba(X_test), classes
            )

        results.append(
            FoldComparisonResult(
                fold_index=fold.fold_index,
                baseline_log_loss=baseline_loss,
                candidate_log_losses=candidate_losses,
            )
        )
    return results


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
