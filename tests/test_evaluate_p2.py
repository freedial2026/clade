"""Expected-value selection settled at real payouts (`db.evaluate_p2`).

The arithmetic is what needs guarding here. `roi` is a ratio of two
accumulators, `ev_all` can place several bets on one race, and a bet is
only a hit when the lane it named actually won -- each of those is a
place where a plausible number can come out of wrong bookkeeping.
"""

from __future__ import annotations

import datetime as dt
import unittest

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from boat_prediction.db import evaluate_p2
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


class EvaluateP2TestBase(unittest.TestCase):
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
        odds: dict[int, float] | None = None,
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
        position = 2
        for lane in range(1, 7):
            if lane == winner:
                finish = 1
            else:
                finish = position
                position += 1
            session.add(
                RaceResultEntry(
                    race_result_id=result.id,
                    lane_number=lane,
                    finish_position=finish,
                    status="01",
                )
            )
        if odds:
            for lane, value in odds.items():
                session.add(
                    OddsSnapshot(
                        race_id=race.id,
                        bet_type="win",
                        combination=str(lane),
                        odds=value,
                        observed_at=deadline,
                        available_at=deadline,
                        is_closing=True,
                    )
                )
            session.add(
                RacePayout(
                    race_result_id=result.id,
                    bet_type="単勝",
                    combination=str(winner),
                    payout_yen=int(odds[winner] * 100),
                )
            )
        session.flush()
        return race

    def _train_window(self, session: Session) -> None:
        """Training races where lane 1 wins 80% and lane 2 the rest.

        Every race carries identical features, so the fit has nothing to
        separate them with and simply learns the base rates -- which is
        what these tests want: a *known* probability vector (~0.8 on lane
        1, ~0.2 on lane 2, ~0 elsewhere) so an expected value can be
        predicted by hand and the bookkeeping checked against it. An even
        1:1 split would leave the argmax to floating-point luck.

        Lanes 3-6 never win, so they are absent from training and their
        probability is 0 -- which also exercises the lane-alignment path.
        """
        day = TRAIN_START
        index = 0
        while day <= TRAIN_END:
            self._add_race(session, day, 1, winner=2 if index % 5 == 0 else 1)
            day += dt.timedelta(days=1)
            index += 1


