"""Tests for `db.dataset` and `db.evaluate_p1`.

The SQL runs against SQLite so a non-portable query fails here rather
than on the host, and every exclusion rule is exercised with the shape
that triggers it.
"""

from __future__ import annotations

import datetime as dt
import unittest

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from boat_prediction.db import dataset, evaluate_p1
from boat_prediction.db.models import (
    Base,
    Race,
    RaceEntry,
    Racer,
    RaceMeeting,
    RaceResult,
    RaceResultEntry,
    Venue,
)

DEADLINE_HOUR = 8


def _engine():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection, _record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine


class DatasetTestBase(unittest.TestCase):
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
        winner_lanes: tuple[int, ...] = (1,),
        lanes: tuple[int, ...] = (1, 2, 3, 4, 5, 6),
        entry_available_at: dt.datetime | None = None,
        missing_class: bool = False,
        status: str = "finished",
        race_class: str = "予選",
        meeting_id=None,
        lane_racers: dict[int, object] | None = None,
        dnf_lanes: tuple[int, ...] = (),
    ) -> Race:
        deadline = dt.datetime.combine(
            race_date, dt.time(DEADLINE_HOUR, 41), tzinfo=dt.UTC
        )
        race = Race(
            venue_id=self.venue_id,
            race_date=race_date,
            race_number=race_number,
            status=status,
            scheduled_deadline_at=deadline,
            race_class=race_class,
            meeting_id=meeting_id,
        )
        session.add(race)
        session.flush()
        racers = session.query(Racer).order_by(Racer.registration_number).all()
        racer_for_lane = {lane: racers[lane - 1] for lane in lanes}
        if lane_racers:
            racer_for_lane.update(lane_racers)
        for lane in lanes:
            session.add(
                RaceEntry(
                    race_id=race.id,
                    lane_number=lane,
                    racer_id=racer_for_lane[lane].id,
                    listed_class=None if missing_class else "A1",
                    listed_age=30,
                    listed_weight=52,
                    listed_national_win_rate=5.5,
                    listed_national_second_rate=35.0,
                    listed_local_win_rate=5.0,
                    listed_local_second_rate=30.0,
                    listed_motor_second_rate=33.0,
                    listed_boat_second_rate=31.0,
                    available_at=entry_available_at
                    or (deadline - dt.timedelta(hours=9)),
                )
            )
        result = RaceResult(race_id=race.id, available_at=deadline + dt.timedelta(hours=6))
        session.add(result)
        session.flush()
        next_position = 1 + len(winner_lanes)
        for lane in lanes:
            if lane in dnf_lanes:
                position = None
            elif lane in winner_lanes:
                position = 1
            else:
                position = next_position
                next_position += 1
            session.add(
                RaceResultEntry(
                    race_result_id=result.id,
                    lane_number=lane,
                    finish_position=position,
                    status="F" if lane in dnf_lanes else "01",
                )
            )
        session.flush()
        return race


