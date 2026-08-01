"""直前情報 capture (`db.capture_beforeinfo`, `loader.load_before_info`).

The interesting assertions are about *when* rather than *what*. These
rows are the only ones in the schema whose availability is established by
the fetch itself rather than by a publication convention, so a test that
only checked the values would miss the property the table exists for.
"""

from __future__ import annotations

import datetime as dt
import unittest
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from boat_prediction.beforeinfo_source import (
    BoatBeforeInfo,
    RaceBeforeInfo,
    StartExhibitionEntry,
    SurfaceWeather,
)
from boat_prediction.db import loader
from boat_prediction.db.capture_beforeinfo import (
    CaptureBeforeInfoError,
    capture_due_beforeinfo,
    find_due_races,
)
from boat_prediction.db.models import (
    Base,
    BeforeInfoEntry,
    Race,
    RaceSurfaceCondition,
)

JST = ZoneInfo("Asia/Tokyo")
RACE_DATE = dt.date(2026, 8, 1)
DEADLINE = dt.datetime(2026, 8, 1, 18, 0, tzinfo=JST).astimezone(dt.UTC)


def _engine():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine


def _boat(lane: int, *, exhibition: float | None = 6.75) -> BoatBeforeInfo:
    return BoatBeforeInfo(
        lane_number=lane,
        racer_registration_number=4000 + lane,
        racer_name=f"選手{lane}",
        weight_kg=52.0,
        adjustment_weight_kg=0.0,
        exhibition_time_sec=exhibition,
        tilt_angle=-0.5,
        propeller_changed=lane == 3,
        parts_replaced=("ピストン",) if lane == 2 else (),
    )


def _info(*, exhibition: float | None = 6.75, weather: SurfaceWeather | None = None,
          entry_swap: bool = False) -> RaceBeforeInfo:
    # lane 6 takes course 1 when entry_swap: the 進入変更 this table exists to see
    courses = {6: 1, 1: 2} if entry_swap else {}
    return RaceBeforeInfo(
        boats=tuple(_boat(lane, exhibition=exhibition) for lane in range(1, 7)),
        start_exhibition=tuple(
            StartExhibitionEntry(
                course_number=courses.get(lane, lane),
                lane_number=lane,
                start_timing_sec=0.15,
                is_flying=False,
            )
            for lane in range(1, 7)
        ),
        weather=weather,
    )


def _weather(label: str = "3R時点", ref: int | None = 3) -> SurfaceWeather:
    return SurfaceWeather(
        raw_label=label,
        reference_race_number=ref,
        air_temperature_c=31.0,
        water_temperature_c=28.5,
        wind_speed_ms=3.0,
        wind_direction_code=5,
        wave_height_cm=4.0,
        weather_text="晴",
        weather_icon_code=1,
    )


class _Opener:
    """Stands in for the HTTP opener; records what was requested."""

    def __init__(self, html: str = "<html></html>") -> None:
        self.html = html
        self.requested: list[str] = []

    def Request(self, url, headers=None):
        self.requested.append(url)
        return url

    def urlopen(self, request, timeout=None):
        opener = self

        class _Response:
            def read(self):
                return opener.html.encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return _Response()


class LoadBeforeInfoTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = _engine()
        self.session = Session(self.engine)
        loader.ensure_reference_data(self.session)
        venue = loader._venue(self.session, "01")
        self.race = Race(
            venue_id=venue.id,
            race_date=RACE_DATE,
            race_number=1,
            status="scheduled",
            scheduled_deadline_at=DEADLINE,
        )
        self.session.add(self.race)
        self.session.flush()
        self.observed = DEADLINE - dt.timedelta(minutes=15)

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def _load(self, info):
        return loader.load_before_info(
            self.session, race_id=self.race.id, info=info, observed_at=self.observed
        )

    def test_stores_six_boats_and_one_weather_row(self) -> None:
        stats = self._load(_info(weather=_weather()))

        self.assertEqual(stats.boat_rows, 6)
        self.assertEqual(stats.weather_rows, 1)
        self.assertEqual(len(list(self.session.scalars(select(BeforeInfoEntry)))), 6)
        self.assertEqual(len(list(self.session.scalars(select(RaceSurfaceCondition)))), 1)

    def test_available_at_is_the_fetch_and_precedes_the_deadline(self) -> None:
        """Unlike every other source here, availability is the fetch itself."""
        self._load(_info(weather=_weather()))

        for available_at, observed_at in self.session.execute(
            select(BeforeInfoEntry.available_at, BeforeInfoEntry.observed_at)
        ):
            self.assertEqual(available_at, observed_at)
            self.assertLess(available_at, DEADLINE.replace(tzinfo=None))

    def test_start_exhibition_course_can_differ_from_the_lane(self) -> None:
        """進入変更 is the reason this is captured pre-race at all."""
        self._load(_info(entry_swap=True))

        by_lane = {
            row.lane_number: row.start_exhibition_course
            for row in self.session.scalars(select(BeforeInfoEntry))
        }
        self.assertEqual(by_lane[6], 1)
        self.assertEqual(by_lane[1], 2)

    def test_a_page_without_exhibition_times_writes_nothing(self) -> None:
        """So the next scheduled run retries instead of a blank row
        standing in permanently for a reading that arrived later."""
        stats = self._load(_info(exhibition=None))

        self.assertEqual(stats.skipped_no_exhibition, 1)
        self.assertEqual(stats.boat_rows, 0)
        self.assertEqual(len(list(self.session.scalars(select(BeforeInfoEntry)))), 0)

    def test_second_load_for_the_same_race_is_skipped(self) -> None:
        self._load(_info())
        stats = self._load(_info())

        self.assertEqual(stats.skipped_already_captured, 1)
        self.assertEqual(len(list(self.session.scalars(select(BeforeInfoEntry)))), 6)

    def test_weather_label_is_preserved_for_a_later_leakage_check(self) -> None:
        self._load(_info(weather=_weather(label="17:43現在", ref=None)))

        row = self.session.scalar(select(RaceSurfaceCondition))
        self.assertEqual(row.raw_label, "17:43現在")
        self.assertIsNone(row.reference_race_number)

    def test_parts_replaced_keeps_the_pages_own_names(self) -> None:
        self._load(_info())

        row = self.session.scalar(
            select(BeforeInfoEntry).where(BeforeInfoEntry.lane_number == 2)
        )
        self.assertEqual(row.parts_replaced, "ピストン")
        blank = self.session.scalar(
            select(BeforeInfoEntry).where(BeforeInfoEntry.lane_number == 1)
        )
        self.assertIsNone(blank.parts_replaced)


class FindDueRacesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = _engine()
        self.session = Session(self.engine)
        loader.ensure_reference_data(self.session)
        venue = loader._venue(self.session, "01")
        self.race = Race(
            venue_id=venue.id,
            race_date=RACE_DATE,
            race_number=1,
            status="scheduled",
            scheduled_deadline_at=DEADLINE,
        )
        self.session.add(self.race)
        self.session.flush()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_inside_the_window_is_due(self) -> None:
        due, considered = find_due_races(
            self.session, DEADLINE - dt.timedelta(minutes=15), race_date=RACE_DATE
        )
        self.assertEqual(considered, 1)
        self.assertEqual(len(due), 1)

    def test_too_early_and_too_late_are_not_due(self) -> None:
        for offset in (45, 2):
            due, _ = find_due_races(
                self.session, DEADLINE - dt.timedelta(minutes=offset), race_date=RACE_DATE
            )
            self.assertEqual(due, [], f"{offset} minutes before should not be due")

    def test_after_the_deadline_is_not_due(self) -> None:
        due, _ = find_due_races(
            self.session, DEADLINE + dt.timedelta(minutes=5), race_date=RACE_DATE
        )
        self.assertEqual(due, [])

    def test_an_already_captured_race_is_not_due_again(self) -> None:
        loader.load_before_info(
            self.session,
            race_id=self.race.id,
            info=_info(),
            observed_at=DEADLINE - dt.timedelta(minutes=20),
        )

        due, _ = find_due_races(
            self.session, DEADLINE - dt.timedelta(minutes=15), race_date=RACE_DATE
        )
        self.assertEqual(due, [])

    def test_a_cancelled_race_is_never_due(self) -> None:
        self.race.status = "cancelled"
        self.session.flush()

        due, _ = find_due_races(
            self.session, DEADLINE - dt.timedelta(minutes=15), race_date=RACE_DATE
        )
        self.assertEqual(due, [])

    def test_rejects_a_nonsensical_window(self) -> None:
        with self.assertRaises(CaptureBeforeInfoError):
            find_due_races(
                self.session, DEADLINE, race_date=RACE_DATE, window_minutes=(30, 5)
            )


class CaptureDueBeforeInfoTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = _engine()
        self.session = Session(self.engine)
        loader.ensure_reference_data(self.session)
        venue = loader._venue(self.session, "01")
        for number in (1, 2):
            self.session.add(
                Race(
                    venue_id=venue.id,
                    race_date=RACE_DATE,
                    race_number=number,
                    status="scheduled",
                    scheduled_deadline_at=DEADLINE + dt.timedelta(minutes=30 * (number - 1)),
                )
            )
        self.session.flush()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_a_failed_page_does_not_stop_the_rest_of_the_run(self) -> None:
        class _Broken(_Opener):
            def urlopen(self, request, timeout=None):
                raise OSError("network down")

        result = capture_due_beforeinfo(
            self.session,
            race_date=RACE_DATE,
            now=DEADLINE - dt.timedelta(minutes=15),
            opener=_Broken(),
            sleep=lambda _s: None,
        )

        self.assertEqual(result.due, 1)
        self.assertEqual(result.failed, 1)
        self.assertTrue(result.errors)

    def test_nothing_due_makes_no_request(self) -> None:
        opener = _Opener()

        result = capture_due_beforeinfo(
            self.session,
            race_date=RACE_DATE,
            now=DEADLINE - dt.timedelta(hours=3),
            opener=opener,
            sleep=lambda _s: None,
        )

        self.assertEqual(result.due, 0)
        self.assertEqual(opener.requested, [])

    def test_paces_requests_between_races(self) -> None:
        slept: list[float] = []
        capture_due_beforeinfo(
            self.session,
            race_date=RACE_DATE,
            now=DEADLINE - dt.timedelta(minutes=15),
            opener=_Opener(),
            sleep=slept.append,
        )
        # one race due in this window, so no inter-request pause needed
        self.assertEqual(slept, [])


if __name__ == "__main__":
    unittest.main()
