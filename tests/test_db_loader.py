"""Tests for boat_prediction.db.loader.

Runs against in-memory SQLite with `PRAGMA foreign_keys=ON` (see
test_db_models.py's `_engine()` for why the pragma matters) so the
loader's ORM-level cascades are exercised the same way they will be on
PostgreSQL. Parsed-record fixtures are built directly as
`bfile_parser`/`kfile_parser` dataclasses rather than through real
downloaded text, matching the existing parser tests' reasoning
(tests/test_bfile_parser.py, tests/test_kfile_parser.py): no official
body's copyrighted data is committed to this repository.
"""

from __future__ import annotations

import datetime as dt
import unittest
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from boat_prediction.bfile_parser import ParsedRaceCard, ParsedVenueDayCard, RaceEntryCard
from boat_prediction.db import loader
from boat_prediction.db.models import (
    Base,
    ExhibitionEntry,
    Race,
    RaceEntry,
    RaceMeeting,
    RacePayout,
    Racer,
    RaceResult,
    RaceResultEntry,
    Venue,
    WeatherObservation,
)
from boat_prediction.jma_weather_source import DailyWeather
from boat_prediction.kfile_parser import ParsedRace, ParsedVenueDay, RaceEntryResult
from boat_prediction.kfile_parser import RacePayout as KRacePayout

JST = ZoneInfo("Asia/Tokyo")
RACE_DATE = dt.date(2026, 6, 1)


def _engine():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection, _record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine


def _entry_card(lane: int = 1, reg: int = 3637, name: str = "齋藤和政") -> RaceEntryCard:
    return RaceEntryCard(
        lane_number=lane,
        racer_registration_number=reg,
        racer_name=name,
        age=55,
        branch="愛知",
        weight_kg=54,
        racer_class="B1",
        national_win_rate=4.15,
        national_second_rate=21.18,
        local_win_rate=4.05,
        local_second_rate=13.64,
        motor_number=72,
        motor_second_rate=0.0,
        boat_number=67,
        boat_second_rate=0.0,
        trailing_info_raw="        9",
    )


def _race_card(
    race_number: int = 1,
    entries: list[RaceEntryCard] | None = None,
    race_class_label: str = "予選",
    deadline: str = "17:41",
) -> ParsedRaceCard:
    return ParsedRaceCard(
        race_number=race_number,
        race_class_label=race_class_label,
        distance_meters=1800,
        scheduled_deadline_time=deadline,
        entries=entries if entries is not None else [_entry_card()],
    )


def _venue_card(
    venue_code: str = "24",
    races: list[ParsedRaceCard] | None = None,
    **kwargs,
) -> ParsedVenueDayCard:
    return ParsedVenueDayCard(
        venue_code=venue_code, races=races if races is not None else [_race_card()], **kwargs
    )


def _result_entry(
    lane: int = 1, reg: int = 3637, status: str = "01", position: int | None = 1
) -> RaceEntryResult:
    return RaceEntryResult(
        finish_status_raw=status,
        finish_position=position,
        lane_number=lane,
        racer_registration_number=reg,
        racer_name="齋藤和政",
        motor_number=72,
        boat_number=67,
        exhibition_time=6.70,
        entry_course=lane,
        start_timing=0.10,
        race_time="1.48.0",
    )


def _k_race(
    race_number: int = 1,
    entries: list[RaceEntryResult] | None = None,
    payouts: list[KRacePayout] | None = None,
    is_cancelled: bool = False,
) -> ParsedRace:
    return ParsedRace(
        race_number=race_number,
        entries=entries if entries is not None else [_result_entry()],
        payouts=payouts if payouts is not None else [KRacePayout("単勝", "1", 150, None)],
        is_cancelled=is_cancelled,
    )


def _k_venue(venue_code: str = "24", races: list[ParsedRace] | None = None) -> ParsedVenueDay:
    return ParsedVenueDay(venue_code=venue_code, races=races if races is not None else [_k_race()])


