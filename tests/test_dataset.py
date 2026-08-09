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
    BeforeInfoEntry,
    Race,
    RaceEntry,
    RaceMeeting,
    Racer,
    RacerPeriodCourseStats,
    RacerPeriodStats,
    RaceResult,
    RaceResultEntry,
    Venue,
)

DEADLINE_HOUR = 8
LANES_FOR_TEST = (1, 2, 3, 4, 5, 6)


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

    def _add_before_info(
        self,
        session: Session,
        race: Race,
        *,
        exhibition_times: dict[int, float | None] | None = None,
        start_sts: dict[int, float | None] | None = None,
        tilts: dict[int, float | None] | None = None,
        courses: dict[int, int | None] | None = None,
        lanes: tuple[int, ...] = (1, 2, 3, 4, 5, 6),
        available_at: dt.datetime | None = None,
        observed_at: dt.datetime | None = None,
    ) -> None:
        """One race's 直前情報, defaulting to a complete and plausible set.

        Defaults give every lane a distinct exhibition time so a z-score
        test has something to measure; a test that cares about one field
        overrides only that field.
        """
        deadline = race.scheduled_deadline_at
        observed = observed_at or (deadline - dt.timedelta(minutes=20))
        for lane in lanes:
            session.add(
                BeforeInfoEntry(
                    race_id=race.id,
                    lane_number=lane,
                    exhibition_time_sec=(exhibition_times or {}).get(lane, 6.70 + lane * 0.01),
                    start_exhibition_st_sec=(start_sts or {}).get(lane, 0.15),
                    tilt_angle=(tilts or {}).get(lane, -0.5),
                    start_exhibition_course=(courses or {}).get(lane, lane),
                    observed_at=observed,
                    available_at=available_at or observed,
                )
            )
        session.flush()


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

    def _lane_value(self, data: dataset.Dataset, lane: int, name: str) -> float:
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

    def test_an_earlier_race_the_same_day_does_not_count_as_prior_form(self) -> None:
        """A racer runs twice on most days of a 節 -- 62.10% of
        (meeting, racer, day) groups in the real archive. Counting the
        earlier one would contradict `loader.results_available_at`, which
        treats a K-file result as unavailable until midnight JST the next
        day because the file carries no per-race confirmation time.
        """
        with Session(self.engine) as session:
            meeting = RaceMeeting(venue_id=self.venue_id, meeting_start_date=dt.date(2026, 1, 5))
            session.add(meeting)
            session.flush()
            # Same racer, same day, twice: race 1 then race 8.
            self._add_race(
                session, dt.date(2026, 1, 5), 1, meeting_id=meeting.id, winner_lanes=(1,)
            )
            self._add_race(
                session, dt.date(2026, 1, 5), 8, meeting_id=meeting.id, winner_lanes=(2,)
            )
            session.commit()

            data = self._build(session, dt.date(2026, 1, 5), dt.date(2026, 1, 5))

            # Neither race sees the other, whichever way the engine
            # ordered the tied rows -- so both fall back to the season
            # prior exactly.
            for row in range(len(data)):
                self.assertEqual(data.X[row][data.feature_names.index("lane1_meeting_starts")], 0.0)
                self.assertAlmostEqual(
                    data.X[row][data.feature_names.index("lane1_meeting_form_score")], 0.55
                )

    def test_both_of_a_days_races_count_on_the_following_day(self) -> None:
        """The flip side: excluding same-day races must not discard them
        permanently. Once the day is over, both are prior form."""
        with Session(self.engine) as session:
            meeting = RaceMeeting(venue_id=self.venue_id, meeting_start_date=dt.date(2026, 1, 5))
            session.add(meeting)
            session.flush()
            self._add_race(
                session, dt.date(2026, 1, 5), 1, meeting_id=meeting.id, winner_lanes=(1,)
            )
            self._add_race(
                session, dt.date(2026, 1, 5), 8, meeting_id=meeting.id, winner_lanes=(1,)
            )
            self._add_race(session, dt.date(2026, 1, 6), 1, meeting_id=meeting.id)
            session.commit()

            data = self._build(session, dt.date(2026, 1, 6), dt.date(2026, 1, 6))

            # Two wins on day 1 => scores 1.0 and 1.0, shrunk with
            # MEETING_FORM_SHRINKAGE_STARTS=3 and season_prior=0.55:
            # (1.0*2 + 0.55*3) / (2+3) = 0.73.
            self.assertEqual(self._lane_value(data, 1, "meeting_starts"), 2.0)
            self.assertAlmostEqual(self._lane_value(data, 1, "meeting_form_score"), 0.73)

    def test_the_same_window_builds_identically_twice(self) -> None:
        """`ORDER BY mw_race_date` with a `ROWS` frame made this false on
        PostgreSQL: the tie among a day's two races let the frame
        boundary move between runs, and two builds of the real window
        disagreed on 165,585 of 198,264 races."""
        with Session(self.engine) as session:
            meeting = RaceMeeting(venue_id=self.venue_id, meeting_start_date=dt.date(2026, 1, 5))
            session.add(meeting)
            session.flush()
            for day in (5, 6, 7):
                for race_number in (1, 8):
                    self._add_race(
                        session, dt.date(2026, 1, day), race_number,
                        meeting_id=meeting.id, winner_lanes=(race_number % 6 + 1,),
                    )
            session.commit()

            first = self._build(session, dt.date(2026, 1, 5), dt.date(2026, 1, 7))
            second = self._build(session, dt.date(2026, 1, 5), dt.date(2026, 1, 7))

        self.assertEqual(first.race_ids, second.race_ids)
        self.assertEqual(first.X, second.X)

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


