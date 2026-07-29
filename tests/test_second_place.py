import unittest
from datetime import date

from boat_prediction.second_place import (
    ConditionalSecondPlaceModel,
    SecondPlaceError,
    evaluate_with_folds,
)
from boat_prediction.walk_forward import generate_monthly_folds

# Lane 1 winning, lane 2 disproportionately often finishes second.
SAMPLE_OBSERVATIONS = [
    (1, 2),
    (1, 2),
    (1, 2),
    (1, 3),
    (1, 4),
    (2, 1),
    (2, 3),
]


class ConditionalSecondPlaceModelTest(unittest.TestCase):
    def test_predict_before_fit_raises(self) -> None:
        with self.assertRaises(SecondPlaceError):
            ConditionalSecondPlaceModel().predict(1)

    def test_rejects_first_equals_second_in_observations(self) -> None:
        with self.assertRaises(SecondPlaceError):
            ConditionalSecondPlaceModel().fit([(1, 1)])

    def test_rejects_lane_out_of_range(self) -> None:
        with self.assertRaises(SecondPlaceError):
            ConditionalSecondPlaceModel().fit([(1, 7)])

    def test_probability_of_first_place_repeating_is_always_exactly_zero(self) -> None:
        model = ConditionalSecondPlaceModel().fit(SAMPLE_OBSERVATIONS)
        probs = model.predict(1)
        self.assertEqual(probs[1], 0.0)

    def test_remaining_probabilities_sum_to_one(self) -> None:
        model = ConditionalSecondPlaceModel().fit(SAMPLE_OBSERVATIONS)
        probs = model.predict(1)
        self.assertAlmostEqual(sum(probs.values()), 1.0)

    def test_predict_reflects_observed_conditional_frequency(self) -> None:
        model = ConditionalSecondPlaceModel().fit(SAMPLE_OBSERVATIONS)
        probs = model.predict(1)
        self.assertGreater(probs[2], probs[4])  # lane 2 seconds far more often after lane 1 wins

    def test_unseen_second_place_gets_nonzero_probability_via_smoothing(self) -> None:
        observations_without_5_or_6 = [(1, 2), (1, 3)]
        model = ConditionalSecondPlaceModel().fit(observations_without_5_or_6)
        probs = model.predict(1)
        self.assertGreater(probs[5], 0.0)
        self.assertGreater(probs[6], 0.0)

    def test_predict_for_a_first_place_lane_with_no_observations_falls_back_to_uniform_over_others(
        self,
    ) -> None:
        model = ConditionalSecondPlaceModel().fit([(1, 2)])
        probs = model.predict(6)  # lane 6 never observed as a winner
        others = [probs[j] for j in probs if j != 6]
        self.assertTrue(all(abs(p - others[0]) < 1e-9 for p in others))

    def test_fit_is_deterministic(self) -> None:
        first = ConditionalSecondPlaceModel().fit(SAMPLE_OBSERVATIONS).predict(1)
        second = ConditionalSecondPlaceModel().fit(SAMPLE_OBSERVATIONS).predict(1)
        self.assertEqual(first, second)


class EvaluateWithFoldsTest(unittest.TestCase):
    def _dataset(self):
        dates, observations = [], []
        for month in (1, 2, 3):
            for day in (1, 10, 20):
                dates.append(date(2026, month, day))
                observations.append((1, 2))
        return dates, observations

    def test_rejects_empty_folds(self) -> None:
        with self.assertRaises(SecondPlaceError):
            evaluate_with_folds([], [], [])

    def test_uses_walk_forward_folds_and_reports_one_result_per_fold(self) -> None:
        dates, observations = self._dataset()
        folds = generate_monthly_folds(dates, min_train_months=1)

        results = evaluate_with_folds(folds, dates, observations)

        self.assertEqual(len(results), len(folds))
        for result in results:
            self.assertIn("fold_index", result)
            self.assertIn("n_test", result)
            self.assertGreaterEqual(result["log_loss"], 0)

    def test_rejects_fold_with_empty_test_split(self) -> None:
        from boat_prediction.walk_forward import Fold

        dates, observations = self._dataset()
        bogus_fold = Fold(
            fold_index=0,
            train_start=date(2026, 1, 1),
            train_end=date(2026, 2, 1),
            test_start=date(2030, 1, 1),
            test_end=date(2030, 2, 1),
        )

        with self.assertRaises(SecondPlaceError):
            evaluate_with_folds([bogus_fold], dates, observations)


if __name__ == "__main__":
    unittest.main()
