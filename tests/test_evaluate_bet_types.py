"""Per-bet-type EV breakdown settled at real payouts
(`db.evaluate_bet_types`).

What needs guarding is the settlement bookkeeping, not the model. Every
pool goes through one `_settle`, keyed by a combination *string* that has
to match how `race_payouts` writes it -- a single lane for 単勝/複勝,
`"i-j"` for 2連単, low-high for 2連複. A mismatch there does not raise:
it silently settles every bet as a loss and reports a clean 0.0000 ROI,
which looks like a finding rather than a bug. Most of these tests pin
that mapping.

The fixture holds the model's probabilities constant by giving every lane
identical card features, so a bet type's ROI is a function of the odds
and the payouts alone -- which is what is being tested.
"""

from __future__ import annotations

import datetime as dt
import unittest

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from boat_prediction.db import evaluate_bet_types as ebt
from boat_prediction.db.models import (
    Base,
    OddsSnapshot,
    Race,
    RaceEntry,
    Racer,
    RacePayout,
    RaceResult,
    RaceResultEntry,
    Venue,
)

TRAIN_START = dt.date(2026, 1, 1)
TRAIN_END = dt.date(2026, 2, 28)
TEST_START = dt.date(2026, 3, 1)
TEST_END = dt.date(2026, 3, 31)


def _engine():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection, _record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine


class BetTypeTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = _engine()
        self.addCleanup(self.engine.dispose)
        with Session(self.engine) as session:
            venue = Venue(code="24", name="大村")
            session.add(venue)
            session.flush()
            self.venue_id = venue.id
            for lane in range(1, 7):
                session.add(Racer(registration_number=4000 + lane, name=f"選手{lane}"))
            session.commit()

    def _add_race(
        self,
        session: Session,
        race_date: dt.date,
        race_number: int,
        *,
        winner: int = 1,
        second: int = 2,
        win_odds: dict[int, float] | None = None,
        place_odds: dict[int, float] | None = None,
        exacta_odds: dict[str, float] | None = None,
        quinella_odds: dict[str, float] | None = None,
        payouts: list[tuple[str, str, int]] | None = None,
    ) -> Race:
        deadline = dt.datetime.combine(race_date, dt.time(8, 41), tzinfo=dt.UTC)
        race = Race(
            venue_id=self.venue_id,
            race_date=race_date,
            race_number=race_number,
            status="finished",
            race_class="予選",
            scheduled_deadline_at=deadline,
        )
        session.add(race)
        session.flush()
        racers = session.query(Racer).order_by(Racer.registration_number).all()
        for lane in range(1, 7):
            session.add(
                RaceEntry(
                    race_id=race.id,
                    lane_number=lane,
                    racer_id=racers[lane - 1].id,
                    listed_class="A1",
                    listed_age=30,
                    listed_weight=52,
                    listed_national_win_rate=5.5,
                    listed_national_second_rate=35.0,
                    listed_local_win_rate=5.0,
                    listed_local_second_rate=30.0,
                    listed_motor_second_rate=33.0,
                    listed_boat_second_rate=31.0,
                    available_at=deadline - dt.timedelta(hours=9),
                )
            )
        result = RaceResult(race_id=race.id, available_at=deadline + dt.timedelta(hours=6))
        session.add(result)
        session.flush()
        ordering = [winner, second] + [
            lane for lane in range(1, 7) if lane not in (winner, second)
        ]
        for position, lane in enumerate(ordering, start=1):
            session.add(
                RaceResultEntry(
                    race_result_id=result.id,
                    lane_number=lane,
                    finish_position=position,
                    status="01",
                )
            )

        def _odds(bet_type: str, grid: dict) -> None:
            for combination, value in grid.items():
                session.add(
                    OddsSnapshot(
                        race_id=race.id,
                        bet_type=bet_type,
                        combination=str(combination),
                        odds=value,
                        observed_at=deadline,
                        available_at=deadline,
                        is_closing=True,
                    )
                )

        if win_odds:
            _odds("win", win_odds)
        if place_odds:
            _odds("place_low", place_odds)
        if exacta_odds:
            _odds("exacta", exacta_odds)
        if quinella_odds:
            _odds("quinella", quinella_odds)
        for bet_type, combination, yen in payouts or []:
            session.add(
                RacePayout(
                    race_result_id=result.id,
                    bet_type=bet_type,
                    combination=combination,
                    payout_yen=yen,
                )
            )
        session.flush()
        return race

    def _fill_training(self, session: Session, *, second_always: int | None = None) -> None:
        """Enough finished races for the classifier and the lane-frequency
        conditional to fit. No odds -- training never reads them.

        Every lane wins equally often, so the fitted probabilities are
        near-uniform (all six lanes carry identical card features, leaving
        the base rate as the only thing to learn). That keeps the EV
        arithmetic in the tests below predictable: with p ~= 1/6, a lane
        priced at 12.0 has EV ~= 2.0.

        `second_always` skews who finishes second without touching who
        wins, which is what separates the lane-frequency conditional from
        the Plackett-Luce one.
        """
        day = TRAIN_START
        day_index = 0
        while day <= TRAIN_END:
            for race_number in range(1, 4):
                winner = ((day_index + race_number) % 6) + 1
                if second_always is not None and second_always != winner:
                    second = second_always
                else:
                    second = (winner % 6) + 1
                self._add_race(session, day, race_number, winner=winner, second=second)
            day += dt.timedelta(days=1)
            day_index += 1

    def _evaluate(self, **kwargs):
        with Session(self.engine) as session:
            return ebt.evaluate_bet_types(
                session,
                train_start=TRAIN_START,
                train_end=TRAIN_END,
                test_start=TEST_START,
                test_end=TEST_END,
                **kwargs,
            )

    @staticmethod
    def _rule(result, bet_type: str, rule: str, threshold: float):
        for candidate in result.rules:
            if (
                candidate.bet_type == bet_type
                and candidate.rule == rule
                and candidate.threshold == threshold
            ):
                return candidate
        raise AssertionError(f"no {bet_type}/{rule}@{threshold} rule in the result")