class RacerStatsBlockTest(DatasetTestBase):
    """Per-course racer ability (`include_racer_stats`).

    Two things here are silent if wrong: the point-in-time join can pick
    a period that was not published yet, and the course join can attach a
    racer's course-1 record to a boat that actually started from course 4.
    Neither raises; both just produce a plausible wrong number.
    """

    DATE = dt.date(2026, 1, 5)

    def _lane_slice(self, row: list[float], lane: int) -> list[float]:
        width = len(dataset.FEATURE_NAMES) + len(dataset.RACER_STATS_FEATURE_NAMES)
        start = (lane - 1) * width + len(dataset.FEATURE_NAMES)
        return row[start : start + len(dataset.RACER_STATS_FEATURE_NAMES)]

    def _add_period(
        self,
        session: Session,
        racer,
        *,
        available_at: dt.datetime,
        courses: dict[int, tuple[int, int]],
        period=(2025, 2),
    ) -> None:
        """`courses` maps course number -> (entry_count, finish_1_count)."""
        stats = RacerPeriodStats(
            racer_id=racer.id,
            period_year=period[0],
            period_number=period[1],
            available_at=available_at,
        )
        session.add(stats)
        session.flush()
        for course, (entries, wins) in courses.items():
            session.add(
                RacerPeriodCourseStats(
                    racer_period_stats_id=stats.id,
                    course_number=course,
                    entry_count=entries,
                    finish_1_count=wins,
                )
            )
        session.flush()

    def _build(self, session):
        return dataset.build_dataset(
            session,
            start_date=self.DATE,
            end_date=self.DATE,
            include_racer_stats=True,
        )

    def test_appends_two_columns_per_lane_and_two_per_race(self) -> None:
        with Session(self.engine) as session:
            self._add_race(session, self.DATE, 1)
            session.commit()

            data = self._build(session)

            self.assertEqual(len(data), 1)
            # 6 lanes x (11 card + 2 racer) + phase + 2 race-level.
            self.assertEqual(len(data.X[0]), 6 * 13 + 1 + 2)
            self.assertEqual(len(data.feature_names), len(data.X[0]))
            self.assertIn("lane1_course_win_shrunk", data.feature_names)
            self.assertIn("field_a1_count", data.feature_names)
            self.assertIn("field_win_rate_sd", data.feature_names)

    def test_no_period_row_resolves_to_the_course_base_rate(self) -> None:
        """A racer with no fan-file history is "no evidence", not a gap:
        shrinkage with zero starts is exactly the prior."""
        with Session(self.engine) as session:
            self._add_race(session, self.DATE, 1)
            session.commit()

            data = self._build(session)

            for lane in LANES_FOR_TEST:
                shrunk, starts = self._lane_slice(data.X[0], lane)
                self.assertEqual(starts, 0.0)
                self.assertAlmostEqual(
                    shrunk, dataset.COURSE_BASE_WIN_RATE[lane], places=9
                )

    def test_shrinks_a_small_sample_toward_the_prior(self) -> None:
        with Session(self.engine) as session:
            race = self._add_race(session, self.DATE, 1)
            racer = session.query(Racer).order_by(Racer.registration_number).first()
            # 4 wins from 4 starts at course 1 -- a raw 100% that must not
            # reach the model as 1.0.
            self._add_period(
                session,
                racer,
                available_at=race.scheduled_deadline_at - dt.timedelta(days=30),
                courses={1: (4, 4)},
            )
            session.commit()

            data = self._build(session)

            shrunk, starts = self._lane_slice(data.X[0], 1)
            self.assertEqual(starts, 4.0)
            k = dataset.COURSE_SHRINKAGE_STARTS[1]
            base = dataset.COURSE_BASE_WIN_RATE[1]
            self.assertAlmostEqual(shrunk, (4 + k * base) / (4 + k), places=9)
            self.assertLess(shrunk, 1.0)
            self.assertGreater(shrunk, base)

    def test_a_period_published_after_the_deadline_is_not_used(self) -> None:
        """The leakage check. A fan file that lands after the race cannot
        describe the racer at the time of it."""
        with Session(self.engine) as session:
            race = self._add_race(session, self.DATE, 1)
            racer = session.query(Racer).order_by(Racer.registration_number).first()
            self._add_period(
                session,
                racer,
                available_at=race.scheduled_deadline_at + dt.timedelta(days=1),
                courses={1: (20, 20)},
            )
            session.commit()

            data = self._build(session)

            shrunk, starts = self._lane_slice(data.X[0], 1)
            self.assertEqual(starts, 0.0)
            self.assertAlmostEqual(shrunk, dataset.COURSE_BASE_WIN_RATE[1], places=9)

    def test_the_latest_available_period_wins(self) -> None:
        with Session(self.engine) as session:
            race = self._add_race(session, self.DATE, 1)
            racer = session.query(Racer).order_by(Racer.registration_number).first()
            self._add_period(
                session,
                racer,
                available_at=race.scheduled_deadline_at - dt.timedelta(days=400),
                courses={1: (30, 0)},
                period=(2024, 2),
            )
            self._add_period(
                session,
                racer,
                available_at=race.scheduled_deadline_at - dt.timedelta(days=30),
                courses={1: (30, 30)},
                period=(2025, 2),
            )
            session.commit()

            data = self._build(session)

            shrunk, starts = self._lane_slice(data.X[0], 1)
            self.assertEqual(starts, 30.0)
            self.assertGreater(shrunk, dataset.COURSE_BASE_WIN_RATE[1])

    def test_joins_on_the_course_actually_taken_not_the_lane(self) -> None:
        """The reason this waited for 直前情報. Lane 1 starting from course
        4 must be scored on their course-4 record."""
        with Session(self.engine) as session:
            race = self._add_race(session, self.DATE, 1)
            racer = session.query(Racer).order_by(Racer.registration_number).first()
            self._add_period(
                session,
                racer,
                available_at=race.scheduled_deadline_at - dt.timedelta(days=30),
                courses={1: (20, 20), 4: (20, 0)},
            )
            self._add_before_info(session, race, courses={1: 4, 2: 1, 3: 3, 4: 2})
            session.commit()

            data = self._build(session)

            shrunk, starts = self._lane_slice(data.X[0], 1)
            self.assertEqual(starts, 20.0)
            # Course 4's record (0 wins), not course 1's (20 wins).
            k = dataset.COURSE_SHRINKAGE_STARTS[4]
            base = dataset.COURSE_BASE_WIN_RATE[4]
            self.assertAlmostEqual(shrunk, (0 + k * base) / (20 + k), places=9)

    def test_falls_back_to_the_lane_when_there_is_no_beforeinfo(self) -> None:
        with Session(self.engine) as session:
            race = self._add_race(session, self.DATE, 1)
            racer = session.query(Racer).order_by(Racer.registration_number).first()
            self._add_period(
                session,
                racer,
                available_at=race.scheduled_deadline_at - dt.timedelta(days=30),
                courses={1: (20, 20)},
            )
            session.commit()

            data = self._build(session)

            _shrunk, starts = self._lane_slice(data.X[0], 1)
            self.assertEqual(starts, 20.0)

    def test_field_globals_count_a1_and_measure_the_spread(self) -> None:
        with Session(self.engine) as session:
            self._add_race(session, self.DATE, 1)
            session.commit()

            data = self._build(session)

            a1, sd = data.X[0][-2:]
            # The fixture gives every lane A1 and an identical win rate.
            self.assertEqual(a1, 6.0)
            self.assertAlmostEqual(sd, 0.0, places=9)

    def test_the_base_dataset_is_unchanged_when_the_block_is_off(self) -> None:
        with Session(self.engine) as session:
            self._add_race(session, self.DATE, 1)
            session.commit()

            data = dataset.build_dataset(
                session, start_date=self.DATE, end_date=self.DATE
            )

            self.assertEqual(len(data.X[0]), 6 * 11 + 1)

    def test_training_and_prediction_produce_the_same_block(self) -> None:
        with Session(self.engine) as session:
            race = self._add_race(session, self.DATE, 1, status="scheduled")
            racer = session.query(Racer).order_by(Racer.registration_number).first()
            self._add_period(
                session,
                racer,
                available_at=race.scheduled_deadline_at - dt.timedelta(days=30),
                courses={1: (12, 5)},
            )
            session.commit()

            rows = dataset.build_prediction_rows(
                session, race_date=self.DATE, include_racer_stats=True
            )
            session.query(Race).filter(Race.id == race.id).update({"status": "finished"})
            session.commit()
            data = self._build(session)

            self.assertEqual(len(rows), 1)
            self.assertEqual(len(data), 1)
            self.assertEqual(rows.X[0], data.X[0])
            self.assertEqual(rows.feature_names, data.feature_names)


