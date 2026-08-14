"""Profit attribution (`db.attribute_profit`).

Two things need guarding here, and they fail in opposite directions.

The **reductions** (counterfactuals, permutation null, noise sensitivity)
are the answers themselves: a blind comparison that quietly averages the
wrong cells, or a permutation that leaves the probabilities where they
were, reports a large model lift and a clean z-score for a rule with no
edge at all. Those are tested on hand-built tables where the right answer
is arithmetic.

The **table build** has to agree with `evaluate_bet_types` exactly, or the
reproduction check that every conclusion rests on compares two different
things. `RuleResultsMatchEvaluateBetTypesTest` runs both against one
database and requires identical bets, hits and ROI.
"""

from __future__ import annotations

import datetime as dt
import unittest

import numpy as np
from sqlalchemy.orm import Session
from test_evaluate_bet_types import (
    TEST_END,
    TEST_START,
    TRAIN_END,
    TRAIN_START,
    BetTypeTestBase,
)

from boat_prediction.db import attribute_profit as ap
from boat_prediction.db import evaluate_bet_types as ebt
from boat_prediction.db.models import OddsSnapshot


def _table(
    *,
    prob,
    odds,
    ret,
    complete=None,
    dates=None,
    combinations=("1", "2"),
    bet_type="win",
) -> ap.CandidateTable:
    prob = np.asarray(prob, dtype=float)
    odds = np.asarray(odds, dtype=float)
    ret = np.asarray(ret, dtype=float)
    races = prob.shape[0]
    return ap.CandidateTable(
        bet_type=bet_type,
        combinations=tuple(combinations),
        race_ids=[f"r{i}" for i in range(races)],
        race_dates=list(dates or [dt.date(2026, 1, 1) for _ in range(races)]),
        venue_ids=["v" for _ in range(races)],
        prob=prob,
        odds=odds,
        ret=ret,
        hit=ret > 0,
        refund=np.zeros_like(ret, dtype=bool),
        complete=np.asarray(
            complete if complete is not None else [True] * races, dtype=bool
        ),
    )


class SelectionTest(unittest.TestCase):
    def test_confidence_takes_the_top_probability_in_every_race(self) -> None:
        table = _table(
            prob=[[0.7, 0.3], [0.2, 0.8]],
            odds=[[1.5, 4.0], [3.0, 1.2]],
            ret=[[1.5, 0.0], [0.0, 1.2]],
        )
        selection = ap.select(table, "confidence")
        self.assertEqual(len(selection), 2)
        self.assertEqual(list(selection.cols), [0, 1])

    def test_ev_best_takes_one_bet_per_race_above_the_threshold(self) -> None:
        # EVs are [1.05, 1.20] and [0.60, 0.96]: only the first race clears 1.1.
        table = _table(
            prob=[[0.7, 0.3], [0.2, 0.8]],
            odds=[[1.5, 4.0], [3.0, 1.2]],
            ret=[[1.5, 0.0], [0.0, 1.2]],
        )
        selection = ap.select(table, "ev_best", 1.1)
        self.assertEqual(len(selection), 1)
        self.assertEqual(list(selection.rows), [0])
        self.assertEqual(list(selection.cols), [1])

    def test_ev_all_takes_every_candidate_above_the_threshold(self) -> None:
        table = _table(
            prob=[[0.7, 0.3], [0.2, 0.8]],
            odds=[[1.5, 4.0], [3.0, 1.2]],
            ret=[[1.5, 0.0], [0.0, 1.2]],
        )
        selection = ap.select(table, "ev_all", 1.0)
        self.assertEqual(sorted(zip(selection.rows, selection.cols)), [(0, 0), (0, 1)])

    def test_incomplete_grids_are_excluded_from_ev_rules_but_not_confidence(self) -> None:
        """A partial grid lets an EV rule pick from a biased subset of the
        pool, which scores as skill. Confidence needs no price at all, so
        it still runs."""
        table = _table(
            prob=[[0.7, 0.3], [0.2, 0.8]],
            odds=[[1.5, 4.0], [3.0, 1.2]],
            ret=[[1.5, 0.0], [0.0, 1.2]],
            complete=[True, False],
        )
        self.assertEqual(len(ap.select(table, "ev_all", 0.0)), 2)
        self.assertEqual(len(ap.select(table, "confidence")), 2)

    def test_unknown_rule_is_rejected(self) -> None:
        table = _table(prob=[[1.0, 0.0]], odds=[[2.0, 2.0]], ret=[[2.0, 0.0]])
        with self.assertRaises(ap.AttributionError):
            ap.select(table, "back_the_favourite")