class TemporalHelpersTest(unittest.TestCase):
    def test_card_available_at_is_midnight_jst_of_the_race_day_in_utc(self) -> None:
        result = loader.card_available_at(RACE_DATE)

        self.assertEqual(result, dt.datetime(2026, 5, 31, 15, 0, tzinfo=dt.UTC))

    def test_results_available_at_is_midnight_jst_of_the_next_day_in_utc(self) -> None:
        result = loader.results_available_at(RACE_DATE)

        self.assertEqual(result, dt.datetime(2026, 6, 1, 15, 0, tzinfo=dt.UTC))

    def test_scheduled_deadline_at_combines_date_and_header_time_in_jst(self) -> None:
        result = loader.scheduled_deadline_at(RACE_DATE, "17:41")

        self.assertEqual(result, dt.datetime(2026, 6, 1, 8, 41, tzinfo=dt.UTC))

    def test_scheduled_deadline_at_rejects_unparsable_time(self) -> None:
        with self.assertRaises(loader.LoaderError):
            loader.scheduled_deadline_at(RACE_DATE, "not-a-time")

    def test_weather_available_at_is_midnight_jst_of_the_next_day_in_utc(self) -> None:
        result = loader.weather_available_at(RACE_DATE)

        self.assertEqual(result, dt.datetime(2026, 6, 1, 15, 0, tzinfo=dt.UTC))


class EnsureReferenceDataTest(unittest.TestCase):
    def test_seeds_all_24_venues_and_two_data_sources(self) -> None:
        engine = _engine()
        self.addCleanup(engine.dispose)
        with Session(engine) as session:
            loader.ensure_reference_data(session)
            session.commit()

            self.assertEqual(session.query(Venue).count(), 24)

    def test_is_idempotent(self) -> None:
        engine = _engine()
        self.addCleanup(engine.dispose)
        with Session(engine) as session:
            loader.ensure_reference_data(session)
            loader.ensure_reference_data(session)
            session.commit()

            self.assertEqual(session.query(Venue).count(), 24)


class LoadBFileDayTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = _engine()
        self.addCleanup(self.engine.dispose)
        with Session(self.engine) as session:
            loader.ensure_reference_data(session)
            session.commit()

    def test_loads_race_and_entry_with_pre_race_available_at(self) -> None:
        with Session(self.engine) as session:
            stats = loader.load_b_file_day(session, RACE_DATE, [_venue_card()])
            session.commit()

            self.assertEqual(stats.races, 1)
            self.assertEqual(stats.entries, 1)
            race = session.scalar(select(Race))
            self.assertEqual(race.race_class, "予選")
            # Naive on purpose: SQLite drops tzinfo on read-back (see the
            # comment on entry.available_at just below).
            self.assertEqual(race.scheduled_deadline_at, dt.datetime(2026, 6, 1, 8, 41))  # noqa: DTZ001
            entry = session.scalar(select(RaceEntry))
            # SQLite's generic DateTime type does not round-trip tzinfo
            # (unlike PostgreSQL's real TIMESTAMPTZ), so comparisons
            # against read-back timestamps are done naive-UTC.
            self.assertEqual(
                entry.available_at, loader.card_available_at(RACE_DATE).replace(tzinfo=None)
            )
            self.assertEqual(float(entry.listed_national_win_rate), 4.15)

    def test_creates_racer_once_and_reuses_it_across_races(self) -> None:
        cards = [
            _venue_card(
                races=[
                    _race_card(race_number=1, entries=[_entry_card(lane=1)]),
                    _race_card(race_number=2, entries=[_entry_card(lane=1)]),
                ]
            )
        ]
        with Session(self.engine) as session:
            loader.load_b_file_day(session, RACE_DATE, cards)
            session.commit()

            self.assertEqual(session.query(Racer).count(), 1)
            self.assertEqual(session.query(RaceEntry).count(), 2)

    def test_reloading_the_same_day_does_not_duplicate_rows(self) -> None:
        cards = [_venue_card()]
        with Session(self.engine) as session:
            loader.load_b_file_day(session, RACE_DATE, cards)
            session.commit()
            loader.load_b_file_day(session, RACE_DATE, cards)
            session.commit()

            self.assertEqual(session.query(Race).count(), 1)
            self.assertEqual(session.query(RaceEntry).count(), 1)

    def test_data_pending_venue_is_skipped_and_counted_not_raised(self) -> None:
        with Session(self.engine) as session:
            stats = loader.load_b_file_day(
                session, RACE_DATE, [_venue_card(races=[], data_pending=True)]
            )
            session.commit()

            self.assertEqual(stats.venues_data_pending, 1)
            self.assertEqual(session.query(Race).count(), 0)

    def test_cancelled_venue_is_skipped_and_counted_not_raised(self) -> None:
        with Session(self.engine) as session:
            stats = loader.load_b_file_day(
                session, RACE_DATE, [_venue_card(races=[], is_cancelled=True)]
            )
            session.commit()

            self.assertEqual(stats.venues_cancelled, 1)
            self.assertEqual(session.query(Race).count(), 0)

    def test_card_less_venue_with_no_flag_raises_loader_error(self) -> None:
        # The signature of a parse defect (tasks/HANDOFF.md): a venue
        # section with no races and neither data_pending nor
        # is_cancelled set should never reach the loader silently.
        with Session(self.engine) as session, self.assertRaises(loader.LoaderError):
            loader.load_b_file_day(session, RACE_DATE, [_venue_card(races=[])])

    def test_meeting_date_mismatch_with_filename_date_raises(self) -> None:
        with Session(self.engine) as session, self.assertRaises(loader.LoaderError):
            loader.load_b_file_day(
                session,
                RACE_DATE,
                [_venue_card(meeting_date=dt.date(2026, 6, 2))],
            )

    def test_series_day_and_meeting_start_date_are_derived_correctly(self) -> None:
        with Session(self.engine) as session:
            loader.load_b_file_day(
                session,
                RACE_DATE,
                [_venue_card(series_day=3, meeting_date=RACE_DATE, meeting_title="G1テスト")],
            )
            session.commit()

            meeting = session.scalar(select(RaceMeeting))
            self.assertEqual(meeting.meeting_start_date, dt.date(2026, 5, 30))
            self.assertEqual(meeting.meeting_title, "G1テスト")
            race = session.scalar(select(Race))
            self.assertEqual(race.series_day, 3)

    def _load_days(self, session, days: list[tuple[dt.date, int]]) -> None:
        """Load one venue's consecutive race days as (date, series_day)."""
        for race_date, series_day in days:
            loader.load_b_file_day(
                session,
                race_date,
                [_venue_card(series_day=series_day, meeting_date=race_date)],
            )
            session.flush()

    def test_a_postponed_day_stays_in_the_same_meeting(self) -> None:
        # Venue 24, 2005-09: 第5日 ran on three consecutive dates. Deriving
        # the key as date - (series_day - 1) made three meetings of one 節.
        with Session(self.engine) as session:
            self._load_days(
                session,
                [
                    (dt.date(2005, 9, 3), 3),
                    (dt.date(2005, 9, 4), 4),
                    (dt.date(2005, 9, 5), 5),
                    (dt.date(2005, 9, 6), 5),
                    (dt.date(2005, 9, 7), 5),
                ],
            )
            session.commit()

            self.assertEqual(session.query(RaceMeeting).count(), 1)
            meeting = session.scalar(select(RaceMeeting))
            self.assertEqual(meeting.meeting_start_date, dt.date(2005, 9, 1))
            self.assertTrue(
                all(race.meeting_id == meeting.id for race in session.scalars(select(Race)))
            )

    def test_a_skipped_or_backwards_day_number_stays_in_the_same_meeting(self) -> None:
        # Venue 03, 2007-07: 第1,2,2,3,4,6,5,6日 on consecutive dates.
        with Session(self.engine) as session:
            self._load_days(
                session,
                [
                    (dt.date(2007, 7, 11), 1),
                    (dt.date(2007, 7, 12), 2),
                    (dt.date(2007, 7, 13), 2),
                    (dt.date(2007, 7, 14), 3),
                    (dt.date(2007, 7, 15), 4),
                    (dt.date(2007, 7, 16), 6),
                    (dt.date(2007, 7, 17), 5),
                    (dt.date(2007, 7, 18), 6),
                ],
            )
            session.commit()

            self.assertEqual(session.query(RaceMeeting).count(), 1)
            self.assertEqual(
                session.scalar(select(RaceMeeting)).meeting_start_date, dt.date(2007, 7, 11)
            )

    def test_a_new_series_starting_the_next_day_gets_its_own_meeting(self) -> None:
        # Consecutive 節 one day apart occur 173 times in the archive, so
        # 第1日 must still open a new meeting.
        with Session(self.engine) as session:
            self._load_days(
                session,
                [
                    (dt.date(2016, 3, 15), 4),
                    (dt.date(2016, 3, 16), 5),
                    (dt.date(2016, 3, 17), 1),
                    (dt.date(2016, 3, 18), 2),
                ],
            )
            session.commit()

            starts = sorted(session.scalars(select(RaceMeeting.meeting_start_date)))
            self.assertEqual(starts, [dt.date(2016, 3, 12), dt.date(2016, 3, 17)])

    def test_a_new_meeting_never_reuses_an_existing_start_date(self) -> None:
        # 第4日 on 03-16 estimates a 03-13 start, which the earlier 節
        # already owns; reusing it would merge two different 節.
        with Session(self.engine) as session:
            self._load_days(session, [(dt.date(2016, 3, 13), 1), (dt.date(2016, 3, 14), 2)])
            self._load_days(session, [(dt.date(2016, 3, 20), 1), (dt.date(2016, 3, 21), 2)])
            self._load_days(session, [(dt.date(2016, 3, 25), 4)])
            session.commit()

            starts = sorted(session.scalars(select(RaceMeeting.meeting_start_date)))
            self.assertEqual(len(starts), 3)
            self.assertEqual(len(set(starts)), 3)

    def test_reloading_a_day_keeps_the_meeting_it_already_belongs_to(self) -> None:
        with Session(self.engine) as session:
            self._load_days(
                session, [(dt.date(2026, 6, 1), 1), (dt.date(2026, 6, 2), 2)]
            )
            session.commit()
            before = session.scalar(select(Race.meeting_id).where(Race.race_date == RACE_DATE))

            self._load_days(session, [(dt.date(2026, 6, 1), 1)])
            session.commit()

            self.assertEqual(session.query(RaceMeeting).count(), 1)
            self.assertEqual(
                session.scalar(select(Race.meeting_id).where(Race.race_date == RACE_DATE)),
                before,
            )

    def test_fixed_entry_flag_is_set_from_race_class_marker(self) -> None:
        with Session(self.engine) as session:
            loader.load_b_file_day(
                session,
                RACE_DATE,
                [_venue_card(races=[_race_card(race_class_label="予選　進入固定")])],
            )
            session.commit()

            race = session.scalar(select(Race))
            self.assertTrue(race.is_fixed_entry)


class LoadKFileDayTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = _engine()
        self.addCleanup(self.engine.dispose)
        with Session(self.engine) as session:
            loader.ensure_reference_data(session)
            session.commit()

    def test_creates_the_race_when_no_card_was_loaded_first(self) -> None:
        with Session(self.engine) as session:
            stats = loader.load_k_file_day(session, RACE_DATE, [_k_venue()])
            session.commit()

            self.assertEqual(stats.results, 1)
            race = session.scalar(select(Race))
            self.assertEqual(race.status, "finished")

    def test_result_available_at_is_the_next_day_bound(self) -> None:
        with Session(self.engine) as session:
            loader.load_k_file_day(session, RACE_DATE, [_k_venue()])
            session.commit()

            result = session.scalar(select(RaceResult))
            self.assertEqual(
                result.available_at, loader.results_available_at(RACE_DATE).replace(tzinfo=None)
            )

    def test_non_numeric_finish_status_keeps_raw_code_and_null_position(self) -> None:
        with Session(self.engine) as session:
            loader.load_k_file_day(
                session,
                RACE_DATE,
                [_k_venue(races=[_k_race(entries=[_result_entry(status="S0", position=None)])])],
            )
            session.commit()

            result_entry = session.scalar(select(RaceResultEntry))
            self.assertIsNone(result_entry.finish_position)
            self.assertEqual(result_entry.status, "S0")

    def test_cancelled_race_sets_status_and_writes_no_result(self) -> None:
        with Session(self.engine) as session:
            stats = loader.load_k_file_day(
                session,
                RACE_DATE,
                [_k_venue(races=[_k_race(entries=[], payouts=[], is_cancelled=True)])],
            )
            session.commit()

            self.assertEqual(stats.races_cancelled, 1)
            race = session.scalar(select(Race))
            self.assertEqual(race.status, "cancelled")
            self.assertEqual(session.query(RaceResult).count(), 0)

    def test_one_empty_race_among_populated_ones_raises(self) -> None:
        # A hole inside a day that otherwise produced results is the parse
        # defect the guard exists for.
        with Session(self.engine) as session, self.assertRaises(loader.LoaderError):
            loader.load_k_file_day(
                session,
                RACE_DATE,
                [
                    _k_venue(
                        races=[
                            _k_race(race_number=1),
                            _k_race(race_number=2, entries=[], payouts=[]),
                        ]
                    )
                ],
            )

    def test_venue_day_with_no_results_at_all_is_treated_as_cancelled(self) -> None:
        # Real shape of venue 01 on 2011-04-24: the 1R-12R payout table is
        # present but empty, there is no race detail block, and the file
        # carries no 中止 marker. The only such day in the 2005-2026
        # archive, and the 5th failure of the .21 full load.
        races = [
            _k_race(race_number=number, entries=[], payouts=[]) for number in range(1, 13)
        ]
        with Session(self.engine) as session:
            stats = loader.load_k_file_day(session, RACE_DATE, [_k_venue(races=races)])
            session.commit()

            self.assertEqual(stats.races_cancelled, 12)
            self.assertEqual(session.query(RaceResult).count(), 0)
            self.assertTrue(
                all(race.status == "cancelled" for race in session.scalars(select(Race)))
            )

    def test_reloading_the_same_day_does_not_duplicate_results_or_payouts(self) -> None:
        venues = [_k_venue()]
        with Session(self.engine) as session:
            loader.load_k_file_day(session, RACE_DATE, venues)
            session.commit()
            loader.load_k_file_day(session, RACE_DATE, venues)
            session.commit()

            self.assertEqual(session.query(RaceResult).count(), 1)
            self.assertEqual(session.query(RacePayout).count(), 1)
            self.assertEqual(session.query(RaceResultEntry).count(), 1)

    def test_duplicate_payout_key_within_one_race_keeps_only_the_first(self) -> None:
        payouts = [
            KRacePayout("拡連複", "1-3", 130, 1),
            KRacePayout("拡連複", "1-3", 130, 1),
        ]
        with Session(self.engine) as session:
            stats = loader.load_k_file_day(
                session, RACE_DATE, [_k_venue(races=[_k_race(payouts=payouts)])]
            )
            session.commit()

            self.assertEqual(stats.payouts, 1)
            self.assertEqual(session.query(RacePayout).count(), 1)

    def test_a_race_re_marked_cancelled_after_being_finished_drops_its_result(self) -> None:
        with Session(self.engine) as session:
            loader.load_k_file_day(session, RACE_DATE, [_k_venue()])
            session.commit()
            self.assertEqual(session.query(RaceResult).count(), 1)

            loader.load_k_file_day(
                session,
                RACE_DATE,
                [_k_venue(races=[_k_race(entries=[], payouts=[], is_cancelled=True)])],
            )
            session.commit()

            self.assertEqual(session.query(RaceResult).count(), 0)
            race = session.scalar(select(Race))
            self.assertEqual(race.status, "cancelled")


