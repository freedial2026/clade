"""Live per-race result capture.

The assertions that matter are about restraint: a page without a
confirmed result must write nothing, a race already captured must not be
fetched again, and the rows must stay distinguishable from the K-file's,
whose availability semantics are different by design.
"""

from __future__ import annotations

import datetime as dt
import unittest
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from boat_prediction.db import loader
from boat_prediction.db.capture_results import (
    CaptureResultsError,
    capture_due_results,
    find_due_races,
    store_result,
)
from boat_prediction.db.models import Base, LiveRaceResult, Race
from boat_prediction.raceresult_source import parse_raceresult
from tests.test_raceresult_source import EMPTY_SHELL, SAMPLE

JST = ZoneInfo("Asia/Tokyo")
RACE_DATE = dt.date(2026, 8, 3)
DEADLINE = dt.datetime(2026, 8, 3, 12, 0, tzinfo=JST).astimezone(dt.UTC)


def _engine():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine


class _Opener:
    def __init__(self, html: str = SAMPLE) -> None:
        self.html = html
        self.requested: list[str] = []

    def Request(self, url, headers=None):
        self.requested.append(url)
        return url

    def urlopen(self, request, timeout=None):
        body = self.html.encode("utf-8")

        class _Response:
            def read(self):
                return body

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return _Response()


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = _engine()
        self.session = Session(self.engine)
        loader.ensure_reference_data(self.session)
        venue = loader._venue(self.session, "23")
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


class StoreResultTest(_Base):
    def test_writes_one_row_per_lane(self) -> None:
        written = store_result(
            self.session,
            race_id=self.race.id,
            page=parse_raceresult(SAMPLE),
            observed_at=DEADLINE + dt.timedelta(minutes=8),
        )

        self.assertEqual(written, 6)
        self.assertEqual(len(list(self.session.scalars(select(LiveRaceResult)))), 6)

    def test_payout_and_method_sit_on_the_winning_lane_only(self) -> None:
        store_result(
            self.session,
            race_id=self.race.id,
            page=parse_raceresult(SAMPLE),
            observed_at=DEADLINE + dt.timedelta(minutes=8),
        )

        rows = {r.lane_number: r for r in self.session.scalars(select(LiveRaceResult))}
        self.assertEqual(rows[1].win_payout_yen, 160)
        self.assertEqual(rows[1].winning_method, "逃げ")
        self.assertIsNone(rows[2].win_payout_yen)
        self.assertIsNone(rows[2].winning_method)

    def test_a_page_without_a_result_writes_nothing(self) -> None:
        """A venue not racing and a race not yet confirmed look identical
        to a shell; neither may become "nobody won"."""
        written = store_result(
            self.session,
            race_id=self.race.id,
            page=parse_raceresult(EMPTY_SHELL),
            observed_at=DEADLINE,
        )

        self.assertEqual(written, 0)
        self.assertEqual(len(list(self.session.scalars(select(LiveRaceResult)))), 0)

    def test_availability_is_the_fetch_not_the_next_day(self) -> None:
        observed = DEADLINE + dt.timedelta(minutes=8)
        store_result(
            self.session, race_id=self.race.id, page=parse_raceresult(SAMPLE),
            observed_at=observed,
        )

        for available_at in self.session.scalars(select(LiveRaceResult.available_at)):
            self.assertEqual(available_at, observed.replace(tzinfo=None))

    def test_rows_are_attributable_to_the_live_page_not_the_k_file(self) -> None:
        store_result(
            self.session, race_id=self.race.id, page=parse_raceresult(SAMPLE),
            observed_at=DEADLINE,
        )

        live = loader._source_id(self.session, loader.SOURCE_RACERESULT)
        kfile = loader._source_id(self.session, loader.SOURCE_K_FILE)
        self.assertNotEqual(live, kfile)
        for source_id in self.session.scalars(select(LiveRaceResult.source_id)):
            self.assertEqual(source_id, live)


