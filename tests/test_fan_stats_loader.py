"""Fan-file 期別 statistics loader (`loader.load_fan_records`).

Fixtures are built from `fan_stats_parser`'s own dataclasses rather than
from real downloaded records, matching every other loader test here --
the official body's data is not redistributed in this repository.

The temporal assertions carry the weight. `available_at` for these rows
is derived from the *application* period, which is not the window the
statistics were computed over, and getting that wrong is a leak rather
than a visible failure.
"""

from __future__ import annotations

import datetime as dt
import unittest
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from boat_prediction.db.loader import (
    LoaderError,
    ensure_reference_data,
    fan_stats_available_at,
    load_fan_records,
)
from boat_prediction.db.models import (
    Base,
    Racer,
    RacerPeriodCourseStats,
    RacerPeriodStats,
)
from boat_prediction.fan_stats_parser import (
    CoursePositionCounts,
    CourseSummary,
    ParsedFanRecord,
)

JST = ZoneInfo("Asia/Tokyo")


def _engine():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection, _record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine


def _course_summary(course: int) -> CourseSummary:
    return CourseSummary(
        entry_count=100 - course,
        place_rate=50.0 - course,
        avg_start_timing=0.15 + course / 100,
        avg_start_rank=float(course),
    )


def _course_counts(course: int) -> CoursePositionCounts:
    return CoursePositionCounts(
        finish_counts=(30 - course, 20, 15, 10, 8, 5),
        f_count=course,
        l0_count=0,
        l1_count=1,
        k0_count=0,
        k1_count=2,
        s0_count=0,
        s1_count=0,
        s2_count=1,
    )


def _record(reg: int = 4444, year: int = 2026, number: int = 2) -> ParsedFanRecord:
    return ParsedFanRecord(
        registration_number=reg,
        name_kanji="試験太郎",
        name_kana="シケンタロウ",
        branch="東京",
        racer_class="A1",
        birth_date=dt.date(1990, 5, 5),
        sex="1",
        age=36,
        height_cm=170,
        weight_kg=52,
        blood_type="A",
        win_rate=6.85,
        place_rate=45.5,
        first_place_count=30,
        second_place_count=20,
        start_count=120,
        championship_appearance_count=3,
        championship_win_count=1,
        avg_start_timing=0.16,
        course_summaries=tuple(_course_summary(c) for c in range(1, 7)),
        prev_class="A2",
        prev2_class="B1",
        prev3_class="B1",
        prev_ability_index=48.5,
        current_ability_index=52.25,
        period_year=year,
        period_number=number,
        period_from=dt.date(2025, 11, 1),
        period_to=dt.date(2026, 4, 30),
        training_period=95,
        course_position_counts=tuple(_course_counts(c) for c in range(1, 7)),
        no_course_l0_count=0,
        no_course_l1_count=1,
        no_course_k0_count=0,
        no_course_k1_count=0,
        hometown="東京都",
    )


class FanStatsAvailableAtTest(unittest.TestCase):
    def test_period_1_applies_from_january_of_its_stated_year(self) -> None:
        self.assertEqual(
            fan_stats_available_at(2026, 1),
            dt.datetime(2026, 1, 1, tzinfo=JST).astimezone(dt.UTC),
        )

    def test_period_2_applies_from_july_of_its_stated_year(self) -> None:
        self.assertEqual(
            fan_stats_available_at(2026, 2),
            dt.datetime(2026, 7, 1, tzinfo=JST).astimezone(dt.UTC),
        )

    def test_available_at_is_after_the_rating_window_it_summarizes(self) -> None:
        """The whole point of the mapping: fan2604 states period 2026-2 but
        was rated over 2025-11-01..2026-04-30. Treating the stated period as
        the rating window would publish these stats eight months early."""
        record = _record(year=2026, number=2)
        available = fan_stats_available_at(record.period_year, record.period_number)
        window_end = dt.datetime.combine(record.period_to, dt.time(0, 0), tzinfo=JST)
        self.assertGreater(available, window_end.astimezone(dt.UTC))

    def test_unknown_period_number_raises_rather_than_defaulting(self) -> None:
        with self.assertRaises(LoaderError):
            fan_stats_available_at(2026, 3)


class LoadFanRecordsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = _engine()
        self.session = Session(self.engine)
        ensure_reference_data(self.session)

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_loads_one_period_row_and_six_course_rows(self) -> None:
        stats = load_fan_records(self.session, [_record()])

        self.assertEqual(stats.period_rows, 1)
        self.assertEqual(stats.course_rows, 6)
        self.assertEqual(stats.replaced, 0)
        self.assertEqual(self.session.scalar(select(RacerPeriodStats.period_year)), 2026)
        self.assertEqual(
            sorted(self.session.scalars(select(RacerPeriodCourseStats.course_number))),
            [1, 2, 3, 4, 5, 6],
        )

    def test_course_rows_keep_their_course_number_not_a_lane(self) -> None:
        load_fan_records(self.session, [_record()])

        row = self.session.scalar(
            select(RacerPeriodCourseStats).where(RacerPeriodCourseStats.course_number == 3)
        )
        self.assertEqual(row.entry_count, 97)
        self.assertEqual(row.finish_1_count, 27)
        self.assertEqual(row.f_count, 3)

    def test_available_at_comes_from_the_application_period(self) -> None:
        load_fan_records(self.session, [_record(year=2026, number=2)])

        available = self.session.scalar(select(RacerPeriodStats.available_at))
        # SQLite hands the timestamp back naive; the stored instant is UTC.
        self.assertEqual(available, fan_stats_available_at(2026, 2).replace(tzinfo=None))

    def test_creates_the_racer_identity_row_once(self) -> None:
        load_fan_records(self.session, [_record(reg=4444)])
        load_fan_records(self.session, [_record(reg=4444, number=1)])

        self.assertEqual(
            len(list(self.session.scalars(select(Racer).where(Racer.registration_number == 4444)))),
            1,
        )

    def test_reload_replaces_rather_than_duplicating(self) -> None:
        load_fan_records(self.session, [_record()])
        stats = load_fan_records(self.session, [_record()])

        self.assertEqual(stats.replaced, 1)
        self.assertEqual(len(list(self.session.scalars(select(RacerPeriodStats)))), 1)
        self.assertEqual(len(list(self.session.scalars(select(RacerPeriodCourseStats)))), 6)

    def test_two_periods_for_one_racer_coexist(self) -> None:
        load_fan_records(self.session, [_record(number=1), _record(number=2)])

        self.assertEqual(len(list(self.session.scalars(select(RacerPeriodStats)))), 2)
        self.assertEqual(len(list(self.session.scalars(select(RacerPeriodCourseStats)))), 12)

    def test_deleting_a_period_cascades_to_its_course_rows(self) -> None:
        load_fan_records(self.session, [_record()])
        period = self.session.scalar(select(RacerPeriodStats))

        self.session.delete(period)
        self.session.flush()

        self.assertEqual(len(list(self.session.scalars(select(RacerPeriodCourseStats)))), 0)

    def test_point_in_time_values_are_not_written_onto_the_racer_row(self) -> None:
        """Deviation 7: these belong to a period, never to the identity row."""
        load_fan_records(self.session, [_record()])

        racer = self.session.scalar(select(Racer))
        self.assertIsNone(racer.branch)
        self.assertIsNone(racer.birth_date)

    def test_empty_input_is_a_no_op(self) -> None:
        stats = load_fan_records(self.session, [])

        self.assertEqual(stats.period_rows, 0)
        self.assertEqual(len(list(self.session.scalars(select(RacerPeriodStats)))), 0)

    def test_unknown_period_number_raises_before_writing_anything(self) -> None:
        with self.assertRaises(LoaderError):
            load_fan_records(self.session, [_record(number=9)])


if __name__ == "__main__":
    unittest.main()