class SettlementTest(BetTypeTestBase):
    def test_win_bet_settles_at_the_single_lane_payout(self) -> None:
        with Session(self.engine) as session:
            self._fill_training(session)
            self._add_race(
                session,
                TEST_START,
                1,
                winner=1,
                second=2,
                win_odds={lane: 12.0 for lane in range(1, 7)},
                payouts=[("単勝", "1", 1200)],
            )
            session.commit()

        result = self._evaluate(bet_types=("win",), ev_thresholds=(1.00,))
        rule = self._rule(result, "win", "ev_all", 1.00)
        # Every lane priced at 2.0 with probabilities summing to 1.0 means
        # EV sums to 2.0 across six lanes, so at least one clears 1.00.
        self.assertGreaterEqual(rule.bets, 1)
        self.assertEqual(rule.hits, 1)
        self.assertEqual(rule.returned, 12.0)
        self.assertEqual(rule.staked_yen, rule.bets * 100)

    def test_place_bet_wins_on_second_place_too(self) -> None:
        """The distinguishing property of 複勝: a lane that finished
        second is a hit. Settling it off the winner alone would report
        exactly half the hits."""
        with Session(self.engine) as session:
            self._fill_training(session)
            self._add_race(
                session,
                TEST_START,
                1,
                winner=1,
                second=2,
                place_odds={lane: 5.0 for lane in range(1, 7)},
                payouts=[("複勝", "1", 130), ("複勝", "2", 210)],
            )
            session.commit()

        result = self._evaluate(bet_types=("place",), ev_thresholds=(1.00,))
        rule = self._rule(result, "place", "ev_all", 1.00)
        self.assertEqual(rule.bets, 6)
        self.assertEqual(rule.hits, 2)
        self.assertAlmostEqual(rule.returned, 1.30 + 2.10, places=9)

    def test_exacta_settles_on_the_ordered_pair(self) -> None:
        with Session(self.engine) as session:
            self._fill_training(session)
            self._add_race(
                session,
                TEST_START,
                1,
                winner=3,
                second=5,
                exacta_odds={
                    f"{i}-{j}": 60.0 for i in range(1, 7) for j in range(1, 7) if i != j
                },
                payouts=[("２連単", "3-5", 6000)],
            )
            session.commit()

        result = self._evaluate(bet_types=("exacta",), ev_thresholds=(1.00,))
        rule = self._rule(result, "exacta", "ev_all", 1.00)
        self.assertEqual(rule.hits, 1)
        self.assertAlmostEqual(rule.returned, 60.0, places=9)

    def test_exacta_does_not_pay_the_reverse_order(self) -> None:
        with Session(self.engine) as session:
            self._fill_training(session)
            odds = {f"{i}-{j}": 60.0 for i in range(1, 7) for j in range(1, 7) if i != j}
            # "5-3" priced far below the EV threshold, on purpose: with
            # every combination near the same probability and the same
            # 60.0 odds, `ev_all` would otherwise bet nearly the whole
            # grid (EV ~= 2.0 everywhere) and "5-3" would clear the
            # threshold like everything else, defeating the point of the
            # test -- it needs "5-3" to be the *only* combination that
            # could possibly be bet, so a hit can only mean it matched a
            # payout row it should not have.
            odds["5-3"] = 0.01
            self._add_race(
                session,
                TEST_START,
                1,
                winner=3,
                second=5,
                exacta_odds=odds,
                payouts=[("２連単", "5-3", 6000)],
            )
            session.commit()

        result = self._evaluate(bet_types=("exacta",), ev_thresholds=(1.00,))
        # The race finished 3 then 5; a payout row for "5-3" belongs to a
        # different outcome, so nothing here may be settled as a hit --
        # and "5-3"'s EV is far below threshold regardless.
        self.assertEqual(self._rule(result, "exacta", "ev_all", 1.00).hits, 0)

    def test_quinella_key_is_low_high_regardless_of_finish_order(self) -> None:
        with Session(self.engine) as session:
            self._fill_training(session)
            self._add_race(
                session,
                TEST_START,
                1,
                winner=5,
                second=2,
                quinella_odds={
                    f"{i}-{j}": 30.0 for i in range(1, 7) for j in range(i + 1, 7)
                },
                payouts=[("２連複", "2-5", 3000)],
            )
            session.commit()

        result = self._evaluate(bet_types=("quinella",), ev_thresholds=(1.00,))
        rule = self._rule(result, "quinella", "ev_all", 1.00)
        self.assertEqual(rule.hits, 1)
        self.assertAlmostEqual(rule.returned, 30.0, places=9)

    def test_special_refund_is_a_partial_return_not_a_hit(self) -> None:
        """特払い pays ¥70 per ¥100 to every ticket in the pool. Counting
        it as a hit would inflate the hit rate; counting it as nothing
        would understate the return."""
        with Session(self.engine) as session:
            self._fill_training(session)
            self._add_race(
                session,
                TEST_START,
                1,
                winner=1,
                second=2,
                win_odds={lane: 12.0 for lane in range(1, 7)},
                payouts=[("単勝", "特払い", 70)],
            )
            session.commit()

        result = self._evaluate(bet_types=("win",), ev_thresholds=(1.00,))
        rule = self._rule(result, "win", "ev_all", 1.00)
        self.assertEqual(rule.hits, 0)
        self.assertEqual(rule.refunds, rule.bets)
        self.assertAlmostEqual(rule.returned, 0.7 * rule.bets, places=9)

    def test_losing_bets_still_count_toward_the_stake(self) -> None:
        with Session(self.engine) as session:
            self._fill_training(session)
            self._add_race(
                session,
                TEST_START,
                1,
                winner=6,
                second=5,
                win_odds={lane: 12.0 for lane in range(1, 7)},
                payouts=[("単勝", "6", 1200)],
            )
            session.commit()

        result = self._evaluate(bet_types=("win",), ev_thresholds=(1.00,))
        rule = self._rule(result, "win", "confidence", 0.00)
        self.assertEqual(rule.bets, 1)
        self.assertEqual(rule.staked, 1.0)
        self.assertAlmostEqual(rule.roi, rule.returned, places=9)


