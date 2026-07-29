import json
import math
import tempfile
import unittest
from pathlib import Path

from boat_prediction.baseline import (
    BaselineError,
    LanePriorBaseline,
    UniformBaseline,
    evaluate,
    write_metrics_report,
)

# Lane 1 wins disproportionately often, matching real inside-lane advantage.
SAMPLE_WINNERS = [1, 1, 1, 1, 2, 2, 3, 4, 5, 6]


class UniformBaselineTest(unittest.TestCase):
    def test_predictions_sum_to_one_and_are_equal(self) -> None:
        probs = UniformBaseline().predict()

        self.assertAlmostEqual(sum(probs.values()), 1.0)
        self.assertEqual(set(probs), {1, 2, 3, 4, 5, 6})
        self.assertTrue(all(math.isclose(p, 1 / 6) for p in probs.values()))


class LanePriorBaselineTest(unittest.TestCase):
    def test_predict_before_fit_raises(self) -> None:
        with self.assertRaises(BaselineError):
            LanePriorBaseline().predict()

    def test_fit_and_predict_sums_to_one(self) -> None:
        probs = LanePriorBaseline().fit(SAMPLE_WINNERS).predict()
        self.assertAlmostEqual(sum(probs.values()), 1.0)

    def test_more_frequent_winner_gets_higher_probability(self) -> None:
        probs = LanePriorBaseline().fit(SAMPLE_WINNERS).predict()
        self.assertGreater(probs[1], probs[6])

    def test_unseen_lane_still_gets_nonzero_probability_via_smoothing(self) -> None:
        winners_without_lane_6 = [1, 1, 2, 3, 4, 5]
        probs = LanePriorBaseline().fit(winners_without_lane_6).predict()
        self.assertGreater(probs[6], 0.0)

    def test_rejects_out_of_range_winner(self) -> None:
        with self.assertRaises(BaselineError):
            LanePriorBaseline().fit([1, 2, 7])

    def test_fitting_twice_on_the_same_data_is_reproducible(self) -> None:
        first = LanePriorBaseline().fit(SAMPLE_WINNERS).predict()
        second = LanePriorBaseline().fit(SAMPLE_WINNERS).predict()
        self.assertEqual(first, second)


class EvaluateTest(unittest.TestCase):
    def test_perfect_prediction_has_zero_log_loss_and_full_accuracy(self) -> None:
        # avoid literal 0.0 for the other lanes (that would make log loss -inf
        # on any misclassified case); near-zero is enough for this check.
        probs = {1: 0.999995, 2: 0.000001, 3: 0.000001, 4: 0.000001, 5: 0.000001, 6: 0.000001}

        metrics = evaluate(probs, actual_winners=[1, 1, 1])

        self.assertAlmostEqual(metrics.top1_accuracy, 1.0)
        self.assertLess(metrics.log_loss, 0.01)
        self.assertEqual(metrics.n_races, 3)

    def test_uniform_baseline_log_loss_matches_hand_computed_value(self) -> None:
        probs = UniformBaseline().predict()

        metrics = evaluate(probs, actual_winners=[1, 2, 3])

        self.assertAlmostEqual(metrics.log_loss, -math.log(1 / 6))

    def test_rejects_empty_actual_winners(self) -> None:
        with self.assertRaises(BaselineError):
            evaluate(UniformBaseline().predict(), actual_winners=[])

    def test_rejects_invalid_probability_distribution(self) -> None:
        with self.assertRaises(BaselineError):
            evaluate({1: 0.5, 2: 0.5}, actual_winners=[1])


class WriteMetricsReportTest(unittest.TestCase):
    def test_persists_metrics_as_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "exports" / "baseline_metrics.json"
            metrics = evaluate(UniformBaseline().predict(), actual_winners=[1, 2, 3])

            write_metrics_report(metrics, path)

            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), metrics.to_dict())


if __name__ == "__main__":
    unittest.main()