class BuildDatasetTest(DatasetTestBase):
    def test_builds_one_row_per_race_six_lanes_wide(self) -> None:
        with Session(self.engine) as session:
            self._add_race(session, dt.date(2026, 1, 5), 1)
            session.commit()

            data = dataset.build_dataset(
                session, start_date=dt.date(2026, 1, 1), end_date=dt.date(2026, 1, 31)
            )

            self.assertEqual(len(data), 1)
            self.assertEqual(
                len(data.X[0]), 6 * len(dataset.FEATURE_NAMES) + len(dataset.GLOBAL_FEATURE_NAMES)
            )
            self.assertEqual(data.y, [1])
            self.assertEqual(data.dates, [dt.date(2026, 1, 5)])
            self.assertEqual(data.phases, ["trial"])
            self.assertEqual(data.feature_names[0], "lane1_national_win_rate")
            self.assertEqual(data.feature_names[-1], "is_standing_seeded")

    def test_excludes_a_dead_heat(self) -> None:
        # Two boats on first place have no single winning lane, and
        # picking one would invent a label.
        with Session(self.engine) as session:
            self._add_race(session, dt.date(2026, 1, 5), 1, winner_lanes=(1, 2))
            session.commit()

            data = dataset.build_dataset(
                session, start_date=dt.date(2026, 1, 1), end_date=dt.date(2026, 1, 31)
            )

            self.assertEqual(len(data), 0)
            self.assertEqual(data.stats.dropped_no_single_winner, 1)

    def test_excludes_a_card_without_six_lanes(self) -> None:
        with Session(self.engine) as session:
            self._add_race(session, dt.date(2026, 1, 5), 1, lanes=(1, 2, 3, 4, 5))
            session.commit()

            data = dataset.build_dataset(
                session, start_date=dt.date(2026, 1, 1), end_date=dt.date(2026, 1, 31)
            )

            self.assertEqual(data.stats.dropped_not_six_lanes, 1)

    def test_excludes_a_feature_available_after_the_deadline(self) -> None:
        # The guard exists so a loader change cannot silently start
        # training on the future.
        with Session(self.engine) as session:
            deadline = dt.datetime.combine(
                dt.date(2026, 1, 5), dt.time(DEADLINE_HOUR, 41), tzinfo=dt.UTC
            )
            self._add_race(
                session,
                dt.date(2026, 1, 5),
                1,
                entry_available_at=deadline + dt.timedelta(minutes=1),
            )
            session.commit()

            data = dataset.build_dataset(
                session, start_date=dt.date(2026, 1, 1), end_date=dt.date(2026, 1, 31)
            )

            self.assertEqual(len(data), 0)
            self.assertEqual(data.stats.dropped_late_feature, 1)

    def test_excludes_a_row_with_a_missing_feature_rather_than_imputing(self) -> None:
        with Session(self.engine) as session:
            self._add_race(session, dt.date(2026, 1, 5), 1, missing_class=True)
            session.commit()

            data = dataset.build_dataset(
                session, start_date=dt.date(2026, 1, 1), end_date=dt.date(2026, 1, 31)
            )

            self.assertEqual(data.stats.dropped_missing_feature, 1)

    def test_excludes_races_outside_the_range_and_unfinished_ones(self) -> None:
        with Session(self.engine) as session:
            self._add_race(session, dt.date(2026, 1, 5), 1)
            self._add_race(session, dt.date(2026, 2, 5), 1)
            self._add_race(session, dt.date(2026, 1, 6), 1, status="scheduled")
            session.commit()

            data = dataset.build_dataset(
                session, start_date=dt.date(2026, 1, 1), end_date=dt.date(2026, 1, 31)
            )

            self.assertEqual(len(data), 1)
            self.assertEqual(data.dates, [dt.date(2026, 1, 5)])

    def test_rows_come_back_in_date_order(self) -> None:
        with Session(self.engine) as session:
            for day in (20, 3, 11):
                self._add_race(session, dt.date(2026, 1, day), 1)
            session.commit()

            data = dataset.build_dataset(
                session, start_date=dt.date(2026, 1, 1), end_date=dt.date(2026, 1, 31)
            )

            self.assertEqual(data.dates, sorted(data.dates))

    def test_rejects_a_reversed_range(self) -> None:
        with Session(self.engine) as session, self.assertRaises(ValueError):
            dataset.build_dataset(
                session, start_date=dt.date(2026, 2, 1), end_date=dt.date(2026, 1, 1)
            )