class SummarizeTest(unittest.TestCase):
    def test_roi_and_hit_rate_are_the_plain_averages(self) -> None:
        table = _table(
            prob=[[1.0, 0.0], [1.0, 0.0], [1.0, 0.0], [1.0, 0.0]],
            odds=[[4.0, 1.0]] * 4,
            ret=[[4.0, 0.0], [0.0, 0.0], [0.0, 0.0], [0.0, 0.0]],
        )
        result = ap.summarize(table, ap.select(table, "confidence"))
        self.assertEqual(result.bets, 4)
        self.assertEqual(result.hits, 1)
        self.assertAlmostEqual(result.roi, 1.0)
        self.assertAlmostEqual(result.hit_rate, 0.25)

    def test_trimmed_roi_removes_the_largest_payouts_stake_and_all(self) -> None:
        """Same definition as `evaluate_p2.RuleResult.trimmed_roi`: the
        largest wins are removed together with their stakes, so a rule
        carried by its tail collapses instead of merely dipping."""
        races = ap.TRIM_TOP_PAYOUTS + 2
        ret = np.zeros((races, 1))
        ret[0, 0] = 100.0
        table = _table(
            prob=np.ones((races, 1)),
            odds=np.full((races, 1), 100.0),
            ret=ret,
            combinations=("1",),
        )
        result = ap.summarize(table, ap.select(table, "confidence"))
        self.assertAlmostEqual(result.roi, 100.0 / races)
        self.assertAlmostEqual(result.trimmed_roi, 0.0)

    def test_trimmed_roi_is_nan_when_there_are_too_few_bets(self) -> None:
        table = _table(prob=[[1.0]], odds=[[3.0]], ret=[[3.0]], combinations=("1",))
        result = ap.summarize(table, ap.select(table, "confidence"))
        self.assertTrue(np.isnan(result.trimmed_roi))


class BlindCounterfactualTest(unittest.TestCase):
    def test_blind_roi_is_the_average_of_the_bets_own_price_bands(self) -> None:
        """Four races priced 5.0 (band 4-6), one winner among them. A rule
        that picks the winning cell every time returns 5.0; buying blind in
        that band returns the band's own 1.25."""
        prob = np.array([[0.9, 0.1], [0.9, 0.1], [0.9, 0.1], [0.9, 0.1]])
        odds = np.full((4, 2), 5.0)
        ret = np.zeros((4, 2))
        ret[0, 0] = 5.0
        ret[1, 1] = 5.0
        table = _table(prob=prob, odds=odds, ret=ret)
        selection = ap.Selection("hand", 0.0, np.array([0, 1]), np.array([0, 1]))
        result = ap.blind_counterfactual(table, selection)
        self.assertEqual(result.covered, 2)
        self.assertAlmostEqual(result.rule_roi, 5.0)
        self.assertAlmostEqual(result.blind_roi, 10.0 / 8)
        self.assertAlmostEqual(result.lift, 5.0 / (10.0 / 8))

    def test_controlling_for_the_combination_removes_a_pure_lane_effect(self) -> None:
        """Both candidates are priced the same, but lane 1 wins every race.
        Controlling only for price credits the rule with the lane; adding
        the combination control leaves it with nothing, which is the right
        answer for a rule that only ever backs lane 1."""
        races = 4
        prob = np.tile([0.9, 0.1], (races, 1))
        odds = np.full((races, 2), 5.0)
        ret = np.zeros((races, 2))
        ret[:, 0] = 5.0
        table = _table(prob=prob, odds=odds, ret=ret)
        selection = ap.select(table, "confidence")
        band_only = ap.blind_counterfactual(table, selection, by_combination=False)
        with_lane = ap.blind_counterfactual(table, selection, by_combination=True)
        self.assertAlmostEqual(band_only.lift, 2.0)
        self.assertAlmostEqual(with_lane.lift, 1.0)


