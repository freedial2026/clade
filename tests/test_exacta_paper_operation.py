import unittest
from datetime import datetime, timezone

from boat_prediction import exacta_paper_operation
from boat_prediction.abstention import AbstentionThresholds
from boat_prediction.exacta import ALL_COMBINATIONS, encode_combination
from boat_prediction.exacta_paper_operation import (
    PROMOTION_REQUIRES_SEPARATE_APPROVAL,
    ExactaPaperOperationError,
    decide_and_record,
    report_risk,
    select_best_combination,
)
from boat_prediction.paper_simulation import PaperSimulator

EASY_THRESHOLDS = AbstentionThresholds(
    min_data_quality_score=90.0, max_model_disagreement=0.5, min_conservative_ev=0.5
)
STRICT_THRESHOLDS = AbstentionThresholds(
    min_data_quality_score=90.0, max_model_disagreement=0.5, min_conservative_ev=100.0
)


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def _uniform_exacta_probs() -> dict:
    return {code: 1 / len(ALL_COMBINATIONS) for code in ALL_COMBINATIONS}


class SelectBestCombinationTest(unittest.TestCase):
    def test_picks_the_highest_conservative_ev_combination(self) -> None:
        probs = _uniform_exacta_probs()
        winner = encode_combination(1, 2)
        odds = {winner: 20.0, encode_combination(2, 1): 3.0}

        best_code, report = select_best_combination(
            probs, odds, calibration_error=0.0, n_samples=1_000_000
        )

        self.assertEqual(best_code, winner)
        self.assertGreater(report.conservative_ev, 0)

    def test_rejects_when_no_priced_combination_exists(self) -> None:
        probs = _uniform_exacta_probs()
        with self.assertRaises(ExactaPaperOperationError):
            select_best_combination(probs, {}, calibration_error=0.0, n_samples=1000)


class DecideAndRecordTest(unittest.TestCase):
    def setUp(self) -> None:
        self.probs = _uniform_exacta_probs()
        self.winner = encode_combination(1, 2)
        self.odds = {self.winner: 20.0}

    def test_skips_when_ev_below_threshold(self) -> None:
        simulator = PaperSimulator()
        record = decide_and_record(
            simulator,
            race_id="race-1",
            prediction_id="pred-1",
            decision_at=_utc(2026, 1, 1, 10, 0),
            exacta_probs=self.probs,
            odds_by_combination=self.odds,
            thresholds=STRICT_THRESHOLDS,
            data_quality_score=95.0,
            model_disagreement=0.05,
            calibration_error=0.0,
            n_samples=1_000_000,
            actual_combination=self.winner,
        )
        self.assertEqual(record.result, "skipped")
        self.assertIsNotNone(record.skip_reason)

    def test_records_a_win_when_actual_matches_recommendation(self) -> None:
        simulator = PaperSimulator()
        record = decide_and_record(
            simulator,
            race_id="race-1",
            prediction_id="pred-1",
            decision_at=_utc(2026, 1, 1, 10, 0),
            exacta_probs=self.probs,
            odds_by_combination=self.odds,
            thresholds=EASY_THRESHOLDS,
            data_quality_score=95.0,
            model_disagreement=0.05,
            calibration_error=0.0,
            n_samples=1_000_000,
            actual_combination=self.winner,
        )
        self.assertEqual(record.result, "win")
        self.assertGreater(record.payout_yen, 0)

    def test_records_a_loss_when_actual_differs(self) -> None:
        simulator = PaperSimulator()
        record = decide_and_record(
            simulator,
            race_id="race-1",
            prediction_id="pred-1",
            decision_at=_utc(2026, 1, 1, 10, 0),
            exacta_probs=self.probs,
            odds_by_combination=self.odds,
            thresholds=EASY_THRESHOLDS,
            data_quality_score=95.0,
            model_disagreement=0.05,
            calibration_error=0.0,
            n_samples=1_000_000,
            actual_combination=encode_combination(3, 4),
        )
        self.assertEqual(record.result, "loss")
        self.assertEqual(record.payout_yen, 0)

    def test_records_void_when_actual_combination_is_none(self) -> None:
        simulator = PaperSimulator()
        record = decide_and_record(
            simulator,
            race_id="race-1",
            prediction_id="pred-1",
            decision_at=_utc(2026, 1, 1, 10, 0),
            exacta_probs=self.probs,
            odds_by_combination=self.odds,
            thresholds=EASY_THRESHOLDS,
            data_quality_score=95.0,
            model_disagreement=0.05,
            calibration_error=0.0,
            n_samples=1_000_000,
            actual_combination=None,
        )
        self.assertEqual(record.result, "void")
        self.assertEqual(record.payout_yen, record.stake_yen)

    def test_rejects_empty_odds(self) -> None:
        simulator = PaperSimulator()
        with self.assertRaises(ExactaPaperOperationError):
            decide_and_record(
                simulator,
                race_id="race-1",
                prediction_id="pred-1",
                decision_at=_utc(2026, 1, 1, 10, 0),
                exacta_probs=self.probs,
                odds_by_combination={},
                thresholds=EASY_THRESHOLDS,
                data_quality_score=95.0,
                model_disagreement=0.05,
                calibration_error=0.0,
                n_samples=1_000_000,
                actual_combination=self.winner,
            )