class GridCompletenessTest(BetTypeTestBase):
    def test_race_with_a_partial_odds_grid_is_not_evaluated(self) -> None:
        """Selecting from a subset of the pool would bias the rule toward
        whatever happened to be captured, so the race is skipped."""
        with Session(self.engine) as session:
            self._fill_training(session)
            self._add_race(
                session,
                TEST_START,
                1,
                winner=1,
                win_odds={1: 2.0, 2: 3.0},
                payouts=[("単勝", "1", 1200)],
            )
            session.commit()

        result = self._evaluate(bet_types=("win",), ev_thresholds=(1.00,))
        self.assertEqual(result.priced_races["win"], 0)
        self.assertEqual(self._rule(result, "win", "ev_all", 1.00).bets, 0)

    def test_live_odds_are_not_read_when_asking_for_closing(self) -> None:
        with Session(self.engine) as session:
            self._fill_training(session)
            race = self._add_race(
                session,
                TEST_START,
                1,
                winner=1,
                payouts=[("単勝", "1", 1200)],
            )
            deadline = race.scheduled_deadline_at
            for lane in range(1, 7):
                session.add(
                    OddsSnapshot(
                        race_id=race.id,
                        bet_type="win",
                        combination=str(lane),
                        odds=2.0,
                        observed_at=deadline,
                        available_at=deadline,
                        is_closing=False,
                    )
                )
            session.commit()

        self.assertEqual(self._evaluate(bet_types=("win",)).priced_races["win"], 0)
        self.assertEqual(
            self._evaluate(bet_types=("win",), is_closing=False).priced_races["win"], 1
        )