class MeetingFormAndPhaseTest(DatasetTestBase):
    """The within-meeting form features and the series-phase column.

    Meeting-history races are placed *before* `start_date` (inside the
    `MEETING_WINDOW_MARGIN_DAYS` margin) so the target race under test is
    the only one the outer query returns -- exercising the margin
    mechanism itself, not just the shrinkage math.
    """

    def _lane_value(self, data: "dataset.Dataset", lane: int, name: str) -> float:
        index = (lane - 1) * len(dataset.FEATURE_NAMES) + dataset.FEATURE_NAMES.index(name)
        return data.X[0][index]

    def _build(self, session: Session, start_date: dt.date, end_date: dt.date):
        return dataset.build_dataset(session, start_date=start_date, end_date=end_date)

    def test_no_prior_meeting_races_falls_back_exactly_to_the_season_prior(self) -> None:
        with Session(self.engine) as session:
            meeting = RaceMeeting(venue_id=self.venue_id, meeting_start_date=dt.date(2026, 1, 8))
            session.add(meeting)
            session.flush()
            self._add_race(session, dt.date(2026, 1, 8), 1, meeting_id=meeting.id)
            session.commit()

            data = self._build(session, dt.date(2026, 1, 8), dt.date(2026, 1, 8))

            self.assertEqual(self._lane_value(data, 1, "meeting_starts"), 0.0)
            # listed_national_win_rate is 5.5 in every fixture entry.
            self.assertAlmostEqual(self._lane_value(data, 1, "meeting_form_score"), 0.55)

    def test_a_within_meeting_win_shrinks_the_form_score_toward_it(self) -> None:
        with Session(self.engine) as session:
            meeting = RaceMeeting(venue_id=self.venue_id, meeting_start_date=dt.date(2026, 1, 5))
            session.add(meeting)
            session.flush()
            self._add_race(
                session, dt.date(2026, 1, 5), 1, meeting_id=meeting.id, winner_lanes=(1,)
            )
            self._add_race(session, dt.date(2026, 1, 9), 1, meeting_id=meeting.id)
            session.commit()

            data = self._build(session, dt.date(2026, 1, 8), dt.date(2026, 1, 15))

            # score_day1 for the winner = (7-1)/6 = 1.0; shrunk with
            # MEETING_FORM_SHRINKAGE_STARTS=3 and season_prior=0.55:
            # (1.0*1 + 0.55*3) / (1+3) = 0.6625.
            self.assertEqual(self._lane_value(data, 1, "meeting_starts"), 1.0)
            self.assertAlmostEqual(self._lane_value(data, 1, "meeting_form_score"), 0.6625)
            self.assertGreater(self._lane_value(data, 1, "meeting_form_score"), 0.55)

    def test_a_non_finish_scores_as_the_worst_outcome_not_as_missing(self) -> None:
        # A DNF is worse evidence than finishing last, and dropping it
        # would selectively delete the bad news.
        with Session(self.engine) as session:
            meeting = RaceMeeting(venue_id=self.venue_id, meeting_start_date=dt.date(2026, 1, 5))
            session.add(meeting)
            session.flush()
            self._add_race(
                session, dt.date(2026, 1, 5), 1, meeting_id=meeting.id, dnf_lanes=(1,)
            )
            self._add_race(session, dt.date(2026, 1, 9), 1, meeting_id=meeting.id)
            session.commit()

            data = self._build(session, dt.date(2026, 1, 8), dt.date(2026, 1, 15))

            self.assertEqual(self._lane_value(data, 1, "meeting_starts"), 1.0)
            # score_day1 = 0.0; shrunk = (0*1 + 0.55*3)/4 = 0.4125 < 0.55.
            self.assertAlmostEqual(self._lane_value(data, 1, "meeting_form_score"), 0.4125)
            self.assertLess(self._lane_value(data, 1, "meeting_form_score"), 0.55)

    def test_a_cancelled_earlier_day_does_not_count_as_a_start(self) -> None:
        with Session(self.engine) as session:
            meeting = RaceMeeting(venue_id=self.venue_id, meeting_start_date=dt.date(2026, 1, 5))
            session.add(meeting)
            session.flush()
            cancelled = self._add_race(
                session, dt.date(2026, 1, 5), 1, meeting_id=meeting.id, status="cancelled"
            )
            session.query(RaceResult).filter(RaceResult.race_id == cancelled.id).delete()
            session.query(RaceResultEntry).filter(
                RaceResultEntry.race_result_id.in_(
                    session.query(RaceResult.id).filter(RaceResult.race_id == cancelled.id)
                )
            ).delete(synchronize_session=False)
            self._add_race(session, dt.date(2026, 1, 9), 1, meeting_id=meeting.id)
            session.commit()

            data = self._build(session, dt.date(2026, 1, 8), dt.date(2026, 1, 15))

            self.assertEqual(self._lane_value(data, 1, "meeting_starts"), 0.0)

    def test_form_follows_the_racer_across_a_lane_reseed(self) -> None:
        # 準優勝戦/優勝戦 reseed by standing, so a racer's lane can change
        # between days of the same 節. Tracked by racer_id, not lane.
        with Session(self.engine) as session:
            racers = session.query(Racer).order_by(Racer.registration_number).all()
            meeting = RaceMeeting(venue_id=self.venue_id, meeting_start_date=dt.date(2026, 1, 5))
            session.add(meeting)
            session.flush()
            # Day 1: this racer is in lane 1 and wins.
            self._add_race(
                session, dt.date(2026, 1, 5), 1, meeting_id=meeting.id, winner_lanes=(1,)
            )
            # Day 2 (the target race): the same racer has been reseeded to
            # lane 3; lane 1 now holds a different racer.
            self._add_race(
                session,
                dt.date(2026, 1, 9),
                1,
                meeting_id=meeting.id,
                lane_racers={1: racers[2], 3: racers[0]},
            )
            session.commit()

            data = self._build(session, dt.date(2026, 1, 8), dt.date(2026, 1, 15))

            # Lane 3 today is racers[0], who won day 1 from lane 1:
            # score 1.0, shrunk to 0.6625.
            self.assertEqual(self._lane_value(data, 3, "meeting_starts"), 1.0)
            self.assertAlmostEqual(self._lane_value(data, 3, "meeting_form_score"), 0.6625)
            # Lane 1 today is racers[2], who finished 3rd from lane 3 on
            # day 1 (score (7-3)/6): a different, independently-tracked
            # history, not "no history" and not lane 3's history either --
            # the strongest evidence the swap is followed by racer_id.
            self.assertEqual(self._lane_value(data, 1, "meeting_starts"), 1.0)
            self.assertAlmostEqual(
                self._lane_value(data, 1, "meeting_form_score"),
                ((7 - 3) / 6 * 1 + 0.55 * 3) / 4,
            )

    def test_a_different_meeting_does_not_leak_in(self) -> None:
        with Session(self.engine) as session:
            racers = session.query(Racer).order_by(Racer.registration_number).all()
            other_meeting = RaceMeeting(
                venue_id=self.venue_id, meeting_start_date=dt.date(2025, 12, 1)
            )
            this_meeting = RaceMeeting(
                venue_id=self.venue_id, meeting_start_date=dt.date(2026, 1, 5)
            )
            session.add_all([other_meeting, this_meeting])
            session.flush()
            self._add_race(
                session,
                dt.date(2026, 1, 5),
                1,
                meeting_id=other_meeting.id,
                winner_lanes=(1,),
            )
            self._add_race(session, dt.date(2026, 1, 9), 1, meeting_id=this_meeting.id)
            session.commit()

            data = self._build(session, dt.date(2026, 1, 8), dt.date(2026, 1, 15))

            self.assertEqual(self._lane_value(data, 1, "meeting_starts"), 0.0)

    def test_a_final_is_flagged_standing_seeded(self) -> None:
        with Session(self.engine) as session:
            self._add_race(session, dt.date(2026, 1, 5), 12, race_class="優勝戦")
            session.commit()

            data = self._build(session, dt.date(2026, 1, 5), dt.date(2026, 1, 5))

            self.assertEqual(data.phases, ["final"])
            self.assertEqual(data.X[0][-1], 1.0)

    def test_a_trial_is_not_standing_seeded(self) -> None:
        with Session(self.engine) as session:
            self._add_race(session, dt.date(2026, 1, 5), 1, race_class="予選")
            session.commit()

            data = self._build(session, dt.date(2026, 1, 5), dt.date(2026, 1, 5))

            self.assertEqual(data.phases, ["trial"])
            self.assertEqual(data.X[0][-1], 0.0)


