import unittest
import unittest.mock
from datetime import date

from boat_prediction.model_comparison import (
    ComparisonError,
    average_complexity_gain,
    catboost_model_factory,
    lightgbm_model_factory,
    run_comparison,
)
from boat_prediction.walk_forward import Fold, generate_monthly_folds


class ConstantProbClassifier:
    """Always predicts a fixed uniform distribution, ignoring X and y."""

    def __init__(self, classes: list[int]) -> None:
        self._classes = classes

    def fit(self, X, y):
        return self

    def predict_proba(self, X):
        row = [1 / len(self._classes)] * len(self._classes)
        return [row for _ in X]


class FeatureLookupClassifier:
    """Deterministic stand-in for a 'smarter' model: memorizes the label
    counts per exact feature value, Laplace-smoothed."""

    def __init__(self, classes: list[int]) -> None:
        self._classes = classes
        self._counts: dict[float, dict[int, int]] = {}

    def fit(self, X, y):
        self._counts = {}
        for row, label in zip(X, y):
            bucket = self._counts.setdefault(row[0], {c: 0 for c in self._classes})
            bucket[label] += 1
        return self

    def predict_proba(self, X):
        rows = []
        for row in X:
            bucket = self._counts.get(row[0], {c: 0 for c in self._classes})
            total = sum(bucket.values()) + len(self._classes)
            rows.append([(bucket[c] + 1) / total for c in self._classes])
        return rows


def _synthetic_dataset():
    """3 months of data where the single feature fully determines the label."""
    dates, X, y = [], [], []
    for month in (1, 2, 3):
        for day in (1, 10, 20):
            for feature_value, label in ((0, 0), (1, 1)):
                dates.append(date(2026, month, day))
                X.append([feature_value])
                y.append(label)
    return dates, X, y


class RunComparisonTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dates, self.X, self.y = _synthetic_dataset()
        self.folds = generate_monthly_folds(self.dates, min_train_months=1)
        self.classes = [0, 1]

    def test_rejects_empty_folds(self) -> None:
        with self.assertRaises(ComparisonError):
            run_comparison(
                [],
                self.dates,
                self.X,
                self.y,
                self.classes,
                baseline_factory=lambda: ConstantProbClassifier(self.classes),
                candidate_factories={"lookup": lambda: FeatureLookupClassifier(self.classes)},
            )

    def test_rejects_no_candidates(self) -> None:
        with self.assertRaises(ComparisonError):
            run_comparison(
                self.folds,
                self.dates,
                self.X,
                self.y,
                self.classes,
                baseline_factory=lambda: ConstantProbClassifier(self.classes),
                candidate_factories={},
            )

    def test_baseline_and_candidate_see_identical_train_sizes_per_fold(self) -> None:
        seen_train_sizes: list[int] = []

        class SpyClassifier(ConstantProbClassifier):
            def fit(self, X, y):
                seen_train_sizes.append(len(X))
                return super().fit(X, y)

        run_comparison(
            self.folds,
            self.dates,
            self.X,
            self.y,
            self.classes,
            baseline_factory=lambda: SpyClassifier(self.classes),
            candidate_factories={"spy_candidate": lambda: SpyClassifier(self.classes)},
        )

        # per fold: [baseline_train_size, candidate_train_size] must match
        for i in range(0, len(seen_train_sizes), 2):
            self.assertEqual(seen_train_sizes[i], seen_train_sizes[i + 1])

    def test_complexity_gain_is_positive_when_candidate_learns_the_feature(self) -> None:
        results = run_comparison(
            self.folds,
            self.dates,
            self.X,
            self.y,
            self.classes,
            baseline_factory=lambda: ConstantProbClassifier(self.classes),
            candidate_factories={"lookup": lambda: FeatureLookupClassifier(self.classes)},
        )

        for result in results:
            self.assertGreater(result.complexity_gain("lookup"), 0)

    def test_average_complexity_gain_aggregates_across_folds(self) -> None:
        results = run_comparison(
            self.folds,
            self.dates,
            self.X,
            self.y,
            self.classes,
            baseline_factory=lambda: ConstantProbClassifier(self.classes),
            candidate_factories={"lookup": lambda: FeatureLookupClassifier(self.classes)},
        )

        self.assertGreater(average_complexity_gain(results, "lookup"), 0)

    def test_average_complexity_gain_rejects_empty_results(self) -> None:
        with self.assertRaises(ComparisonError):
            average_complexity_gain([], "lookup")

    def test_fold_with_empty_test_split_is_rejected(self) -> None:
        bogus_fold = Fold(
            fold_index=0,
            train_start=date(2026, 1, 1),
            train_end=date(2026, 2, 1),
            test_start=date(2030, 1, 1),
            test_end=date(2030, 2, 1),
        )

        with self.assertRaises(ComparisonError):
            run_comparison(
                [bogus_fold],
                self.dates,
                self.X,
                self.y,
                self.classes,
                baseline_factory=lambda: ConstantProbClassifier(self.classes),
                candidate_factories={"lookup": lambda: FeatureLookupClassifier(self.classes)},
            )


