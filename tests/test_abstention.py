import unittest

from boat_prediction.abstention import (
    ABSTENTION_POLICY_VERSION,
    REASON_CODES,
    AbstentionThresholds,
    evaluate_abstention,
)

THRESHOLDS = AbstentionThresholds(
    min_data_quality_score=90.0, max_model_disagreement=0.15, min_conservative_ev=1.0
)


def _passing_inputs(**overrides):
    defaults = dict(
        thresholds=THRESHOLDS,
        data_quality_score=95.0,
        odds_found=True,
        model_disagreement=0.05,
        conservative_ev=1.2,
    )
    defaults.update(overrides)
    return defaults


class EvaluateAbstentionTest(unittest.TestCase):
    def test_no_abstain_when_everything_passes(self) -> None:
        decision = evaluate_abstention(**_passing_inputs())
        self.assertFalse(decision.abstain)
        self.assertEqual(decision.reason_codes, ())

    def test_missing_data_quality_score_defaults_to_abstain(self) -> None:
        decision = evaluate_abstention(**_passing_inputs(data_quality_score=None))
        self.assertTrue(decision.abstain)
        self.assertIn("DQ_MISSING_REQUIRED_DATA", decision.reason_codes)

    def test_low_data_quality_score_triggers_its_own_code(self) -> None:
        decision = evaluate_abstention(**_passing_inputs(data_quality_score=70.0))
        self.assertTrue(decision.abstain)
        self.assertEqual(decision.reason_codes, ("DQ_LOW_DATA_QUALITY",))

    def test_missing_odds_triggers_od_odds_missing(self) -> None:
        decision = evaluate_abstention(**_passing_inputs(odds_found=False))
        self.assertEqual(decision.reason_codes, ("OD_ODDS_MISSING",))

    def test_missing_model_disagreement_defaults_to_abstain(self) -> None:
        decision = evaluate_abstention(**_passing_inputs(model_disagreement=None))
        self.assertIn("DQ_MISSING_REQUIRED_DATA", decision.reason_codes)

    def test_high_model_disagreement_triggers_its_own_code(self) -> None:
        decision = evaluate_abstention(**_passing_inputs(model_disagreement=0.5))
        self.assertEqual(decision.reason_codes, ("MD_MODEL_DISAGREEMENT",))

    def test_missing_conservative_ev_defaults_to_abstain(self) -> None:
        decision = evaluate_abstention(**_passing_inputs(conservative_ev=None))
        self.assertIn("DQ_MISSING_REQUIRED_DATA", decision.reason_codes)

    def test_low_conservative_ev_triggers_its_own_code(self) -> None:
        decision = evaluate_abstention(**_passing_inputs(conservative_ev=0.8))
        self.assertEqual(decision.reason_codes, ("EV_CONSERVATIVE_BELOW_THRESHOLD",))

    def test_multiple_failures_combine_into_multiple_reason_codes(self) -> None:
        decision = evaluate_abstention(
            **_passing_inputs(data_quality_score=50.0, odds_found=False, conservative_ev=0.1)
        )
        self.assertEqual(
            set(decision.reason_codes),
            {"DQ_LOW_DATA_QUALITY", "OD_ODDS_MISSING", "EV_CONSERVATIVE_BELOW_THRESHOLD"},
        )

    def test_repeated_missing_data_reason_is_deduplicated(self) -> None:
        decision = evaluate_abstention(
            **_passing_inputs(data_quality_score=None, model_disagreement=None, conservative_ev=None)
        )
        self.assertEqual(decision.reason_codes.count("DQ_MISSING_REQUIRED_DATA"), 1)

    def test_decision_records_the_policy_version(self) -> None:
        decision = evaluate_abstention(**_passing_inputs())
        self.assertEqual(decision.policy_version, ABSTENTION_POLICY_VERSION)

    def test_to_dict_contains_expected_keys(self) -> None:
        decision = evaluate_abstention(**_passing_inputs(odds_found=False))
        self.assertEqual(set(decision.to_dict()), {"abstain", "reason_codes", "policy_version"})

    def test_all_produced_reason_codes_are_documented(self) -> None:
        decision = evaluate_abstention(
            **_passing_inputs(
                data_quality_score=None, odds_found=False, model_disagreement=None, conservative_ev=None
            )
        )
        for code in decision.reason_codes:
            self.assertIn(code, REASON_CODES)


if __name__ == "__main__":
    unittest.main()
