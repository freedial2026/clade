import unittest
from datetime import datetime, timezone

from boat_prediction.paper_simulation import (
    BetCandidate,
    PaperSimulationError,
    PaperSimulator,
    RESULT_LOSS,
    RESULT_VOID,
    RESULT_WIN,
    summarize,
)


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def _candidate(race_id: str, decision_at: datetime, *, recommended: bool, skip_reason=None) -> BetCandidate:
    return BetCandidate(
        race_id=race_id,
        prediction_id=f"pred-{race_id}",
        decision_at=decision_at,
        combination="1",
        odds_at_decision=3.0,
        model_probability=0.4,
        conservative_probability=0.3,
        expected_value=0.9,
        recommended=recommended,
        skip_reason=skip_reason,
    )


class PaperSimulatorConfigTest(unittest.TestCase):
    def test_stake_defaults_to_guide_value(self) -> None:
        sim = PaperSimulator()
        self.assertEqual(sim.stake_yen, 100)

    def test_stake_is_configurable(self) -> None:
        sim = PaperSimulator(stake_yen=50, daily_cap_yen=600)
        self.assertEqual(sim.stake_yen, 50)

    def test_rejects_non_positive_stake(self) -> None:
        with self.assertRaises(PaperSimulationError):
            PaperSimulator(stake_yen=0)

    def test_rejects_daily_cap_below_stake(self) -> None:
        with self.assertRaises(PaperSimulationError):
            PaperSimulator(stake_yen=100, daily_cap_yen=50)


class RecordSkipTest(unittest.TestCase):
    def test_records_a_skip_with_zero_stake_and_payout(self) -> None:
        sim = PaperSimulator()
        candidate = _candidate("race-1", _utc(2026, 1, 1, 10, 0), recommended=False, skip_reason="OD_ODDS_MISSING")

        record = sim.record_skip(candidate)

        self.assertEqual(record.stake_yen, 0)
        self.assertEqual(record.payout_yen, 0)
        self.assertEqual(record.result, "skipped")

    def test_rejects_recommended_candidate(self) -> None:
        sim = PaperSimulator()
        candidate = _candidate("race-1", _utc(2026, 1, 1, 10, 0), recommended=True)

        with self.assertRaises(PaperSimulationError):
            sim.record_skip(candidate)

    def test_requires_a_skip_reason(self) -> None:
        sim = PaperSimulator()
        candidate = _candidate("race-1", _utc(2026, 1, 1, 10, 0), recommended=False, skip_reason=None)

        with self.assertRaises(PaperSimulationError):
            sim.record_skip(candidate)