class PermutationNullTest(unittest.TestCase):
    def test_identical_probabilities_make_the_null_equal_the_real_rule(self) -> None:
        """When every race carries the same probability vector, shuffling
        them changes nothing -- so any gap the null reports would be an
        artifact of the shuffle rather than evidence about the model."""
        rng = np.random.default_rng(3)
        races = 60
        prob = np.tile([0.6, 0.4], (races, 1))
        odds = rng.uniform(2.0, 8.0, size=(races, 2))
        ret = np.where(rng.random((races, 2)) < 0.2, odds, 0.0)
        table = _table(prob=prob, odds=odds, ret=ret)
        result = ap.permutation_null(table, threshold=1.0, permutations=5, seed=1)
        self.assertAlmostEqual(result.real_roi, result.null_roi_mean)
        self.assertAlmostEqual(result.null_roi_sd, 0.0)

    def test_a_real_edge_beats_every_shuffle(self) -> None:
        """The probabilities name the winner; a shuffled copy names another
        race's winner, so the real rule has to stand clear of the null."""
        rng = np.random.default_rng(5)
        races = 200
        winner = rng.integers(0, 2, size=races)
        prob = np.zeros((races, 2))
        prob[np.arange(races), winner] = 0.8
        prob[np.arange(races), 1 - winner] = 0.2
        odds = np.full((races, 2), 3.0)
        ret = np.zeros((races, 2))
        ret[np.arange(races), winner] = 3.0
        table = _table(prob=prob, odds=odds, ret=ret)
        result = ap.permutation_null(table, threshold=1.0, permutations=10, seed=2)
        self.assertAlmostEqual(result.real_roi, 3.0)
        self.assertLess(result.null_roi_max, result.real_roi)
        self.assertGreater(result.z, 3.0)

    def test_the_table_is_left_as_it_was_found(self) -> None:
        table = _table(
            prob=[[0.7, 0.3], [0.2, 0.8]],
            odds=[[1.5, 4.0], [3.0, 1.2]],
            ret=[[1.5, 0.0], [0.0, 1.2]],
        )
        before = table.prob.copy()
        ap.permutation_null(table, threshold=0.0, permutations=3, seed=0)
        np.testing.assert_array_equal(table.prob, before)


class NoiseSensitivityTest(unittest.TestCase):
    def _table(self) -> ap.CandidateTable:
        rng = np.random.default_rng(9)
        races = 120
        prob = rng.dirichlet([2.0, 2.0], size=races)
        odds = 1.0 / prob * 0.8
        ret = np.where(rng.random((races, 2)) < prob, odds, 0.0)
        return _table(prob=prob, odds=odds, ret=ret)

    def test_zero_noise_reproduces_the_measured_roi(self) -> None:
        table = self._table()
        result = ap.noise_sensitivity(table, threshold=1.0, sigma=0.0, draws=3, seed=0)
        self.assertAlmostEqual(result.real_roi, result.noisy_roi_mean)
        self.assertAlmostEqual(result.noisy_roi_sd, 0.0)

    def test_noise_moves_the_selection_and_not_the_settlement(self) -> None:
        """The blurred price is what the rule selects on; the payout stays
        whatever the race really paid. If the settlement moved too, the
        sensitivity would measure a different game rather than the cost of
        not knowing the closing price."""
        table = self._table()
        odds_before = table.odds.copy()
        ret_before = table.ret.copy()
        result = ap.noise_sensitivity(table, threshold=1.0, sigma=0.6, draws=5, seed=4)
        np.testing.assert_array_equal(table.odds, odds_before)
        np.testing.assert_array_equal(table.ret, ret_before)
        self.assertGreater(result.noisy_roi_sd, 0.0)


