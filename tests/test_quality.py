import json
import unittest

from boat_prediction.quality import (
    QualityGateError,
    require_allowed,
    score_report,
)

PERFECT = {
    "completeness": 25,
    "uniqueness": 15,
    "validity": 20,
    "consistency": 15,
    "point_in_time": 25,
}


def _scores(**overrides: float) -> dict:
    scores = dict(PERFECT)
    scores.update(overrides)
    return scores


class ScoreReportTest(unittest.TestCase):
    def test_perfect_score_is_train_or_predict(self) -> None:
        report = score_report(PERFECT)

        self.assertEqual(report.total_score, 100)
        self.assertEqual(report.verdict, "train_or_predict")
        self.assertTrue(report.allows_prediction)
        self.assertTrue(report.allows_research)

    def test_score_in_predict_with_warning_band(self) -> None:
        report = score_report(_scores(validity=10))  # total 90

        self.assertEqual(report.total_score, 90)
        self.assertEqual(report.verdict, "predict_with_warning")
        self.assertTrue(report.allows_prediction)

    def test_score_in_research_only_band_refuses_prediction(self) -> None:
        report = score_report(_scores(validity=5))  # total 85

        self.assertEqual(report.verdict, "research_only")
        self.assertFalse(report.allows_prediction)
        self.assertTrue(report.allows_research)

    def test_score_below_80_is_blocked(self) -> None:
        report = score_report(_scores(validity=0, consistency=0))  # total 70

        self.assertEqual(report.verdict, "blocked")
        self.assertFalse(report.allows_prediction)
        self.assertFalse(report.allows_research)

    def test_missing_axis_is_rejected(self) -> None:
        incomplete = dict(PERFECT)
        del incomplete["validity"]

        with self.assertRaises(QualityGateError):
            score_report(incomplete)

    def test_unknown_axis_is_rejected(self) -> None:
        with self.assertRaises(QualityGateError):
            score_report(_scores(**{"extra_axis": 1}))

    def test_axis_score_above_weight_is_rejected(self) -> None:
        with self.assertRaises(QualityGateError):
            score_report(_scores(validity=21))

    def test_axis_score_below_zero_is_rejected(self) -> None:
        with self.assertRaises(QualityGateError):
            score_report(_scores(validity=-1))

    def test_configurable_thresholds_change_the_verdict(self) -> None:
        lenient = ((50, "train_or_predict"), (0, "blocked"))

        report = score_report(_scores(validity=0, consistency=5), thresholds=lenient)

        self.assertEqual(report.total_score, 70)
        self.assertEqual(report.verdict, "train_or_predict")


class ReportFormatTest(unittest.TestCase):
    def test_to_dict_and_to_json_round_trip(self) -> None:
        report = score_report(PERFECT)

        as_dict = report.to_dict()
        as_json = json.loads(report.to_json())

        self.assertEqual(as_dict, as_json)
        self.assertEqual(as_dict["verdict"], "train_or_predict")

    def test_render_is_human_readable_text(self) -> None:
        report = score_report(PERFECT)

        text = report.render()

        self.assertIn("100.0/100", text)
        self.assertIn("completeness: 25.0/25", text)


class RequireAllowedTest(unittest.TestCase):
    def test_blocked_dataset_refuses_prediction_and_research(self) -> None:
        report = score_report(_scores(validity=0, consistency=0))

        with self.assertRaises(QualityGateError):
            require_allowed(report, purpose="prediction")
        with self.assertRaises(QualityGateError):
            require_allowed(report, purpose="research")

    def test_research_only_dataset_refuses_prediction_but_allows_research(self) -> None:
        report = score_report(_scores(validity=5))

        with self.assertRaises(QualityGateError):
            require_allowed(report, purpose="prediction")
        require_allowed(report, purpose="research")  # does not raise

    def test_perfect_dataset_allows_both(self) -> None:
        report = score_report(PERFECT)

        require_allowed(report, purpose="prediction")
        require_allowed(report, purpose="research")

    def test_unknown_purpose_is_rejected(self) -> None:
        report = score_report(PERFECT)

        with self.assertRaises(QualityGateError):
            require_allowed(report, purpose="deployment")


if __name__ == "__main__":
    unittest.main()
