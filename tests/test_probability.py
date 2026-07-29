import unittest

from boat_prediction.probability import validate_probability_distribution


class LocalError(ValueError):
    pass


class ValidateProbabilityDistributionTest(unittest.TestCase):
    def test_accepts_a_valid_distribution(self) -> None:
        validate_probability_distribution({1: 0.5, 2: 0.5}, [1, 2], error_type=LocalError)

    def test_rejects_missing_key(self) -> None:
        with self.assertRaises(LocalError):
            validate_probability_distribution({1: 1.0}, [1, 2], error_type=LocalError)

    def test_rejects_extra_key(self) -> None:
        with self.assertRaises(LocalError):
            validate_probability_distribution({1: 0.5, 2: 0.5, 3: 0.0}, [1, 2], error_type=LocalError)

    def test_rejects_value_above_one(self) -> None:
        with self.assertRaises(LocalError):
            validate_probability_distribution({1: 1.5, 2: -0.5}, [1, 2], error_type=LocalError)

    def test_rejects_negative_value(self) -> None:
        with self.assertRaises(LocalError):
            validate_probability_distribution({1: -0.5, 2: 1.5}, [1, 2], error_type=LocalError)

    def test_rejects_sum_not_equal_to_one(self) -> None:
        with self.assertRaises(LocalError):
            validate_probability_distribution({1: 0.3, 2: 0.3}, [1, 2], error_type=LocalError)

    def test_zero_at_requires_that_key_to_be_exactly_zero(self) -> None:
        with self.assertRaises(LocalError):
            validate_probability_distribution(
                {1: 0.1, 2: 0.9}, [1, 2], error_type=LocalError, zero_at=1
            )

    def test_zero_at_passes_when_that_key_is_exactly_zero(self) -> None:
        validate_probability_distribution(
            {1: 0.0, 2: 1.0}, [1, 2], error_type=LocalError, zero_at=1
        )

    def test_custom_tolerance_allows_a_small_sum_deviation(self) -> None:
        validate_probability_distribution(
            {1: 0.5, 2: 0.5001}, [1, 2], error_type=LocalError, tolerance=1e-3
        )


if __name__ == "__main__":
    unittest.main()
