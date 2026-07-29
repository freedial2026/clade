import unittest

from boat_prediction.exacta import (
    ALL_COMBINATIONS,
    ExactaError,
    construct_exacta_probabilities,
    decode_combination,
    encode_combination,
    evaluate_exacta_calibration,
)
from boat_prediction.second_place import ConditionalSecondPlaceModel

SAMPLE_OBSERVATIONS = [(1, 2), (1, 2), (1, 3), (2, 1), (2, 3), (3, 1)]

UNIFORM_FIRST_PLACE = {lane: 1 / 6 for lane in range(1, 7)}


class ScopeBoundaryTest(unittest.TestCase):
    def test_exactly_30_combinations_no_trifecta(self) -> None:
        # 6*5 = 30 exacta combinations; a trifecta would be 6*5*4 = 120.
        self.assertEqual(len(ALL_COMBINATIONS), 30)


class EncodeDecodeCombinationTest(unittest.TestCase):
    def test_round_trips(self) -> None:
        code = encode_combination(2, 5)
        self.assertEqual(decode_combination(code), (2, 5))

    def test_rejects_same_lane(self) -> None:
        with self.assertRaises(ExactaError):
            encode_combination(3, 3)

    def test_rejects_lane_out_of_range(self) -> None:
        with self.assertRaises(ExactaError):
            encode_combination(1, 7)

    def test_decode_rejects_invalid_code(self) -> None:
        with self.assertRaises(ExactaError):
            decode_combination(11)  # first == second, never a valid combination


class ConstructExactaProbabilitiesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.model = ConditionalSecondPlaceModel().fit(SAMPLE_OBSERVATIONS)

    def test_sums_to_one(self) -> None:
        probs = construct_exacta_probabilities(UNIFORM_FIRST_PLACE, self.model)
        self.assertAlmostEqual(sum(probs.values()), 1.0)

    def test_covers_exactly_the_30_combinations(self) -> None:
        probs = construct_exacta_probabilities(UNIFORM_FIRST_PLACE, self.model)
        self.assertEqual(set(probs), set(ALL_COMBINATIONS))

    def test_marginal_over_second_place_recovers_first_place_distribution(self) -> None:
        skewed_first_place = {1: 0.5, 2: 0.1, 3: 0.1, 4: 0.1, 5: 0.1, 6: 0.1}
        probs = construct_exacta_probabilities(skewed_first_place, self.model)

        for first in range(1, 7):
            marginal = sum(p for code, p in probs.items() if code // 10 == first)
            self.assertAlmostEqual(marginal, skewed_first_place[first])

    def test_rejects_incomplete_first_place_distribution(self) -> None:
        incomplete = {1: 0.5, 2: 0.5}
        with self.assertRaises(ExactaError):
            construct_exacta_probabilities(incomplete, self.model)

    def test_rejects_incoherent_conditional_model(self) -> None:
        class BrokenModel:
            def predict(self, first_place: int) -> dict:
                # deliberately does not sum to 1 -- an incoherent conditional
                return {j: 0.5 for j in range(1, 7) if j != first_place}

        with self.assertRaises(ExactaError):
            construct_exacta_probabilities(UNIFORM_FIRST_PLACE, BrokenModel())


class EvaluateExactaCalibrationTest(unittest.TestCase):
    def setUp(self) -> None:
        model = ConditionalSecondPlaceModel().fit(SAMPLE_OBSERVATIONS)
        self.probs = construct_exacta_probabilities(UNIFORM_FIRST_PLACE, model)

    def test_returns_a_report_with_expected_sample_count(self) -> None:
        actual = [encode_combination(1, 2), encode_combination(2, 1)]
        report = evaluate_exacta_calibration(actual, self.probs)
        self.assertEqual(report.n_samples, 2)
        self.assertGreater(report.log_loss, 0)

    def test_rejects_empty_actual_combinations(self) -> None:
        with self.assertRaises(ExactaError):
            evaluate_exacta_calibration([], self.probs)


if __name__ == "__main__":
    unittest.main()