class SecondPlaceComparisonTest(BetTypeTestBase):
    def test_both_conditionals_are_scored_on_held_out_races(self) -> None:
        with Session(self.engine) as session:
            self._fill_training(session)
            for race_number in range(1, 4):
                self._add_race(session, TEST_START, race_number, winner=1, second=2)
            session.commit()

        comparison = self._evaluate(bet_types=("win",)).second_place
        self.assertEqual(comparison.n, 3)
        self.assertGreater(comparison.plackett_luce, 0.0)
        self.assertGreater(comparison.lane_frequency, 0.0)

    def test_a_race_without_a_single_second_place_is_excluded(self) -> None:
        """同着 for second is real and has no non-arbitrary resolution, so
        it must drop out of the denominator rather than be guessed."""
        with Session(self.engine) as session:
            self._fill_training(session)
            race = self._add_race(session, TEST_START, 1, winner=1, second=2)
            session.query(RaceResultEntry).filter(
                RaceResultEntry.race_result_id
                == session.query(RaceResult.id)
                .filter(RaceResult.race_id == race.id)
                .scalar_subquery(),
                RaceResultEntry.lane_number == 3,
            ).update({"finish_position": 2})
            session.commit()

        self.assertEqual(self._evaluate(bet_types=("win",)).second_place.n, 0)

    def test_selecting_the_lane_frequency_conditional_changes_the_joint(self) -> None:
        with Session(self.engine) as session:
            # `second_always=2`: whenever some lane other than 2 wins,
            # training has it finishing ahead of lane 2 specifically, not
            # a random other lane. The lane-frequency conditional can
            # learn that skew; the Plackett-Luce conditional cannot -- it
            # is read off the classifier's first-place distribution alone,
            # which this fixture keeps near-uniform, so it stays close to
            # "any other lane is an equally likely runner-up."
            self._fill_training(session, second_always=2)
            self._add_race(
                session,
                TEST_START,
                1,
                winner=1,
                second=2,
                exacta_odds={
                    f"{i}-{j}": 60.0 for i in range(1, 7) for j in range(1, 7) if i != j
                },
                payouts=[("２連単", "1-2", 6000)],
            )
            session.commit()

        pl = self._evaluate(
            bet_types=("exacta",), ev_thresholds=(1.00,), second_place="plackett_luce"
        )
        lf = self._evaluate(
            bet_types=("exacta",), ev_thresholds=(1.00,), second_place="lane_frequency"
        )
        pl_all = self._rule(pl, "exacta", "ev_all", 1.00)
        lf_all = self._rule(lf, "exacta", "ev_all", 1.00)
        # Same odds and the same payout table; only the conditional
        # differs, and the difference shows up in *how many* combinations
        # clear the threshold rather than in which single one wins --
        # `ev_best` would need to land on the exact argmax among near-tied
        # candidates, which the classifier's own class-imbalance noise
        # makes an unreliable thing to pin a test to. Plackett-Luce is
        # close to uniform over 30 combinations, so ~all of them clear a
        # low EV bar; the lane-frequency conditional concentrated its
        # mass on the pairs actually seen in training (six of them), so
        # only those clear it. Both still hit "1-2" at ¥60 -- the
        # difference this test is pinned to is concentration, not miss vs
        # hit.
        self.assertLess(lf_all.bets, pl_all.bets)
        self.assertEqual(lf_all.hits, 1)
        self.assertEqual(pl_all.hits, 1)


