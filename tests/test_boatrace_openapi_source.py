"""Boatrace Open API adapter and backfill.

The adapter's job is to make a third-party feed indistinguishable from
the official parser's output *where the two agree*, and to make the
places they do not agree impossible to overlook. Both halves are tested:
the sign convention that the cross-validation exposed, and the two fields
the mirror does not carry.
"""

from __future__ import annotations

import datetime as dt
import json
import unittest

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from boat_prediction.boatrace_openapi_source import (
    EARLIEST_DATE,
    OPENAPI_WEATHER_LABEL,
    BoatraceOpenApiError,
    fetch_day,
    parse_previews,
    previews_url,
)
from boat_prediction.db import loader
from boat_prediction.db.load_beforeinfo_archive import backfill_day
from boat_prediction.db.models import Base, BeforeInfoEntry, Race, RaceSurfaceCondition

RACE_DATE = dt.date(2024, 8, 1)

SAMPLE = {
    "previews": [
        {
            "date": "2024-08-01",
            "stadium_number": 3,
            "number": 1,
            "wind_speed": 4,
            "wind_direction_number": 14,
            "wave_height": 5,
            "weather_number": 1,
            "air_temperature": 31,
            "water_temperature": 31,
            "boats": [
                {
                    "racer_boat_number": lane,
                    # lane 6 muscles into course 1 -- a 進入変更
                    "racer_course_number": 1 if lane == 6 else (lane + 1 if lane < 6 else lane),
                    # lane 2 flies: the API writes that as a negative
                    "racer_start_timing": -0.03 if lane == 2 else 0.12,
                    "racer_weight": 52,
                    "racer_weight_adjustment": 0,
                    "racer_exhibition_time": 6.79 + lane / 100,
                    "racer_tilt_adjustment": 0.5,
                }
                for lane in range(1, 7)
            ],
        }
    ]
}


def _engine():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine


class _Opener:
    def __init__(self, payload=None) -> None:
        self.payload = SAMPLE if payload is None else payload
        self.requested: list[str] = []

    def Request(self, url, headers=None):
        self.requested.append(url)
        return url

    def urlopen(self, request, timeout=None):
        body = json.dumps(self.payload).encode("utf-8")

        class _Response:
            def read(self):
                return body

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return _Response()


class UrlTest(unittest.TestCase):
    def test_url_shape(self) -> None:
        self.assertEqual(
            previews_url(dt.date(2024, 8, 1)),
            "https://boatraceopenapi.github.io/previews/v3/2024/20240801.json",
        )

    def test_refuses_a_date_before_the_feed_exists(self) -> None:
        with self.assertRaises(BoatraceOpenApiError):
            previews_url(EARLIEST_DATE - dt.timedelta(days=1))


class ParsePreviewsTest(unittest.TestCase):
    def test_adapts_into_the_official_parsers_types(self) -> None:
        races = parse_previews(SAMPLE)

        self.assertEqual(len(races), 1)
        race = races[0]
        self.assertEqual(race.venue_code, "03")
        self.assertEqual(race.race_number, 1)
        self.assertEqual(len(race.info.boats), 6)
        self.assertTrue(race.info.has_exhibition_data)

    def test_negative_start_timing_becomes_magnitude_plus_flag(self) -> None:
        """The one disagreement the cross-validation found: the API signs a
        flying start, this project flags it."""
        race = parse_previews(SAMPLE)[0]
        by_lane = {s.lane_number: s for s in race.info.start_exhibition}

        self.assertAlmostEqual(by_lane[2].start_timing_sec, 0.03)
        self.assertTrue(by_lane[2].is_flying)
        self.assertAlmostEqual(by_lane[1].start_timing_sec, 0.12)
        self.assertFalse(by_lane[1].is_flying)

    def test_entry_course_is_carried(self) -> None:
        race = parse_previews(SAMPLE)[0]
        by_lane = {s.lane_number: s.course_number for s in race.info.start_exhibition}

        self.assertEqual(by_lane[6], 1)

    def test_weather_is_marked_undatable_so_the_safety_check_refuses_it(self) -> None:
        """The mirror drops the page's observation label, so a reading
        cannot be placed in time and must never pass as a feature."""
        race = parse_previews(SAMPLE)[0]
        weather = race.info.weather

        self.assertEqual(weather.raw_label, OPENAPI_WEATHER_LABEL)
        self.assertIsNone(weather.reference_race_number)
        for race_number in range(1, 13):
            self.assertFalse(weather.is_safe_for_race(race_number))

    def test_boats_as_a_dict_parses_identically(self) -> None:
        """The feed is not shape-stable: some dates key boats by lane
        number as a string. A first backfill lost 112 days to this."""
        as_list = SAMPLE["previews"][0]["boats"]
        as_dict = {str(b["racer_boat_number"]): b for b in as_list}
        payload = {"previews": [dict(SAMPLE["previews"][0], boats=as_dict)]}

        listed = parse_previews(SAMPLE)[0]
        keyed = parse_previews(payload)[0]

        self.assertEqual(len(keyed.info.boats), 6)
        self.assertEqual(
            [b.exhibition_time_sec for b in keyed.info.boats],
            [b.exhibition_time_sec for b in listed.info.boats],
        )
        self.assertEqual(
            [s.course_number for s in keyed.info.start_exhibition],
            [s.course_number for s in listed.info.start_exhibition],
        )

    def test_unknown_venue_is_skipped_not_fatal(self) -> None:
        payload = {"previews": [dict(SAMPLE["previews"][0], stadium_number=99)]}

        self.assertEqual(parse_previews(payload), [])

    def test_missing_previews_key_raises(self) -> None:
        with self.assertRaises(BoatraceOpenApiError):
            parse_previews({"something_else": []})

    def test_fetch_day_uses_the_dated_url(self) -> None:
        opener = _Opener()

        races = fetch_day(RACE_DATE, opener=opener)

        self.assertEqual(len(races), 1)
        self.assertIn("20240801.json", opener.requested[0])


class BackfillDayTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = _engine()
        self.session = Session(self.engine)
        loader.ensure_reference_data(self.session)
        venue = loader._venue(self.session, "03")
        self.deadline = dt.datetime(2024, 8, 1, 11, 30, tzinfo=dt.UTC)
        self.race = Race(
            venue_id=venue.id,
            race_date=RACE_DATE,
            race_number=1,
            status="finished",
            scheduled_deadline_at=self.deadline,
        )
        self.session.add(self.race)
        self.session.flush()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_loads_the_race_and_dates_it_at_the_deadline(self) -> None:
        stats = backfill_day(self.session, RACE_DATE, opener=_Opener())

        self.assertEqual(stats.races_loaded, 1)
        rows = list(self.session.scalars(select(BeforeInfoEntry)))
        self.assertEqual(len(rows), 6)
        for row in rows:
            self.assertEqual(row.available_at, self.deadline.replace(tzinfo=None))

    def test_parts_are_null_not_false(self) -> None:
        """The mirror does not carry them; writing False would assert an
        absence that was never observed."""
        backfill_day(self.session, RACE_DATE, opener=_Opener())

        for row in self.session.scalars(select(BeforeInfoEntry)):
            self.assertIsNone(row.propeller_changed)
            self.assertIsNone(row.parts_replaced)

    def test_backfilled_rows_are_attributable_to_the_mirror(self) -> None:
        backfill_day(self.session, RACE_DATE, opener=_Opener())

        source_id = loader._source_id(self.session, loader.SOURCE_BOATRACE_OPENAPI)
        for row in self.session.scalars(select(BeforeInfoEntry)):
            self.assertEqual(row.source_id, source_id)
        weather = self.session.scalar(select(RaceSurfaceCondition))
        self.assertIsNone(weather.reference_race_number)

    def test_a_race_absent_from_the_database_is_counted_not_invented(self) -> None:
        payload = {"previews": [dict(SAMPLE["previews"][0], number=9)]}

        stats = backfill_day(self.session, RACE_DATE, opener=_Opener(payload))

        self.assertEqual(stats.races_not_in_db, 1)
        self.assertEqual(len(list(self.session.scalars(select(Race)))), 1)

    def test_a_live_capture_is_not_overwritten_by_the_backfill(self) -> None:
        """Official rows must win: they carry a real observation time and
        the parts data the mirror lacks."""
        live_observed = self.deadline - dt.timedelta(minutes=15)
        loader.load_before_info(
            self.session,
            race_id=self.race.id,
            info=parse_previews(SAMPLE)[0].info,
            observed_at=live_observed,
        )

        stats = backfill_day(self.session, RACE_DATE, opener=_Opener())

        self.assertEqual(stats.races_already_loaded, 1)
        self.assertEqual(len(list(self.session.scalars(select(BeforeInfoEntry)))), 6)
        for row in self.session.scalars(select(BeforeInfoEntry)):
            self.assertEqual(row.observed_at, live_observed.replace(tzinfo=None))


if __name__ == "__main__":
    unittest.main()
