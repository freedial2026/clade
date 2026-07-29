import unittest

from boat_prediction.calibration import (
    BinnedCalibrator,
    CalibrationError,
    evaluate,
    expected_calibration_error,
    fit_and_evaluate,
)

CLASSES = [0, 1]


class ExpectedCalibrationErrorTest(unittest.TestCase):
    def test_zero_when_confidence_matches_accuracy_exactly(self) -> None:
        # 10 samples, top-class confidence 0.9, correct exactly 9/10 times.
        probs = [[0.9, 0.1]] * 10
        y_true = [0] * 9 + [1]

        ece = expected_calibration_error(y_true, probs, CLASSES, n_bins=10)

        self.assertAlmostEqual(ece, 0.0, places=6)

    def test_positive_when_overconfident(self) -> None:
        # Always 0.99 confident but only right half the time -> miscalibrated.
        probs = [[0.99, 0.01]] * 10
        y_true = [0, 1] * 5

        ece = expected_calibration_error(y_true, probs, CLASSES, n_bins=10)

        self.assertGreater(ece, 0.4)

    def test_rejects_empty_input(self) -> None:
        with self.assertRaises(CalibrationError):
            expected_calibration_error([], [], CLASSES)


class EvaluateTest(unittest.TestCase):
    def test_report_contains_all_three_metrics_and_sample_count(self) -> None:
        probs = [[0.9, 0.1]] * 10
        y_true = [0] * 9 + [1]

        report = evaluate(y_true, probs, CLASSES)

        self.assertEqual(report.n_samples, 10)
        self.assertGreater(report.log_loss, 0)
        self.assertGreater(report.brier_score, 0)
        self.assertGreaterEqual(report.expected_calibration_error, 0)
        self.assertEqual(
            set(report.to_dict()),
            {"log_loss", "brier_score", "expected_calibration_error", "n_samples"},
        )


class BinnedCalibratorTest(unittest.TestCase):
    def test_predict_before_fit_raises(self) -> None:
        with self.assertRaises(CalibrationError):
            BinnedCalibrator().calibrate_confidence(0.9)

    def test_calibrates_overconfident_predictions_downward(self) -> None:
        probs = [[0.99, 0.01]] * 10
        y_true = [0, 1] * 5  # only 50% correct despite 0.99 confidence

        calibrator = BinnedCalibrator(n_bins=10).fit(y_true, probs, CLASSES)

        self.assertLess(calibrator.calibrate_confidence(0.99), 0.99)

    def test_fitting_twice_on_same_data_is_reproducible(self) -> None:
        probs = [[0.9, 0.1]] * 10
        y_true = [0] * 9 + [1]

        first = BinnedCalibrator().fit(y_true, probs, CLASSES)
        second = BinnedCalibrator().fit(y_true, probs, CLASSES)

        self.assertEqual(first.calibrate_confidence(0.9), second.calibrate_confidence(0.9))


class FitAndEvaluateTest(unittest.TestCase):
    def test_holdout_data_is_never_passed_to_fit(self) -> None:
        seen_fit_ids: list[int] = []

        class SpyCalibrator(BinnedCalibrator):
            def fit(self, y_true, probs, classes):
                seen_fit_ids.append(id(y_true))
                seen_fit_ids.append(id(probs))
                return super().fit(y_true, probs, classes)

        fit_y = [0] * 9 + [1]
        fit_probs = [[0.9, 0.1]] * 10
        holdout_y = [1] * 4 + [0] * 4
        holdout_probs = [[0.8, 0.2]] * 8

        original = BinnedCalibrator
        try:
            import boat_prediction.calibration as calibration_module

            calibration_module.BinnedCalibrator = SpyCalibrator
            calibrator, fit_report, holdout_report = fit_and_evaluate(
                fit_y, fit_probs, holdout_y, holdout_probs, CLASSES
            )
        finally:
            calibration_module.BinnedCalibrator = original

        self.assertNotIn(id(holdout_y), seen_fit_ids)
        self.assertNotIn(id(holdout_probs), seen_fit_ids)
        self.assertIn(id(fit_y), seen_fit_ids)
        self.assertIn(id(fit_probs), seen_fit_ids)
        self.assertEqual(fit_report.n_samples, 10)
        self.assertEqual(holdout_report.n_samples, 8)

    def test_returns_separate_reports_for_fit_and_holdout_splits(self) -> None:
        fit_y = [0] * 9 + [1]
        fit_probs = [[0.9, 0.1]] * 10
        holdout_y = [1] * 4 + [0] * 4  # deliberately different distribution
        holdout_probs = [[0.9, 0.1]] * 8

        _, fit_report, holdout_report = fit_and_evaluate(
            fit_y, fit_probs, holdout_y, holdout_probs, CLASSES
        )

        self.assertNotEqual(fit_report.log_loss, holdout_report.log_loss)


if __name__ == "__main__":
    unittest.main()