class CrossFileLinkingTest(unittest.TestCase):
    """race_result_entries.race_entry_id must end up correctly linked
    regardless of which file (B or K) is loaded first, since the two
    archives are downloaded and will be loaded independently."""

    def setUp(self) -> None:
        self.engine = _engine()
        self.addCleanup(self.engine.dispose)
        with Session(self.engine) as session:
            loader.ensure_reference_data(session)
            session.commit()

    def test_b_file_then_k_file_links_the_result_entry_to_the_card_entry(self) -> None:
        with Session(self.engine) as session:
            loader.load_b_file_day(session, RACE_DATE, [_venue_card()])
            loader.load_k_file_day(session, RACE_DATE, [_k_venue()])
            session.commit()

            result_entry = session.scalar(select(RaceResultEntry))
            entry = session.scalar(select(RaceEntry))
            self.assertEqual(result_entry.race_entry_id, entry.id)

    def test_k_file_then_b_file_retroactively_links_the_result_entry(self) -> None:
        with Session(self.engine) as session:
            loader.load_k_file_day(session, RACE_DATE, [_k_venue()])
            session.commit()
            result_entry = session.scalar(select(RaceResultEntry))
            self.assertIsNone(result_entry.race_entry_id)

            loader.load_b_file_day(session, RACE_DATE, [_venue_card()])
            session.commit()

            session.refresh(result_entry)
            entry = session.scalar(select(RaceEntry))
            self.assertEqual(result_entry.race_entry_id, entry.id)

    def test_exhibition_entry_is_linked_to_the_matching_race_entry(self) -> None:
        with Session(self.engine) as session:
            loader.load_b_file_day(session, RACE_DATE, [_venue_card()])
            loader.load_k_file_day(session, RACE_DATE, [_k_venue()])
            session.commit()

            exhibition = session.scalar(select(ExhibitionEntry))
            entry = session.scalar(select(RaceEntry))
            self.assertEqual(exhibition.race_entry_id, entry.id)
            self.assertEqual(float(exhibition.exhibition_time_sec), 6.70)
            # Results-time availability, not pre-race -- see
            # models.ExhibitionEntry's docstring on why this is correct.
            self.assertEqual(
                exhibition.available_at, loader.results_available_at(RACE_DATE).replace(tzinfo=None)
            )


