import math
import unittest

from boat_prediction.abstention import (
    DQ_MISSING_REQUIRED_DATA,
    OD_ODDS_SHARP_CHANGE,
    REASON_CODES,
    AbstentionError,
    AbstentionThresholds,
    evaluate_abstention,
)
from boat_prediction.odds_deviation import (
    OD_ODDS_UNEXPECTED_VS_MODEL,
    ODDS_DEVIATION_VERSION,
    OddsDeviationError,
    compute_race_deviation,
    deviation_abstention_reasons,
)


class ComputeRaceDeviationTest(unittest.TestCase):
    def test_identical_odds_give_zero_deviation(self) -> None:
        odds = {1: 2.0, 2: 5.0, 3: 10.0}

        result = compute_race_deviation(odds, odds)

        self.assertEqual(result.max_abs_log_ratio, 0.0)
        self.assertEqual(result.mean_abs_log_ratio, 0.0)
        self.assertEqual(result.missing_lanes, ())
        for lane in result.lanes:
            self.assertEqual(lane.log_ratio, 0.0)

    def test_deviation_is_multiplicative_not_additive(self) -> None:
        # 2.0 vs 1.0 and 20.0 vs 10.0 are both a doubling, so both must
        # register the same magnitude of surprise.
        small = compute_race_deviation({1: 1.0}, {1: 2.0})
        large = compute_race_deviation({1: 10.0}, {1: 20.0})

        self.assertAlmostEqual(small.lanes[0].log_ratio, math.log(2))
        self.assertAlmostEqual(large.lanes[0].log_ratio, math.log(2))

    def test_sign_encodes_direction_of_the_surprise(self) -> None:
        market_longer = compute_race_deviation({1: 2.0}, {1: 6.0})
        market_shorter = compute_race_deviation({1: 6.0}, {1: 2.0})

        self.assertGreater(market_longer.lanes[0].log_ratio, 0)
        self.assertLess(market_shorter.lanes[0].log_ratio, 0)
        # same magnitude, opposite direction
        self.assertAlmostEqual(
            market_longer.lanes[0].abs_log_ratio, market_shorter.lanes[0].abs_log_ratio
        )

    def test_max_and_mean_ignore_uncomputable_lanes_but_report_them(self) -> None:
        result = compute_race_deviation({1: 2.0, 2: 5.0, 3: 4.0}, {1: 2.0, 2: 10.0})

        self.assertEqual(result.missing_lanes, (3,))
        self.assertIsNone(result.for_lane(3).log_ratio)
        # only lanes 1 and 2 contribute
        self.assertAlmostEqual(result.max_abs_log_ratio, math.log(2))
        self.assertAlmostEqual(result.mean_abs_log_ratio, math.log(2) / 2)

    def test_lane_present_on_only_one_side_is_kept_not_dropped(self) -> None:
        result = compute_race_deviation({1: 2.0}, {2: 3.0})

        self.assertEqual([lane.lane_number for lane in result.lanes], [1, 2])
        self.assertEqual(result.missing_lanes, (1, 2))
        self.assertIsNone(result.max_abs_log_ratio)

    def test_rejects_non_positive_odds(self) -> None:
        with self.assertRaises(OddsDeviationError):
            compute_race_deviation({1: 0.0}, {1: 2.0})
        with self.assertRaises(OddsDeviationError):
            compute_race_deviation({1: 2.0}, {1: -1.0})

    def test_rejects_out_of_range_lane(self) -> None:
        with self.assertRaises(OddsDeviationError):
            compute_race_deviation({7: 2.0}, {7: 2.0})

    def test_rejects_empty_input(self) -> None:
        with self.assertRaises(OddsDeviationError):
            compute_race_deviation({}, {})

    def test_carries_a_version_and_serializes(self) -> None:
        result = compute_race_deviation({1: 2.0}, {1: 4.0})

        self.assertEqual(result.version, ODDS_DEVIATION_VERSION)
        as_dict = result.to_dict()
        self.assertEqual(as_dict["version"], ODDS_DEVIATION_VERSION)
        self.assertEqual(as_dict["lanes"][0]["actual_odds"], 4.0)