class LightgbmAdapterTest(unittest.TestCase):
    def test_factory_returns_working_model_or_a_clear_error(self) -> None:
        factory = lightgbm_model_factory(n_estimators=5, max_depth=2, verbosity=-1)
        try:
            import lightgbm  # noqa: F401
        except ImportError:
            with self.assertRaises(ComparisonError):
                factory()
        else:
            model = factory()
            self.assertTrue(hasattr(model, "fit"))
            self.assertTrue(hasattr(model, "predict_proba"))

    def test_end_to_end_comparison_with_real_lightgbm(self) -> None:
        try:
            import lightgbm  # noqa: F401
        except ImportError:
            self.skipTest("lightgbm not installed")

        dates, X, y = _synthetic_dataset()
        folds = generate_monthly_folds(dates, min_train_months=1)

        results = run_comparison(
            folds,
            dates,
            X,
            y,
            [0, 1],
            baseline_factory=lambda: ConstantProbClassifier([0, 1]),
            candidate_factories={
                "lightgbm": lightgbm_model_factory(
                    n_estimators=10, max_depth=2, min_child_samples=1, verbosity=-1
                )
            },
        )

        self.assertEqual(len(results), len(folds))


class CatboostAdapterTest(unittest.TestCase):
    def test_factory_returns_working_model_or_a_clear_error(self) -> None:
        factory = catboost_model_factory(iterations=5, depth=2)
        try:
            import catboost  # noqa: F401
        except ImportError:
            with self.assertRaises(ComparisonError):
                factory()
        else:
            model = factory()
            self.assertTrue(hasattr(model, "fit"))
            self.assertTrue(hasattr(model, "predict_proba"))

    def test_end_to_end_comparison_with_real_catboost(self) -> None:
        try:
            import catboost  # noqa: F401
        except ImportError:
            self.skipTest("catboost not installed")

        dates, X, y = _synthetic_dataset()
        folds = generate_monthly_folds(dates, min_train_months=1)

        results = run_comparison(
            folds,
            dates,
            X,
            y,
            [0, 1],
            baseline_factory=lambda: ConstantProbClassifier([0, 1]),
            candidate_factories={"catboost": catboost_model_factory(iterations=10, depth=2)},
        )

        self.assertEqual(len(results), len(folds))


if __name__ == "__main__":
    unittest.main()


def _constant_factory(classes):
    """Top-level so a worker process can rebuild it.

    The `lambda: ConstantProbClassifier(...)` form the serial tests use
    works under loky too -- joblib pickles callables with cloudpickle --
    but naming these makes the parallel tests independent of that detail
    rather than quietly depending on it.
    """
    return lambda: ConstantProbClassifier(classes)


def _lookup_factory(classes):
    return lambda: FeatureLookupClassifier(classes)


class RunComparisonParallelTest(unittest.TestCase):
    """The folds are independent, so `n_jobs` may change scheduling and
    nothing else. That is the property worth pinning: a parallel run that
    produced *slightly* different numbers would invalidate every figure
    recorded against the serial path."""

    def setUp(self) -> None:
        self.dates, self.X, self.y = _synthetic_dataset()
        self.folds = generate_monthly_folds(self.dates, min_train_months=1)
        self.classes = [0, 1]

    def _run(self, n_jobs: int):
        return run_comparison(
            self.folds,
            self.dates,
            self.X,
            self.y,
            self.classes,
            baseline_factory=_constant_factory(self.classes),
            candidate_factories={"lookup": _lookup_factory(self.classes)},
            n_jobs=n_jobs,
        )

    def test_parallel_matches_serial_exactly(self) -> None:
        serial = self._run(1)
        parallel = self._run(2)
        self.assertEqual(len(serial), len(parallel))
        for a, b in zip(serial, parallel):
            self.assertEqual(a.fold_index, b.fold_index)
            self.assertEqual(a.baseline_log_loss, b.baseline_log_loss)
            self.assertEqual(a.candidate_log_losses, b.candidate_log_losses)

    def test_results_come_back_in_fold_order(self) -> None:
        parallel = self._run(2)
        self.assertEqual([r.fold_index for r in parallel], [f.fold_index for f in self.folds])

    def test_an_empty_split_is_rejected_before_any_worker_starts(self) -> None:
        """Validation happens in the parent, so a bad fold fails the same
        way whether or not workers were going to be used."""
        bad = [
            Fold(
                fold_index=0,
                train_start=date(2099, 1, 1),
                train_end=date(2099, 1, 31),
                test_start=date(2099, 2, 1),
                test_end=date(2099, 2, 28),
            )
        ]
        for n_jobs in (1, 2):
            with self.subTest(n_jobs=n_jobs), self.assertRaises(ComparisonError):
                run_comparison(
                    bad,
                    self.dates,
                    self.X,
                    self.y,
                    self.classes,
                    baseline_factory=_constant_factory(self.classes),
                    candidate_factories={"lookup": _lookup_factory(self.classes)},
                    n_jobs=n_jobs,
                )

    def test_missing_numpy_or_joblib_is_a_clear_error_not_an_import_crash(self) -> None:
        import builtins

        real_import = builtins.__import__

        def refuse(name, *args, **kwargs):
            if name in ("numpy", "joblib"):
                raise ImportError(f"no {name}")
            return real_import(name, *args, **kwargs)

        with (
            unittest.mock.patch.object(builtins, "__import__", refuse),
            self.assertRaises(ComparisonError) as ctx,
        ):
            self._run(2)
        self.assertIn("n_jobs", str(ctx.exception))
