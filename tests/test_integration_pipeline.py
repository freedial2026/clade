"""End-to-end composition check across the whole P0-P3 decision chain.

Unlike the other test files (one per module), this proves the modules
actually interoperate when wired together the way a real prediction run
would: baseline first-place probabilities -> market normalization ->
entry-course/second-place models -> exacta construction -> paper
operation -> risk reporting. All data here is synthetic (see
tasks/P3-T004.md); this is a composition smoke test, not evidence the
policy performs well on anything real.
"""

import unittest
from datetime import datetime, timezone

from boat_prediction.abstention import AbstentionThresholds
from boat_prediction.baseline import LanePriorBaseline
from boat_prediction.exacta import construct_exacta_probabilities, encode_combination
from boat_prediction.exacta_paper_operation import decide_and_record, report_risk
from boat_prediction.market import normalize_market_odds
from boat_prediction.paper_simulation import PaperSimulator
from boat_prediction.second_place import ConditionalSecondPlaceModel

# Lane 1 wins disproportionately often; lane 2 tends to finish second
# behind lane 1 -- a simple, deliberately non-uniform synthetic pattern.
WINNERS = [1, 1, 1, 1, 2, 2, 3, 4, 5, 6]
FIRST_SECOND_PAIRS = [(1, 2), (1, 2), (1, 3), (2, 1), (2, 3), (3, 1)]

ODDS_BY_LANE = {1: 2.5, 2: 3.5, 3: 5.0, 4: 8.0, 5: 12.0, 6: 20.0}


class EndToEndPipelineTest(unittest.TestCase):
    def test_baseline_to_exacta_paper_operation_composes(self) -> None:
        # P1-T001: baseline first-place distribution
        first_place_probs = LanePriorBaseline().fit(WINNERS).predict()
        self.assertAlmostEqual(sum(first_place_probs.values()), 1.0)

        # P2-T002: market normalization (used here as a sanity cross-check,
        # not fed back into the model -- keeping model vs. market separate
        # per docs/PROJECT_PROFILE.md)
        market_probs = normalize_market_odds(ODDS_BY_LANE)
        self.assertAlmostEqual(sum(market_probs.values()), 1.0)

        # P3-T002: conditional second-place model
        second_place_model = ConditionalSecondPlaceModel().fit(FIRST_SECOND_PAIRS)

        # P3-T003: exacta construction from P1's first-place distribution
        # and P3-T002's conditional model; coherence is checked internally.
        exacta_probs = construct_exacta_probabilities(first_place_probs, second_place_model)
        self.assertAlmostEqual(sum(exacta_probs.values()), 1.0)

        odds_by_combination = {
            encode_combination(first, second): 3.0 + first + second
            for first in range(1, 7)
            for second in range(1, 7)
            if first != second
        }

        # P3-T004: decide (P2-T003 EV + P2-T004 abstention) and record
        # into a paper-only simulator (P2-T005) -- never a real transaction.
        simulator = PaperSimulator(stake_yen=100, daily_cap_yen=1_200)
        thresholds = AbstentionThresholds(
            min_data_quality_score=90.0, max_model_disagreement=0.5, min_conservative_ev=0.05
        )
        actual_winner_combination = encode_combination(1, 2)  # what "really happened"

        record = decide_and_record(
            simulator,
            race_id="2026-07-29-01-01",
            prediction_id="pred-1",
            decision_at=datetime(2026, 7, 29, 9, 0, tzinfo=timezone.utc),
            exacta_probs=exacta_probs,
            odds_by_combination=odds_by_combination,
            thresholds=thresholds,
            data_quality_score=96.0,
            model_disagreement=0.02,
            calibration_error=0.01,
            n_samples=500,
            actual_combination=actual_winner_combination,
        )

        self.assertIn(record.result, ("win", "loss", "skipped"))
        self.assertEqual(simulator.records, [record])

        # P2-T006 (via P3-T004's report_risk), only meaningful for placed bets.
        if record.result != "skipped":
            risk_report = report_risk(simulator.records, lambda r: r.race_id)
            self.assertEqual(risk_report.subgroups[0].n, 1)


if __name__ == "__main__":
    unittest.main()