class FindDueTest(_Base):
    def test_not_due_before_the_settle_time(self) -> None:
        due, considered = find_due_races(
            self.session, DEADLINE + dt.timedelta(minutes=3), race_date=RACE_DATE
        )

        self.assertEqual(considered, 1)
        self.assertEqual(due, [])

    def test_due_after_the_settle_time(self) -> None:
        due, _ = find_due_races(
            self.session, DEADLINE + dt.timedelta(minutes=10), race_date=RACE_DATE
        )

        self.assertEqual(len(due), 1)

    def test_an_already_captured_race_is_not_fetched_again(self) -> None:
        """A result does not change, so one request per race is enough."""
        store_result(
            self.session, race_id=self.race.id, page=parse_raceresult(SAMPLE),
            observed_at=DEADLINE,
        )

        due, _ = find_due_races(
            self.session, DEADLINE + dt.timedelta(minutes=30), race_date=RACE_DATE
        )
        self.assertEqual(due, [])

    def test_a_cancelled_race_is_never_due(self) -> None:
        self.race.status = "cancelled"
        self.session.flush()

        due, _ = find_due_races(
            self.session, DEADLINE + dt.timedelta(minutes=30), race_date=RACE_DATE
        )
        self.assertEqual(due, [])

    def test_rejects_a_negative_settle_time(self) -> None:
        with self.assertRaises(CaptureResultsError):
            find_due_races(self.session, DEADLINE, race_date=RACE_DATE, settle_minutes=-1)


class CaptureRunTest(_Base):
    def test_an_unconfirmed_race_is_counted_and_retried_later(self) -> None:
        result = capture_due_results(
            self.session,
            race_date=RACE_DATE,
            now=DEADLINE + dt.timedelta(minutes=10),
            opener=_Opener(EMPTY_SHELL),
            sleep=lambda _s: None,
        )

        self.assertEqual(result.fetched, 1)
        self.assertEqual(result.not_confirmed_yet, 1)
        self.assertEqual(result.stored, 0)
        # nothing written, so the race is still due
        due, _ = find_due_races(
            self.session, DEADLINE + dt.timedelta(minutes=20), race_date=RACE_DATE
        )
        self.assertEqual(len(due), 1)

    def test_a_failed_fetch_does_not_stop_the_run(self) -> None:
        class _Broken(_Opener):
            def urlopen(self, request, timeout=None):
                raise OSError("network down")

        result = capture_due_results(
            self.session,
            race_date=RACE_DATE,
            now=DEADLINE + dt.timedelta(minutes=10),
            opener=_Broken(),
            sleep=lambda _s: None,
        )

        self.assertEqual(result.failed, 1)
        self.assertTrue(result.errors)

    def test_each_stored_race_is_checkpointed(self) -> None:
        """A single commit at the end cost a 130-race catch-up run once."""
        calls = []
        capture_due_results(
            self.session,
            race_date=RACE_DATE,
            now=DEADLINE + dt.timedelta(minutes=10),
            opener=_Opener(),
            sleep=lambda _s: None,
            checkpoint=lambda: calls.append(1),
        )

        self.assertEqual(len(calls), 1)

    def test_an_unconfirmed_race_is_not_checkpointed(self) -> None:
        calls = []
        capture_due_results(
            self.session,
            race_date=RACE_DATE,
            now=DEADLINE + dt.timedelta(minutes=10),
            opener=_Opener(EMPTY_SHELL),
            sleep=lambda _s: None,
            checkpoint=lambda: calls.append(1),
        )

        self.assertEqual(calls, [])

    def test_nothing_due_makes_no_request(self) -> None:
        opener = _Opener()

        capture_due_results(
            self.session,
            race_date=RACE_DATE,
            now=DEADLINE - dt.timedelta(hours=2),
            opener=opener,
            sleep=lambda _s: None,
        )

        self.assertEqual(opener.requested, [])


if __name__ == "__main__":
    unittest.main()