class BeforeInfoBlockTest(DatasetTestBase):
    """The 直前情報 block (`include_before_info`).

    The failure this class is really guarding against is a silent one: a
    block that lands on the wrong lane, or that is computed one way for
    training and another for prediction, produces no error at all -- only
    a model scoring numbers that do not mean what it was fit on. So the
    lane-by-lane values are asserted, and the train and predict paths are
    asserted to agree on the same race.
    """

    DATE = dt.date(2026, 1, 5)

    def _lane_slice(self, row: list[float], lane: int) -> list[float]:
        width = len(dataset.FEATURE_NAMES) + len(dataset.BEFORE_INFO_FEATURE_NAMES)
        start = (lane - 1) * width + len(dataset.FEATURE_NAMES)
        return row[start : start + len(dataset.BEFORE_INFO_FEATURE_NAMES)]

    def _build(self, session, **kwargs):
        return dataset.build_dataset(
            session,
            start_date=self.DATE,
            end_date=self.DATE,
            include_before_info=True,
            **kwargs,
        )

    def test_appends_four_columns_per_lane_and_names_them(self) -> None:
        with Session(self.engine) as session:
            race = self._add_race(session, self.DATE, 1)
            self._add_before_info(session, race)
            session.commit()

            data = self._build(session)

            self.assertEqual(len(data), 1)
            # 6 lanes x (11 card + 4 直前情報) + 1 shared phase column.
            self.assertEqual(len(data.X[0]), 6 * 15 + 1)
            self.assertEqual(len(data.feature_names), len(data.X[0]))
            self.assertIn("lane1_exhibition_time_z", data.feature_names)
            self.assertIn("lane6_course_changed", data.feature_names)

    def test_the_base_dataset_is_unchanged_when_the_block_is_off(self) -> None:
        """The block is opt-in; the card model's feature row must not move
        under it, or the frozen card model would be reading shifted
        columns."""
        with Session(self.engine) as session:
            race = self._add_race(session, self.DATE, 1)
            self._add_before_info(session, race)
            session.commit()

            data = dataset.build_dataset(
                session, start_date=self.DATE, end_date=self.DATE
            )

            self.assertEqual(len(data.X[0]), 6 * 11 + 1)
            self.assertEqual(data.stats.dropped_missing_before_info, 0)

    def test_drops_a_race_with_no_beforeinfo_rather_than_predicting_without_it(self) -> None:
        with Session(self.engine) as session:
            self._add_race(session, self.DATE, 1)
            session.commit()

            data = self._build(session)

            self.assertEqual(len(data), 0)
            self.assertEqual(data.stats.dropped_missing_before_info, 1)

    def test_drops_a_race_whose_beforeinfo_covers_only_some_lanes(self) -> None:
        with Session(self.engine) as session:
            race = self._add_race(session, self.DATE, 1)
            self._add_before_info(session, race, lanes=(1, 2, 3))
            session.commit()

            data = self._build(session)

            self.assertEqual(data.stats.dropped_missing_before_info, 1)

    def test_drops_a_race_missing_one_exhibition_time(self) -> None:
        """A partial block is not filled in: an imputed exhibition time is
        indistinguishable from a measured one downstream."""
        with Session(self.engine) as session:
            race = self._add_race(session, self.DATE, 1)
            self._add_before_info(session, race, exhibition_times={4: None})
            session.commit()

            data = self._build(session)

            self.assertEqual(data.stats.dropped_missing_before_info, 1)

    def test_exhibition_time_is_z_scored_within_the_race(self) -> None:
        """The transform that makes the feature discriminating: the level
        is shared by all six boats and cannot change who wins."""
        with Session(self.engine) as session:
            race = self._add_race(session, self.DATE, 1)
            times = {1: 6.70, 2: 6.70, 3: 6.70, 4: 6.80, 5: 6.80, 6: 6.80}
            self._add_before_info(session, race, exhibition_times=times)
            session.commit()

            data = self._build(session)

            zs = [self._lane_slice(data.X[0], lane)[0] for lane in range(1, 7)]
            self.assertAlmostEqual(sum(zs), 0.0, places=9)
            # Three fast, three slow, one sd apart either side of the mean.
            for lane in (1, 2, 3):
                self.assertAlmostEqual(zs[lane - 1], -1.0, places=9)
            for lane in (4, 5, 6):
                self.assertAlmostEqual(zs[lane - 1], 1.0, places=9)

    def test_an_identical_field_gives_zero_rather_than_dividing_by_zero(self) -> None:
        with Session(self.engine) as session:
            race = self._add_race(session, self.DATE, 1)
            self._add_before_info(
                session, race, exhibition_times=dict.fromkeys(range(1, 7), 6.75)
            )
            session.commit()

            data = self._build(session)

            self.assertEqual(len(data), 1)
            for lane in range(1, 7):
                self.assertEqual(self._lane_slice(data.X[0], lane)[0], 0.0)

    def test_a_lane_without_a_start_st_takes_the_field_mean_and_keeps_the_race(self) -> None:
        """展示ST is missing on 2.6% of races; the documented exception to
        "drop rather than impute", because the block's value is carried by
        展示タイム and losing the race costs more than a zero does."""
        with Session(self.engine) as session:
            race = self._add_race(session, self.DATE, 1)
            self._add_before_info(
                session,
                race,
                start_sts={1: 0.10, 2: 0.20, 3: 0.15, 4: 0.15, 5: 0.15, 6: None},
            )
            session.commit()

            data = self._build(session)

            self.assertEqual(len(data), 1)
            self.assertEqual(data.stats.dropped_missing_before_info, 0)
            self.assertEqual(self._lane_slice(data.X[0], 6)[1], 0.0)
            self.assertLess(self._lane_slice(data.X[0], 1)[1], 0.0)

    def test_course_changed_fires_only_where_the_course_differs_from_the_lane(self) -> None:
        with Session(self.engine) as session:
            race = self._add_race(session, self.DATE, 1)
            # A classic 進入変更: lane 4 takes the inside, lanes 1-3 shift out.
            self._add_before_info(
                session, race, courses={1: 2, 2: 3, 3: 4, 4: 1, 5: 5, 6: 6}
            )
            session.commit()

            data = self._build(session)

            flags = [self._lane_slice(data.X[0], lane)[3] for lane in range(1, 7)]
            self.assertEqual(flags, [1.0, 1.0, 1.0, 1.0, 0.0, 0.0])

    def test_tilt_is_carried_through_per_lane(self) -> None:
        with Session(self.engine) as session:
            race = self._add_race(session, self.DATE, 1)
            self._add_before_info(session, race, tilts={3: 1.5})
            session.commit()

            data = self._build(session)

            self.assertEqual(self._lane_slice(data.X[0], 3)[2], 1.5)
            self.assertEqual(self._lane_slice(data.X[0], 1)[2], -0.5)

    def test_excludes_beforeinfo_available_after_the_deadline(self) -> None:
        """The same leakage rule the card features get. A 直前情報 row
        stamped after the deadline did not exist when the bet closed."""
        with Session(self.engine) as session:
            race = self._add_race(session, self.DATE, 1)
            self._add_before_info(
                session,
                race,
                available_at=race.scheduled_deadline_at + dt.timedelta(minutes=1),
            )
            session.commit()

            data = self._build(session)

            self.assertEqual(len(data), 0)
            self.assertEqual(data.stats.dropped_late_feature, 1)

    def test_a_second_observation_does_not_multiply_the_row(self) -> None:
        """The table's key admits a re-capture even though the loader
        refuses to write one. The earliest observation wins, and the race
        still yields exactly one row."""
        with Session(self.engine) as session:
            race = self._add_race(session, self.DATE, 1)
            deadline = race.scheduled_deadline_at
            self._add_before_info(
                session,
                race,
                observed_at=deadline - dt.timedelta(minutes=25),
                tilts=dict.fromkeys(range(1, 7), -0.5),
            )
            self._add_before_info(
                session,
                race,
                observed_at=deadline - dt.timedelta(minutes=5),
                tilts=dict.fromkeys(range(1, 7), 3.0),
            )
            session.commit()

            data = self._build(session)

            self.assertEqual(len(data), 1)
            self.assertEqual(self._lane_slice(data.X[0], 1)[2], -0.5)

    def test_training_and_prediction_produce_the_same_block(self) -> None:
        """The invariant the shared SQL fragments exist to protect."""
        with Session(self.engine) as session:
            race = self._add_race(session, self.DATE, 1, status="scheduled")
            self._add_before_info(
                session, race, courses={1: 2, 2: 1, 3: 3, 4: 4, 5: 5, 6: 6}
            )
            session.commit()

            rows = dataset.build_prediction_rows(
                session, race_date=self.DATE, include_before_info=True
            )
            # The same race, now with the target, through the training path.
            session.query(Race).filter(Race.id == race.id).update({"status": "finished"})
            session.commit()
            data = self._build(session)

            self.assertEqual(len(rows), 1)
            self.assertEqual(len(data), 1)
            self.assertEqual(rows.X[0], data.X[0])
            self.assertEqual(rows.feature_names, data.feature_names)

    def test_prediction_rows_drop_a_race_whose_beforeinfo_has_not_arrived(self) -> None:
        """What makes "only the races whose 直前情報 is in" a property of
        the data rather than a filter the caller must remember."""
        with Session(self.engine) as session:
            ready = self._add_race(session, self.DATE, 1, status="scheduled")
            self._add_before_info(session, ready)
            self._add_race(session, self.DATE, 2, status="scheduled")
            session.commit()

            rows = dataset.build_prediction_rows(
                session, race_date=self.DATE, include_before_info=True
            )

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows.race_ids[0], ready.id)
            self.assertEqual(rows.stats.dropped_missing_before_info, 1)


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
