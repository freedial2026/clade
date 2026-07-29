import math
import unittest

from boat_prediction.expected_value import (
    UNCERTAINTY_MARGIN_FORMULA_VERSION,
    ExpectedValueError,
    compute_conservative_ev,
    uncertainty_margin,
)


class UncertaintyMarginTest(unittest.TestCase):
    def test_matches_hand_computed_value(self) -> None:
        margin = uncertainty_margin(0.05, n_samples=100, model_std=0.02)
        expected = 0.05 + 1.0 / math.sqrt(100) + 1.0 * 0.02
        self.assertAlmostEqual(margin, expected)

    def test_shrinks_as_sample_size_grows(self) -> None:
        small_n = uncertainty_margin(0.05, n_samples=10)
        large_n = uncertainty_margin(0.05, n_samples=10_000)
        self.assertGreater(small_n, large_n)

    def test_grows_with_calibration_error(self) -> None:
        low_error = uncertainty_margin(0.01, n_samples=100)
        high_error = uncertainty_margin(0.20, n_samples=100)
        self.assertGreater(high_error, low_error)

    def test_grows_with_model_std(self) -> None:
        low_std = uncertainty_margin(0.05, n_samples=100, model_std=0.0)
        high_std = uncertainty_margin(0.05, n_samples=100, model_std=0.1)
        self.assertGreater(high_std, low_std)

    def test_custom_weights_change_the_margin(self) -> None:
        default_weights = uncertainty_margin(0.05, n_samples=100, model_std=0.05)
        custom_weights = uncertainty_margin(
            0.05, n_samples=100, model_std=0.05, sample_size_weight=0.1, variance_weight=5.0
        )
        self.assertNotEqual(default_weights, custom_weights)

    def test_rejects_negative_calibration_error(self) -> None:
        with self.assertRaises(ExpectedValueError):
            uncertainty_margin(-0.01, n_samples=100)

    def test_rejects_non_positive_sample_size(self) -> None:
        with self.assertRaises(ExpectedValueError):
            uncertainty_margin(0.05, n_samples=0)

    def test_rejects_negative_model_std(self) -> None:
        with self.assertRaises(ExpectedValueError):
            uncertainty_margin(0.05, n_samples=100, model_std=-0.1)


class ComputeConservativeEvTest(unittest.TestCase):
    def test_retains_both_raw_and_conservative_values(self) -> None:
        report = compute_conservative_ev(
            calibrated_probability=0.4, odds=3.0, calibration_error=0.05, n_samples=100
        )

        self.assertEqual(report.raw_probability, 0.4)
        self.assertAlmostEqual(report.raw_ev, 0.4 * 3.0)
        self.assertLess(report.conservative_probability, report.raw_probability)
        self.assertLess(report.conservative_ev, report.raw_ev)

    def test_conservative_ev_equals_conservative_probability_times_odds(self) -> None:
        report = compute_conservative_ev(
            calibrated_probability=0.4, odds=3.0, calibration_error=0.05, n_samples=100
        )
        self.assertAlmostEqual(report.conservative_ev, report.conservative_probability * 3.0)

    def test_conservative_probability_is_floored_at_zero(self) -> None:
        report = compute_conservative_ev(
            calibrated_probability=0.05,
            odds=10.0,
            calibration_error=0.5,  # huge margin relative to a small probability
            n_samples=10,
        )
        self.assertEqual(report.conservative_probability, 0.0)
        self.assertEqual(report.conservative_ev, 0.0)

    def test_records_the_formula_version(self) -> None:
        report = compute_conservative_ev(
            calibrated_probability=0.4, odds=3.0, calibration_error=0.05, n_samples=100
        )
        self.assertEqual(report.formula_version, UNCERTAINTY_MARGIN_FORMULA_VERSION)

    def test_to_dict_contains_all_fields(self) -> None:
        report = compute_conservative_ev(
            calibrated_probability=0.4, odds=3.0, calibration_error=0.05, n_samples=100
        )
        self.assertEqual(
            set(report.to_dict()),
            {
                "raw_probability",
                "conservative_probability",
                "odds",
                "raw_ev",
                "conservative_ev",
                "uncertainty_margin",
                "formula_version",
            },
        )

    def test_rejects_probability_out_of_range(self) -> None:
        with self.assertRaises(ExpectedValueError):
            compute_conservative_ev(
                calibrated_probability=1.5, odds=3.0, calibration_error=0.05, n_samples=100
            )

    def test_rejects_non_positive_odds(self) -> None:
        with self.assertRaises(ExpectedValueError):
            compute_conservative_ev(
                calibrated_probability=0.4, odds=0.0, calibration_error=0.05, n_samples=100
            )


if __name__ == "__main__":
    unittest.main()