class EvaluateP2Test(EvaluateP2TestBase):
    def test_refuses_overlapping_train_and_test_windows(self) -> None:
        """The failure this prevents scores the model on rows it was fit
        on and reports the result as a forward test."""
        with Session(self.engine) as session, self.assertRaises(ValueError):
            evaluate_p2.evaluate_p2(
                session,
                train_start=TRAIN_START,
                train_end=TEST_START,
                test_start=TEST_START,
                test_end=TEST_END,
            )

    def test_a_race_without_odds_is_not_bet_on(self) -> None:
        with Session(self.engine) as session:
            self._train_window(session)
            self._add_race(session, TEST_START, 1, winner=1)  # no odds
            session.commit()

            result = evaluate_p2.evaluate_p2(
                session,
                train_start=TRAIN_START,
                train_end=TRAIN_END,
                test_start=TEST_START,
                test_end=TEST_END,
            )

            self.assertEqual(result.test_races, 1)
            self.assertEqual(result.priced_races, 0)
            self.assertTrue(all(rule.bets == 0 for rule in result.rules))

    def test_roi_is_returns_over_stake_at_the_real_payout(self) -> None:
        """One race, one bet, a known payout: the ROI must be that payout
        and nothing else."""
        with Session(self.engine) as session:
            self._train_window(session)
            # p1 ~ 0.8, so lane 1 at 4.0 has EV ~ 3.2 and is the pick;
            # lane 2 at 2.0 has EV ~ 0.4, and lanes 3-6 have EV 0.
            self._add_race(
                session,
                TEST_START,
                1,
                winner=1,
                odds={1: 4.0, 2: 2.0, 3: 2.0, 4: 2.0, 5: 2.0, 6: 2.0},
            )
            session.commit()

            result = evaluate_p2.evaluate_p2(
                session,
                train_start=TRAIN_START,
                train_end=TRAIN_END,
                test_start=TEST_START,
                test_end=TEST_END,
                ev_thresholds=(1.0,),
                thresholds=(0.0,),
            )

            best = next(r for r in result.rules if r.rule == "ev_best")
            self.assertEqual(best.bets, 1)
            self.assertEqual(best.hits, 1)
            self.assertAlmostEqual(best.roi, 4.0, places=6)

    def test_a_losing_bet_returns_nothing_and_still_counts_as_staked(self) -> None:
        with Session(self.engine) as session:
            self._train_window(session)
            # Lane 6 wins; the model will not favour it, so whatever is
            # backed loses and ROI must be exactly zero, not undefined.
            self._add_race(
                session,
                TEST_START,
                1,
                winner=6,
                odds={1: 1.2, 2: 20.0, 3: 20.0, 4: 20.0, 5: 20.0, 6: 60.0},
            )
            session.commit()

            result = evaluate_p2.evaluate_p2(
                session,
                train_start=TRAIN_START,
                train_end=TRAIN_END,
                test_start=TEST_START,
                test_end=TEST_END,
                thresholds=(0.0,),
                ev_thresholds=(0.0,),
            )

            confidence = next(r for r in result.rules if r.rule == "confidence")
            self.assertEqual(confidence.bets, 1)
            self.assertEqual(confidence.hits, 0)
            self.assertEqual(confidence.roi, 0.0)

    def test_ev_all_can_place_several_bets_on_one_race(self) -> None:
        """The rule that follows from the arithmetic: if two lanes are both
        underpriced, both are worth backing, and the stake must count
        twice."""
        with Session(self.engine) as session:
            self._train_window(session)
            # EV ~ 1.6 on lane 1 and ~ 2.0 on lane 2; lanes 3-6 sit at 0
            # because they never won in training.
            self._add_race(
                session,
                TEST_START,
                1,
                winner=1,
                odds={1: 2.0, 2: 10.0, 3: 1.0, 4: 1.0, 5: 1.0, 6: 1.0},
            )
            session.commit()

            result = evaluate_p2.evaluate_p2(
                session,
                train_start=TRAIN_START,
                train_end=TRAIN_END,
                test_start=TEST_START,
                test_end=TEST_END,
                ev_thresholds=(1.0,),
            )

            ev_all = next(r for r in result.rules if r.rule == "ev_all")
            self.assertEqual(ev_all.bets, 2)
            self.assertEqual(ev_all.races, 1)
            self.assertEqual(ev_all.hits, 1)
            # Two stakes, one 2.0x return: the stake on the loser must
            # still count in the denominator.
            self.assertAlmostEqual(ev_all.roi, 2.0 / 2, places=6)

    def test_a_higher_ev_threshold_never_places_more_bets(self) -> None:
        with Session(self.engine) as session:
            self._train_window(session)
            for index in range(10):
                self._add_race(
                    session,
                    TEST_START + dt.timedelta(days=index),
                    1,
                    winner=1 + index % 3,
                    odds={1: 1.5, 2: 4.0, 3: 8.0, 4: 20.0, 5: 30.0, 6: 50.0},
                )
            session.commit()

            result = evaluate_p2.evaluate_p2(
                session,
                train_start=TRAIN_START,
                train_end=TRAIN_END,
                test_start=TEST_START,
                test_end=TEST_END,
                ev_thresholds=(1.0, 1.5, 5.0),
            )

            by_threshold = {
                r.threshold: r.bets for r in result.rules if r.rule == "ev_all"
            }
            self.assertGreaterEqual(by_threshold[1.0], by_threshold[1.5])
            self.assertGreaterEqual(by_threshold[1.5], by_threshold[5.0])

    def test_probabilities_are_read_by_lane_not_by_column_position(self) -> None:
        """A training window where some lane never wins shifts sklearn's
        columns. Reading them positionally would score later lanes against
        the wrong probability and raise nothing."""
        with Session(self.engine) as session:
            self._train_window(session)  # only lanes 1 and 2 ever win
            self._add_race(
                session,
                TEST_START,
                1,
                winner=1,
                odds=dict.fromkeys(range(1, 7), 3.0),
            )
            session.commit()

            result = evaluate_p2.evaluate_p2(
                session,
                train_start=TRAIN_START,
                train_end=TRAIN_END,
                test_start=TEST_START,
                test_end=TEST_END,
                thresholds=(0.0,),
                ev_thresholds=(1.0,),
            )

            self.assertEqual(result.priced_races, 1)
            confidence = next(r for r in result.rules if r.rule == "confidence")
            # Lanes 3-6 never won in training, so sklearn returns four
            # columns; read positionally, lane 4's probability would land
            # on lane 6. Lane 1 dominates training, so the argmax must be
            # lane 1 -- and it must hit, since lane 1 won.
            self.assertEqual(confidence.bets, 1)
            self.assertEqual(confidence.hits, 1)
            self.assertEqual(confidence.roi, 3.0)


if __name__ == "__main__":
    unittest.main()


class TrimmedRoiTest(unittest.TestCase):
    """`trimmed_roi` is what separated a real edge from a payout tail on
    the first real run, so it needs its own arithmetic check."""

    def _rule(self, payouts: list[float], bets: int) -> evaluate_p2.RuleResult:
        rule = evaluate_p2.RuleResult("ev_all", 1.0)
        rule.bets = bets
        rule.staked = float(bets)
        rule.hits = len(payouts)
        rule.returned = sum(payouts)
        rule.payouts = list(payouts)
        return rule

    def test_removes_the_largest_wins_and_their_stakes(self) -> None:
        # 100 bets, one 200x hit and ten 1x hits. Raw ROI is carried by
        # the single big one; trimmed must not be.
        rule = self._rule([200.0] + [1.0] * 10, bets=100)

        self.assertAlmostEqual(rule.roi, 210.0 / 100, places=6)
        # The ten largest are 200 and nine 1s -> 209 removed, 10 stakes.
        self.assertAlmostEqual(rule.trimmed_roi, (210.0 - 209.0) / 90, places=6)

    def test_a_flat_rule_is_barely_moved_by_trimming(self) -> None:
        rule = self._rule([2.0] * 50, bets=100)

        self.assertAlmostEqual(rule.roi, 1.0, places=6)
        self.assertAlmostEqual(rule.trimmed_roi, (100.0 - 20.0) / 90, places=6)

    def test_too_few_bets_to_trim_reports_nan_rather_than_a_number(self) -> None:
        rule = self._rule([5.0], bets=5)

        self.assertNotEqual(rule.trimmed_roi, rule.trimmed_roi)  # nan
