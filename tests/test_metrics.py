import math
import unittest

from boat_prediction.metrics import MetricsError, multiclass_brier_score, multiclass_log_loss


class MulticlassLogLossTest(unittest.TestCase):
    def test_matches_hand_computed_value_for_uniform_predictions(self) -> None:
        probs = [[1 / 3, 1 / 3, 1 / 3]] * 3
        loss = multiclass_log_loss([0, 1, 2], probs, classes=[0, 1, 2])
        self.assertAlmostEqual(loss, -math.log(1 / 3))

    def test_rejects_empty_input(self) -> None:
        with self.assertRaises(MetricsError):
            multiclass_log_loss([], [], classes=[0, 1])


class MulticlassBrierScoreTest(unittest.TestCase):
    def test_zero_for_perfect_confident_predictions(self) -> None:
        probs = [[1.0, 0.0], [0.0, 1.0]]
        score = multiclass_brier_score([0, 1], probs, classes=[0, 1])
        self.assertAlmostEqual(score, 0.0)

    def test_matches_hand_computed_value_for_uniform_predictions(self) -> None:
        probs = [[0.5, 0.5]]
        # winner=0: (0.5-1)^2 + (0.5-0)^2 = 0.25 + 0.25 = 0.5
        score = multiclass_brier_score([0], probs, classes=[0, 1])
        self.assertAlmostEqual(score, 0.5)

    def test_rejects_empty_input(self) -> None:
        with self.assertRaises(MetricsError):
            multiclass_brier_score([], [], classes=[0, 1])


if __name__ == "__main__":
    unittest.main()