class DescriptiveStatisticsTest(unittest.TestCase):
    def test_tail_concentration_reports_the_share_carried_by_the_largest_wins(self) -> None:
        races = 100
        ret = np.zeros((races, 1))
        ret[0, 0] = 300.0
        ret[1:6, 0] = 20.0
        table = _table(
            prob=np.ones((races, 1)),
            odds=np.full((races, 1), 50.0),
            ret=ret,
            combinations=("1",),
        )
        result = ap.tail_concentration(table, ap.select(table, "confidence"), resamples=50)
        self.assertAlmostEqual(result.roi, 4.0)
        self.assertAlmostEqual(result.top1_percent_share, 300.0 / 400.0)
        self.assertAlmostEqual(result.top10_share, 1.0)

    def test_monthly_stability_groups_by_calendar_month(self) -> None:
        dates = [dt.date(2026, 1, 5), dt.date(2026, 1, 20), dt.date(2026, 2, 3)]
        table = _table(
            prob=[[1.0], [1.0], [1.0]],
            odds=[[4.0], [4.0], [4.0]],
            ret=[[4.0], [0.0], [4.0]],
            dates=dates,
            combinations=("1",),
        )
        rows = ap.monthly_stability(table, ap.select(table, "confidence"))
        self.assertEqual([row.month for row in rows], ["2026-01", "2026-02"])
        self.assertAlmostEqual(rows[0].roi, 2.0)
        self.assertAlmostEqual(rows[1].roi, 4.0)

    def test_quote_realization_separates_an_exact_quote_from_a_range(self) -> None:
        """1.0 means the quote is the settlement price; above 1.0 means the
        stored quote is the low end of a quoted range, which makes an EV
        threshold read tighter than it is."""
        exact = _table(
            prob=[[1.0], [1.0]], odds=[[4.0], [7.0]], ret=[[4.0], [7.0]], combinations=("1",)
        )
        ranged = _table(
            prob=[[1.0], [1.0]], odds=[[4.0], [7.0]], ret=[[6.0], [7.0]], combinations=("1",)
        )
        self.assertAlmostEqual(ap.quote_realization(exact).mean_ratio, 1.0)
        self.assertAlmostEqual(ap.quote_realization(ranged).mean_ratio, 1.25)


class MarketRoundTest(BetTypeTestBase):
    """The live rounds, which are the only prices a bet could have been
    placed at."""

    def _add_live_odds(self, session, race, lead_minutes: float, odds: float) -> None:
        observed = race.scheduled_deadline_at - dt.timedelta(minutes=lead_minutes)
        for lane in range(1, 7):
            session.add(
                OddsSnapshot(
                    race_id=race.id,
                    bet_type="win",
                    combination=str(lane),
                    odds=odds if lane == 1 else 9.0,
                    observed_at=observed,
                    available_at=observed,
                    is_closing=False,
                )
            )

    def _build(self, market_round: str):
        with Session(self.engine) as session:
            return ap.build_candidate_tables(
                session,
                train_start=TRAIN_START,
                train_end=TRAIN_END,
                test_start=TEST_START,
                test_end=TEST_END,
                bet_types=("win",),
                market_round=market_round,
            )["win"]

    def setUp(self) -> None:
        super().setUp()
        with Session(self.engine) as session:
            self._fill_training(session)
            race = self._add_race(
                session,
                TEST_START,
                1,
                winner=1,
                second=2,
                win_odds={lane: 3.0 for lane in range(1, 7)},
                payouts=[("単勝", "1", 300)],
            )
            self._add_live_odds(session, race, 61, 20.0)
            self._add_live_odds(session, race, 11, 12.0)
            self._add_live_odds(session, race, 4, 6.0)
            self._add_live_odds(session, race, 2, 5.0)
            session.commit()

    def test_each_round_reads_its_own_window(self) -> None:
        self.assertAlmostEqual(self._build("t-60").odds[0][0], 20.0)
        self.assertAlmostEqual(self._build("t-10").odds[0][0], 12.0)

    def test_a_round_keeps_the_last_quote_inside_its_window(self) -> None:
        """Two captures fall inside the t-2 window (4 and 2 minutes out).
        The later one is what a bettor would have seen last."""
        self.assertAlmostEqual(self._build("t-2").odds[0][0], 5.0)

    def test_closing_ignores_the_live_capture(self) -> None:
        self.assertAlmostEqual(self._build("closing").odds[0][0], 3.0)

    def test_unknown_round_is_rejected(self) -> None:
        with self.assertRaises(ap.AttributionError):
            self._build("t-30")

    def test_overlapping_train_and_test_windows_are_rejected(self) -> None:
        with Session(self.engine) as session, self.assertRaises(ap.AttributionError):
            ap.build_candidate_tables(
                session,
                train_start=TRAIN_START,
                train_end=TEST_END,
                test_start=TEST_START,
                test_end=TEST_END,
                bet_types=("win",),
            )