class ReportRiskTest(unittest.TestCase):
    def test_groups_by_an_arbitrary_key_and_reports_stability(self) -> None:
        simulator = PaperSimulator(daily_cap_yen=1_200)
        probs = _uniform_exacta_probs()
        winner = encode_combination(1, 2)
        odds = {winner: 20.0}

        for i in range(2):
            decide_and_record(
                simulator,
                race_id=f"race-{i}",
                prediction_id=f"pred-{i}",
                decision_at=_utc(2026, 1, 1, 10 + i, 0),
                exacta_probs=probs,
                odds_by_combination=odds,
                thresholds=EASY_THRESHOLDS,
                data_quality_score=95.0,
                model_disagreement=0.05,
                calibration_error=0.0,
                n_samples=1_000_000,
                actual_combination=winner,
            )

        report = report_risk(simulator.records, lambda r: r.race_id)
        self.assertEqual(len(report.subgroups), 2)

    def test_rejects_when_there_are_no_placed_bets(self) -> None:
        simulator = PaperSimulator()
        probs = _uniform_exacta_probs()
        winner = encode_combination(1, 2)
        decide_and_record(
            simulator,
            race_id="race-1",
            prediction_id="pred-1",
            decision_at=_utc(2026, 1, 1, 10, 0),
            exacta_probs=probs,
            odds_by_combination={winner: 20.0},
            thresholds=STRICT_THRESHOLDS,  # forces a skip
            data_quality_score=95.0,
            model_disagreement=0.05,
            calibration_error=0.0,
            n_samples=1_000_000,
            actual_combination=winner,
        )

        with self.assertRaises(ExactaPaperOperationError):
            report_risk(simulator.records, lambda r: r.race_id)


class NoRealTransactionOrPromotionPathTest(unittest.TestCase):
    def test_no_forbidden_transaction_related_names_exist(self) -> None:
        forbidden_fragments = ("real_bet", "submit_order", "broker", "exchange", "purchase_api")
        names = dir(exacta_paper_operation)
        for fragment in forbidden_fragments:
            matches = [n for n in names if fragment in n.lower()]
            self.assertEqual(matches, [], f"found forbidden name(s) containing {fragment!r}: {matches}")

    def test_no_promotion_function_exists(self) -> None:
        for name in ("promote", "go_live", "activate_production", "enable_real_trading"):
            self.assertFalse(hasattr(exacta_paper_operation, name))

    def test_promotion_requires_separate_approval_is_documented_true(self) -> None:
        self.assertTrue(PROMOTION_REQUIRES_SEPARATE_APPROVAL)


if __name__ == "__main__":
    unittest.main()