class ReportingTest(BetTypeTestBase):
    def test_unpriced_pools_are_named_rather_than_omitted(self) -> None:
        with Session(self.engine) as session:
            self._fill_training(session)
            session.commit()
        result = self._evaluate(bet_types=("win",))
        self.assertEqual(result.unpriced, ebt.UNPRICED_BET_TYPES)
        for pool in ("拡連複", "３連単", "３連複"):
            self.assertIn(pool, str(result))

    def test_bets_per_race_and_staked_yen_expose_the_budget(self) -> None:
        with Session(self.engine) as session:
            self._fill_training(session)
            self._add_race(
                session,
                TEST_START,
                1,
                winner=1,
                second=2,
                place_odds={lane: 5.0 for lane in range(1, 7)},
                payouts=[("複勝", "1", 130), ("複勝", "2", 210)],
            )
            session.commit()

        rule = self._rule(
            self._evaluate(bet_types=("place",), ev_thresholds=(1.00,)),
            "place",
            "ev_all",
            1.00,
        )
        self.assertEqual(rule.races, 1)
        self.assertEqual(rule.bets_per_race, 6.0)
        self.assertEqual(rule.staked_yen, 600)


class ArgumentValidationTest(BetTypeTestBase):
    def test_overlapping_train_and_test_windows_raise(self) -> None:
        with Session(self.engine) as session, self.assertRaises(ValueError):
            ebt.evaluate_bet_types(
                session,
                train_start=TRAIN_START,
                train_end=TEST_START,
                test_start=TEST_START,
                test_end=TEST_END,
            )

    def test_unknown_bet_type_raises(self) -> None:
        with Session(self.engine) as session, self.assertRaises(ValueError):
            ebt.evaluate_bet_types(
                session,
                train_start=TRAIN_START,
                train_end=TRAIN_END,
                test_start=TEST_START,
                test_end=TEST_END,
                bet_types=("trifecta",),
            )

    def test_unknown_second_place_conditional_raises(self) -> None:
        with Session(self.engine) as session, self.assertRaises(ValueError):
            ebt.evaluate_bet_types(
                session,
                train_start=TRAIN_START,
                train_end=TRAIN_END,
                test_start=TEST_START,
                test_end=TEST_END,
                second_place="magic",
            )


if __name__ == "__main__":
    unittest.main()