class RecordOutcomeTest(unittest.TestCase):
    def test_rejects_non_recommended_candidate(self) -> None:
        sim = PaperSimulator()
        candidate = _candidate("race-1", _utc(2026, 1, 1, 10, 0), recommended=False, skip_reason="x")

        with self.assertRaises(PaperSimulationError):
            sim.record_outcome(candidate, result=RESULT_WIN, payout_yen=300)

    def test_uses_the_fixed_stake_regardless_of_result(self) -> None:
        sim = PaperSimulator(stake_yen=100, daily_cap_yen=1_200)
        loss = sim.record_outcome(
            _candidate("race-1", _utc(2026, 1, 1, 10, 0), recommended=True), result=RESULT_LOSS
        )
        win_after_loss = sim.record_outcome(
            _candidate("race-2", _utc(2026, 1, 1, 11, 0), recommended=True),
            result=RESULT_WIN,
            payout_yen=300,
        )

        # no martingale: stake after a loss is identical to before
        self.assertEqual(loss.stake_yen, 100)
        self.assertEqual(win_after_loss.stake_yen, 100)

    def test_void_refunds_the_stake_in_full(self) -> None:
        sim = PaperSimulator()
        candidate = _candidate("race-1", _utc(2026, 1, 1, 10, 0), recommended=True)

        record = sim.record_outcome(candidate, result=RESULT_VOID)

        self.assertEqual(record.stake_yen, 100)
        self.assertEqual(record.payout_yen, 100)  # net 0, not counted as a loss
        self.assertEqual(record.result, "void")

    def test_rejects_a_second_bet_on_the_same_race(self) -> None:
        sim = PaperSimulator(daily_cap_yen=1_200)
        sim.record_outcome(
            _candidate("race-1", _utc(2026, 1, 1, 10, 0), recommended=True), result=RESULT_WIN, payout_yen=300
        )

        with self.assertRaises(PaperSimulationError):
            sim.record_outcome(
                _candidate("race-1", _utc(2026, 1, 1, 10, 5), recommended=True), result=RESULT_LOSS
            )

    def test_enforces_the_daily_cap(self) -> None:
        sim = PaperSimulator(stake_yen=100, daily_cap_yen=300)
        for i in range(3):
            sim.record_outcome(
                _candidate(f"race-{i}", _utc(2026, 1, 1, 10 + i, 0), recommended=True),
                result=RESULT_LOSS,
            )

        with self.assertRaises(PaperSimulationError):
            sim.record_outcome(
                _candidate("race-4", _utc(2026, 1, 1, 14, 0), recommended=True), result=RESULT_LOSS
            )

    def test_daily_cap_resets_on_a_new_day(self) -> None:
        sim = PaperSimulator(stake_yen=100, daily_cap_yen=300)
        for i in range(3):
            sim.record_outcome(
                _candidate(f"race-{i}", _utc(2026, 1, 1, 10 + i, 0), recommended=True),
                result=RESULT_LOSS,
            )

        # next calendar day: cap should have reset
        record = sim.record_outcome(
            _candidate("race-day2", _utc(2026, 1, 2, 10, 0), recommended=True), result=RESULT_WIN, payout_yen=300
        )
        self.assertEqual(record.stake_yen, 100)

    def test_rejects_invalid_result(self) -> None:
        sim = PaperSimulator()
        candidate = _candidate("race-1", _utc(2026, 1, 1, 10, 0), recommended=True)

        with self.assertRaises(PaperSimulationError):
            sim.record_outcome(candidate, result="jackpot")


class SummarizeTest(unittest.TestCase):
    def test_returns_drawdown_and_streaks_match_hand_computed_example(self) -> None:
        sim = PaperSimulator(stake_yen=100, daily_cap_yen=1_200)
        sim.record_outcome(_candidate("r1", _utc(2026, 1, 1, 10), recommended=True), result=RESULT_WIN, payout_yen=250)
        sim.record_outcome(_candidate("r2", _utc(2026, 1, 1, 11), recommended=True), result=RESULT_LOSS)
        sim.record_outcome(_candidate("r3", _utc(2026, 1, 1, 12), recommended=True), result=RESULT_LOSS)
        sim.record_outcome(_candidate("r4", _utc(2026, 1, 1, 13), recommended=True), result=RESULT_WIN, payout_yen=300)
        sim.record_outcome(_candidate("r5", _utc(2026, 1, 1, 14), recommended=True), result=RESULT_VOID)
        sim.record_skip(_candidate("r6", _utc(2026, 1, 1, 15), recommended=False, skip_reason="OD_ODDS_MISSING"))

        summary = summarize(sim.records)

        self.assertEqual(summary.n_bets, 5)
        self.assertEqual(summary.n_skipped, 1)
        self.assertEqual(summary.n_void, 1)
        self.assertEqual(summary.total_stake_yen, 500)
        self.assertEqual(summary.total_payout_yen, 650)
        self.assertEqual(summary.net_return_yen, 150)
        self.assertEqual(summary.max_drawdown_yen, 200)
        self.assertEqual(summary.max_win_streak, 1)
        self.assertEqual(summary.max_loss_streak, 2)

    def test_empty_records_produce_zeroed_summary(self) -> None:
        summary = summarize([])
        self.assertEqual(summary.n_bets, 0)
        self.assertEqual(summary.net_return_yen, 0)
        self.assertEqual(summary.max_drawdown_yen, 0)

    def test_to_dict_contains_all_fields(self) -> None:
        summary = summarize([])
        self.assertEqual(
            set(summary.to_dict()),
            {
                "n_bets",
                "n_skipped",
                "n_void",
                "total_stake_yen",
                "total_payout_yen",
                "net_return_yen",
                "max_drawdown_yen",
                "max_win_streak",
                "max_loss_streak",
            },
        )


if __name__ == "__main__":
    unittest.main()