def _daily_weather(day: int, **overrides) -> DailyWeather:
    fields = {
        "date_iso": f"2026-06-{day:02d}",
        "precipitation_total_mm": 0.0,
        "precipitation_max_1h_mm": 0.0,
        "precipitation_max_10min_mm": 0.0,
        "temperature_avg_c": 22.5,
        "temperature_max_c": 27.1,
        "temperature_min_c": 18.3,
        "humidity_avg_pct": 65.0,
        "humidity_min_pct": 40.0,
        "wind_avg_ms": 2.1,
        "wind_max_ms": 5.4,
        "wind_max_direction": "南西",
        "wind_max_instant_ms": 8.9,
        "wind_max_instant_direction": "西",
        "wind_prevailing_direction": "南",
        "sunshine_hours": 6.7,
    }
    fields.update(overrides)
    return DailyWeather(**fields)


class LoadWeatherMonthTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = _engine()
        self.addCleanup(self.engine.dispose)
        with Session(self.engine) as session:
            loader.ensure_reference_data(session)
            session.commit()

    def test_loads_every_day_given_for_the_venue(self) -> None:
        with Session(self.engine) as session:
            stats = loader.load_weather_month(
                session, "24", 2026, 6, (_daily_weather(1), _daily_weather(2))
            )
            session.commit()

            self.assertEqual(stats.observations, 2)
            self.assertEqual(session.query(WeatherObservation).count(), 2)

    def test_available_at_is_midnight_jst_of_the_day_after_the_observation(self) -> None:
        with Session(self.engine) as session:
            loader.load_weather_month(session, "24", 2026, 6, (_daily_weather(1),))
            session.commit()

            row = session.scalar(select(WeatherObservation))
            self.assertEqual(
                row.available_at, loader.weather_available_at(dt.date(2026, 6, 1)).replace(tzinfo=None)
            )

    def test_skips_an_unknown_venue_code_without_raising(self) -> None:
        with Session(self.engine) as session:
            stats = loader.load_weather_month(session, "99", 2026, 6, (_daily_weather(1),))

            self.assertEqual(stats.skipped_unknown_venue, 1)
            self.assertEqual(stats.observations, 0)
            self.assertEqual(session.query(WeatherObservation).count(), 0)

    def test_reloading_the_same_month_replaces_rather_than_duplicates(self) -> None:
        with Session(self.engine) as session:
            loader.load_weather_month(session, "24", 2026, 6, (_daily_weather(1, sunshine_hours=1.0),))
            session.commit()

            loader.load_weather_month(session, "24", 2026, 6, (_daily_weather(1, sunshine_hours=9.0),))
            session.commit()

            self.assertEqual(session.query(WeatherObservation).count(), 1)
            row = session.scalar(select(WeatherObservation))
            self.assertEqual(float(row.sunshine_hours), 9.0)

    def test_loading_a_different_month_does_not_touch_the_first(self) -> None:
        with Session(self.engine) as session:
            loader.load_weather_month(session, "24", 2026, 6, (_daily_weather(30),))
            session.commit()

            loader.load_weather_month(
                session, "24", 2026, 7, (_daily_weather(1, date_iso="2026-07-01"),)
            )
            session.commit()

            self.assertEqual(session.query(WeatherObservation).count(), 2)

    def test_loading_a_different_venue_does_not_touch_the_first(self) -> None:
        with Session(self.engine) as session:
            loader.load_weather_month(session, "24", 2026, 6, (_daily_weather(1),))
            session.commit()

            loader.load_weather_month(session, "01", 2026, 6, (_daily_weather(1),))
            session.commit()

            self.assertEqual(session.query(WeatherObservation).count(), 2)


if __name__ == "__main__":
    unittest.main()