class RuleResultsMatchEvaluateBetTypesTest(BetTypeTestBase):
    """The reproduction guarantee. Every attribution below is an argument
    about a published figure, so this module's rules have to be the same
    rules -- same probabilities, same settlement, same bets."""

    def setUp(self) -> None:
        super().setUp()
        with Session(self.engine) as session:
            self._fill_training(session)
            day = TEST_START
            for index in range(12):
                winner = (index % 6) + 1
                self._add_race(
                    session,
                    day,
                    (index % 3) + 1,
                    winner=winner,
                    second=(winner % 6) + 1,
                    win_odds={lane: 2.0 + lane for lane in range(1, 7)},
                    place_odds={lane: 1.2 + 0.4 * lane for lane in range(1, 7)},
                    payouts=[
                        ("単勝", str(winner), int(100 * (2.0 + winner))),
                        ("複勝", str(winner), int(100 * (1.2 + 0.4 * winner))),
                        ("複勝", str((winner % 6) + 1), 150),
                    ],
                )
                if index % 3 == 2:
                    day += dt.timedelta(days=1)
            session.commit()

    def test_every_rule_matches_bet_for_bet(self) -> None:
        thresholds = (1.0, 1.2, 1.5)
        with Session(self.engine) as session:
            published = ebt.evaluate_bet_types(
                session,
                train_start=TRAIN_START,
                train_end=TRAIN_END,
                test_start=TEST_START,
                test_end=TEST_END,
                bet_types=("win", "place"),
                ev_thresholds=thresholds,
            )
            report = ap.attribute(
                session,
                train_start=TRAIN_START,
                train_end=TRAIN_END,
                test_start=TEST_START,
                test_end=TEST_END,
                bet_types=("win", "place"),
                thresholds=thresholds,
                permutations=0,
                noise_draws=0,
            )

        mine = {(r.bet_type, r.rule, r.threshold): r for pool in report.pools for r in pool.rules}
        compared = 0
        for rule in published.rules:
            key = (rule.bet_type, rule.rule, rule.threshold)
            if key not in mine:
                continue
            compared += 1
            self.assertEqual(mine[key].bets, rule.bets, key)
            self.assertEqual(mine[key].hits, rule.hits, key)
            self.assertEqual(mine[key].races, rule.races, key)
            self.assertAlmostEqual(mine[key].roi, rule.roi, places=9, msg=key)
            if rule.bets > ap.TRIM_TOP_PAYOUTS:
                self.assertAlmostEqual(
                    mine[key].trimmed_roi, rule.trimmed_roi, places=9, msg=key
                )
        self.assertGreaterEqual(compared, 14)
        self.assertGreater(sum(r.bets for r in mine.values()), 0)


if __name__ == "__main__":
    unittest.main()