class DeviationAbstentionReasonsTest(unittest.TestCase):
    def test_no_reason_when_every_lane_is_within_threshold(self) -> None:
        deviation = compute_race_deviation({1: 2.0, 2: 5.0}, {1: 2.1, 2: 5.2})

        self.assertEqual(deviation_abstention_reasons(deviation, max_abs_log_ratio=0.5), ())

    def test_large_deviation_anywhere_in_the_field_abstains(self) -> None:
        deviation = compute_race_deviation({1: 2.0, 2: 5.0}, {1: 2.0, 2: 50.0})

        reasons = deviation_abstention_reasons(deviation, max_abs_log_ratio=0.5)

        self.assertEqual(reasons, (OD_ODDS_UNEXPECTED_VS_MODEL,))

    def test_target_lane_is_judged_alone_so_an_unrelated_outsider_does_not_veto(self) -> None:
        # lane 1 (the intended bet) is priced as expected; lane 6 is wild.
        deviation = compute_race_deviation({1: 2.0, 6: 20.0}, {1: 2.0, 6: 200.0})

        self.assertEqual(
            deviation_abstention_reasons(deviation, max_abs_log_ratio=0.5, target_lane=1), ()
        )
        # without a target lane, the same race does abstain
        self.assertEqual(
            deviation_abstention_reasons(deviation, max_abs_log_ratio=0.5),
            (OD_ODDS_UNEXPECTED_VS_MODEL,),
        )

    def test_target_lane_over_threshold_abstains(self) -> None:
        deviation = compute_race_deviation({1: 2.0}, {1: 8.0})

        self.assertEqual(
            deviation_abstention_reasons(deviation, max_abs_log_ratio=0.5, target_lane=1),
            (OD_ODDS_UNEXPECTED_VS_MODEL,),
        )

    def test_uncomputable_deviation_abstains_rather_than_passing(self) -> None:
        deviation = compute_race_deviation({1: 2.0}, {})

        self.assertEqual(
            deviation_abstention_reasons(deviation, max_abs_log_ratio=0.5),
            (DQ_MISSING_REQUIRED_DATA,),
        )

    def test_absent_target_lane_abstains(self) -> None:
        deviation = compute_race_deviation({1: 2.0}, {1: 2.0})

        self.assertEqual(
            deviation_abstention_reasons(deviation, max_abs_log_ratio=0.5, target_lane=4),
            (DQ_MISSING_REQUIRED_DATA,),
        )

    def test_threshold_of_zero_only_passes_an_exact_match(self) -> None:
        exact = compute_race_deviation({1: 2.0}, {1: 2.0})
        off = compute_race_deviation({1: 2.0}, {1: 2.01})

        self.assertEqual(deviation_abstention_reasons(exact, max_abs_log_ratio=0.0), ())
        self.assertEqual(
            deviation_abstention_reasons(off, max_abs_log_ratio=0.0),
            (OD_ODDS_UNEXPECTED_VS_MODEL,),
        )

    def test_rejects_a_negative_threshold(self) -> None:
        deviation = compute_race_deviation({1: 2.0}, {1: 2.0})

        with self.assertRaises(OddsDeviationError):
            deviation_abstention_reasons(deviation, max_abs_log_ratio=-0.1)


class ReasonCodeIntegrationTest(unittest.TestCase):
    def test_missing_data_code_is_the_shared_abstention_constant(self) -> None:
        # not a restated literal -- it must be the same code abstention.py uses
        self.assertIn(DQ_MISSING_REQUIRED_DATA, REASON_CODES)

    def test_new_code_is_a_recognized_reason_code(self) -> None:
        # owned by abstention.py (single home for reason codes) and
        # accepted by evaluate_abstention's validation
        self.assertIn(OD_ODDS_UNEXPECTED_VS_MODEL, REASON_CODES)

    def test_code_is_distinct_from_the_guides_sharp_change_code(self) -> None:
        # documented in the module docstring: OD_ODDS_SHARP_CHANGE describes
        # odds *moving* over time, which needs a time series we do not have.
        self.assertNotEqual(OD_ODDS_UNEXPECTED_VS_MODEL, OD_ODDS_SHARP_CHANGE)

    def test_deviation_reasons_flow_through_evaluate_abstention(self) -> None:
        """The actual wiring: a race that passes every other check still
        abstains once a sharp odds deviation is merged in."""
        thresholds = AbstentionThresholds(
            min_data_quality_score=90.0,
            max_model_disagreement=0.2,
            min_conservative_ev=1.0,
        )
        passing = {
            "thresholds": thresholds,
            "data_quality_score": 95.0,
            "odds_found": True,
            "model_disagreement": 0.05,
            "conservative_ev": 1.5,
        }

        clean = evaluate_abstention(**passing)
        self.assertFalse(clean.abstain)

        deviation = compute_race_deviation({1: 2.0}, {1: 20.0})
        reasons = deviation_abstention_reasons(deviation, max_abs_log_ratio=0.5)
        with_deviation = evaluate_abstention(**passing, extra_reason_codes=reasons)

        self.assertTrue(with_deviation.abstain)
        self.assertIn(OD_ODDS_UNEXPECTED_VS_MODEL, with_deviation.reason_codes)
        self.assertEqual(
            with_deviation.to_skip_reason_string(), OD_ODDS_UNEXPECTED_VS_MODEL
        )

    def test_merged_codes_are_deduplicated_not_appended_blindly(self) -> None:
        thresholds = AbstentionThresholds(
            min_data_quality_score=90.0,
            max_model_disagreement=0.2,
            min_conservative_ev=1.0,
        )
        # missing data raises DQ_MISSING_REQUIRED_DATA on its own, and the
        # deviation check contributes the same code
        decision = evaluate_abstention(
            thresholds=thresholds,
            data_quality_score=None,
            odds_found=True,
            model_disagreement=0.05,
            conservative_ev=1.5,
            extra_reason_codes=(DQ_MISSING_REQUIRED_DATA,),
        )

        self.assertEqual(decision.reason_codes.count(DQ_MISSING_REQUIRED_DATA), 1)

    def test_evaluate_abstention_rejects_an_unrecognized_extra_code(self) -> None:
        thresholds = AbstentionThresholds(
            min_data_quality_score=90.0,
            max_model_disagreement=0.2,
            min_conservative_ev=1.0,
        )

        with self.assertRaises(AbstentionError):
            evaluate_abstention(
                thresholds=thresholds,
                data_quality_score=95.0,
                odds_found=True,
                model_disagreement=0.05,
                conservative_ev=1.5,
                extra_reason_codes=("OD_ODDS_TYPOED_CODE",),
            )

    def test_this_module_never_emits_a_buy_signal(self) -> None:
        import inspect

        from boat_prediction import odds_deviation

        source = inspect.getsource(odds_deviation)
        for forbidden in ("recommend", "should_bet", "buy_signal", "place_bet"):
            self.assertNotIn(f"def {forbidden}", source)


if __name__ == "__main__":
    unittest.main()