class EvaluateP1Test(DatasetTestBase):
    def test_runs_walk_forward_over_real_rows(self) -> None:
        with Session(self.engine) as session:
            # Three months, lane 1 winning more often than the rest, so
            # lane_prior has something to learn that uniform does not.
            for month in (1, 2, 3):
                for day in range(1, 13):
                    # every lane wins at least once per month, lane 1 most
                    winner = 1 if day <= 7 else day - 6
                    self._add_race(
                        session, dt.date(2026, month, day), 1, winner_lanes=(winner,)
                    )
            session.commit()

            result = evaluate_p1.evaluate(
                session,
                start_date=dt.date(2026, 1, 1),
                end_date=dt.date(2026, 3, 31),
                min_train_months=1,
            )

            self.assertEqual(result.n_races, 36)
            self.assertEqual(result.n_folds, 2)
            self.assertIn("uniform", result.mean_log_loss)
            self.assertIn("lane_prior", result.mean_log_loss)
            # The point of the run: the informed baseline must beat 1/6.
            self.assertLess(
                result.mean_log_loss["lane_prior"], result.mean_log_loss["uniform"]
            )
            self.assertIn("mean log-loss", evaluate_p1.render(result))

    def test_reports_an_empty_range_rather_than_producing_a_meaningless_score(
        self,
    ) -> None:
        with Session(self.engine) as session, self.assertRaises(ValueError):
            evaluate_p1.evaluate(
                session, start_date=dt.date(2030, 1, 1), end_date=dt.date(2030, 12, 31)
            )


if __name__ == "__main__":
    unittest.main()
