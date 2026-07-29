import math
import unittest

from boat_prediction.entry_course import (
    EntryCourseError,
    EntryCoursePrediction,
    entry_course_abstention_reason,
    is_uncertain,
)

UNIFORM = {c: 1 / 6 for c in range(1, 7)}


def _confident(course: int) -> dict:
    probs = {c: 0.002 for c in range(1, 7)}
    probs[course] = 1.0 - 0.002 * 5
    return probs


class EntryCoursePredictionTest(unittest.TestCase):
    def test_rejects_invalid_lane_number(self) -> None:
        with self.assertRaises(EntryCourseError):
            EntryCoursePrediction(lane_number=7, course_probabilities=UNIFORM)

    def test_rejects_distribution_not_summing_to_one(self) -> None:
        bad = dict(UNIFORM)
        bad[1] = 0.5
        with self.assertRaises(EntryCourseError):
            EntryCoursePrediction(lane_number=1, course_probabilities=bad)

    def test_rejects_distribution_missing_a_course(self) -> None:
        incomplete = {c: 1 / 5 for c in range(1, 6)}
        with self.assertRaises(EntryCourseError):
            EntryCoursePrediction(lane_number=1, course_probabilities=incomplete)

    def test_expected_course_is_the_argmax(self) -> None:
        prediction = EntryCoursePrediction(lane_number=1, course_probabilities=_confident(3))
        self.assertEqual(prediction.expected_course, 3)

    def test_changed_from_lane_true_when_argmax_differs(self) -> None:
        prediction = EntryCoursePrediction(lane_number=1, course_probabilities=_confident(4))
        self.assertTrue(prediction.changed_from_lane)

    def test_changed_from_lane_false_when_argmax_matches(self) -> None:
        prediction = EntryCoursePrediction(lane_number=1, course_probabilities=_confident(1))
        self.assertFalse(prediction.changed_from_lane)

    def test_entropy_is_near_zero_for_a_confident_prediction(self) -> None:
        prediction = EntryCoursePrediction(lane_number=1, course_probabilities=_confident(1))
        self.assertLess(prediction.entropy_bits, 0.15)

    def test_entropy_is_maximal_for_uniform_distribution(self) -> None:
        prediction = EntryCoursePrediction(lane_number=1, course_probabilities=UNIFORM)
        self.assertAlmostEqual(prediction.entropy_bits, math.log2(6), places=5)


class IsUncertainTest(unittest.TestCase):
    def test_uniform_distribution_is_uncertain(self) -> None:
        prediction = EntryCoursePrediction(lane_number=1, course_probabilities=UNIFORM)
        self.assertTrue(is_uncertain(prediction))

    def test_confident_distribution_is_not_uncertain(self) -> None:
        prediction = EntryCoursePrediction(lane_number=1, course_probabilities=_confident(1))
        self.assertFalse(is_uncertain(prediction))

    def test_threshold_is_caller_supplied_not_hardcoded(self) -> None:
        prediction = EntryCoursePrediction(lane_number=1, course_probabilities=_confident(1))
        # a near-zero threshold makes even a confident prediction "uncertain"
        self.assertTrue(is_uncertain(prediction, entropy_threshold_bits=0.0001))


class EntryCourseAbstentionReasonTest(unittest.TestCase):
    def test_returns_rc_entry_change_when_uncertain(self) -> None:
        prediction = EntryCoursePrediction(lane_number=1, course_probabilities=UNIFORM)
        self.assertEqual(entry_course_abstention_reason(prediction), "RC_ENTRY_CHANGE")

    def test_returns_none_when_confident(self) -> None:
        prediction = EntryCoursePrediction(lane_number=1, course_probabilities=_confident(1))
        self.assertIsNone(entry_course_abstention_reason(prediction))

    def test_reason_code_is_a_documented_abstention_reason(self) -> None:
        from boat_prediction.abstention import REASON_CODES

        self.assertIn("RC_ENTRY_CHANGE", REASON_CODES)


if __name__ == "__main__":
    unittest.main()
