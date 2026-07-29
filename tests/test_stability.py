import math
import unittest
from dataclasses import dataclass

from boat_prediction.stability import StabilityError, assess_subgroup_stability


@dataclass(frozen=True)
class Bet:
    month: str
    venue: str
    grade: str
    odds_band: str
    net_return: float


RECORDS = [
    Bet("2026-01", "venue-A", "grade-1", "low", 10.0),
    Bet("2026-01", "venue-A", "grade-1", "low", -5.0),
    Bet("2026-01", "venue-B", "grade-2", "high", 20.0),
    Bet("2026-02", "venue-A", "grade-1", "low", 0.0),
    Bet("2026-02", "venue-B", "grade-2", "high", -10.0),
]


class AssessSubgroupStabilityTest(unittest.TestCase):
    def test_rejects_empty_records(self) -> None:
        with self.assertRaises(StabilityError):
            assess_subgroup_stability([], lambda r: r.month, lambda r: r.net_return)

    def test_rejects_invalid_concentration_threshold(self) -> None:
        with self.assertRaises(StabilityError):
            assess_subgroup_stability(
                RECORDS, lambda r: r.month, lambda r: r.net_return, concentration_threshold=0
            )

    def test_rejects_unsupported_confidence_level(self) -> None:
        with self.assertRaises(StabilityError):
            assess_subgroup_stability(
                RECORDS, lambda r: r.month, lambda r: r.net_return, confidence_level=0.5
            )

    def test_reports_sample_size_per_group(self) -> None:
        report = assess_subgroup_stability(RECORDS, lambda r: r.month, lambda r: r.net_return)
        sizes = {s.group: s.n for s in report.subgroups}
        self.assertEqual(sizes, {"2026-01": 3, "2026-02": 2})

    def test_single_sample_group_ci_collapses_to_the_point_estimate(self) -> None:
        single = [RECORDS[2]]  # exactly one record -> n=1 for its group
        report = assess_subgroup_stability(single, lambda r: r.grade, lambda r: r.net_return)
        stats = report.subgroups[0]
        self.assertEqual(stats.n, 1)
        self.assertEqual(stats.ci_low, stats.mean)
        self.assertEqual(stats.ci_high, stats.mean)

    def test_confidence_interval_matches_hand_computed_normal_approximation(self) -> None:
        values = [10.0, -5.0, 0.0]
        mean = sum(values) / 3
        variance = sum((v - mean) ** 2 for v in values) / 2  # sample variance, n-1
        stderr = math.sqrt(variance / 3)
        expected_margin = 1.96 * stderr

        group_records = [
            Bet("m", "v", "g", "b", 10.0),
            Bet("m", "v", "g", "b", -5.0),
            Bet("m", "v", "g", "b", 0.0),
        ]
        report = assess_subgroup_stability(group_records, lambda r: r.month, lambda r: r.net_return)
        stats = report.subgroups[0]

        self.assertAlmostEqual(stats.mean, mean)
        self.assertAlmostEqual(stats.ci_low, mean - expected_margin)
        self.assertAlmostEqual(stats.ci_high, mean + expected_margin)

    def test_flags_concentration_when_one_group_dominates(self) -> None:
        skewed = [Bet("2026-01", "v", "g", "b", 1.0)] * 9 + [Bet("2026-02", "v", "g", "b", 1.0)]
        report = assess_subgroup_stability(
            skewed, lambda r: r.month, lambda r: r.net_return, concentration_threshold=0.5
        )
        flagged = {s.group: s.concentration_flag for s in report.subgroups}
        self.assertTrue(flagged["2026-01"])
        self.assertFalse(flagged["2026-02"])
        self.assertEqual(report.flagged_groups, ("2026-01",))

    def test_does_not_flag_when_groups_are_balanced(self) -> None:
        balanced = [Bet("2026-01", "v", "g", "b", 1.0)] * 5 + [Bet("2026-02", "v", "g", "b", 1.0)] * 5
        report = assess_subgroup_stability(
            balanced, lambda r: r.month, lambda r: r.net_return, concentration_threshold=0.5
        )
        self.assertEqual(report.flagged_groups, ())

    def test_works_grouped_by_month(self) -> None:
        report = assess_subgroup_stability(RECORDS, lambda r: r.month, lambda r: r.net_return)
        self.assertEqual({s.group for s in report.subgroups}, {"2026-01", "2026-02"})

    def test_works_grouped_by_venue(self) -> None:
        report = assess_subgroup_stability(RECORDS, lambda r: r.venue, lambda r: r.net_return)
        self.assertEqual({s.group for s in report.subgroups}, {"venue-A", "venue-B"})

    def test_works_grouped_by_grade(self) -> None:
        report = assess_subgroup_stability(RECORDS, lambda r: r.grade, lambda r: r.net_return)
        self.assertEqual({s.group for s in report.subgroups}, {"grade-1", "grade-2"})

    def test_works_grouped_by_odds_band(self) -> None:
        report = assess_subgroup_stability(RECORDS, lambda r: r.odds_band, lambda r: r.net_return)
        self.assertEqual({s.group for s in report.subgroups}, {"low", "high"})

    def test_to_dict_contains_expected_keys(self) -> None:
        report = assess_subgroup_stability(RECORDS, lambda r: r.month, lambda r: r.net_return)
        data = report.to_dict()
        self.assertEqual(set(data), {"subgroups", "confidence_level", "concentration_threshold"})
        self.assertEqual(
            set(data["subgroups"][0]),
            {"group", "n", "mean", "ci_low", "ci_high", "share_of_total", "concentration_flag"},
        )


if __name__ == "__main__":
    unittest.main()
