"""Tests for `db.capture_odds`.

No network: `opener` is a fake and the sleeper is a recorder, so the
pacing is asserted rather than waited out.
"""

from __future__ import annotations

import datetime as dt
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from boat_prediction.db import capture_odds, loader
from boat_prediction.db.capture_odds import DEFAULT_LEAD_MINUTES
from boat_prediction.db.models import Base, OddsSnapshot, Race, Venue
from boat_prediction.odds_source import RaceOdds, WinPlaceOdds

JST = loader.JST
RACE_DATE = dt.date(2026, 6, 1)
# 12:00 JST deadline, held in UTC the way the loader stores it.
DEADLINE = dt.datetime(2026, 6, 1, 3, 0, tzinfo=dt.UTC)

_ODDS_HTML = "<html>odds</html>"


def _engine():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection, _record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine


def _parsed(is_closing: bool = False, win: float = 2.5) -> RaceOdds:
    return RaceOdds(
        is_closing=is_closing,
        entries=(
            WinPlaceOdds(
                lane_number=1,
                racer_name="齋藤和政",
                win_odds=win,
                place_odds_low=1.1,
                place_odds_high=1.4,
            ),
        ),
    )


def _clock_at(start: dt.datetime, step_seconds: float = 3.0):
    """A clock that starts at `start` and advances one request's spacing
    per call, so a run's readings get distinct, ordered timestamps the
    way real pacing produces them."""
    state = {"now": start}

    def _now() -> dt.datetime:
        value = state["now"]
        state["now"] = value + dt.timedelta(seconds=step_seconds)
        return value

    return _now


class FakeOpener:
    """Stands in for `urllib.request`, recording the URLs asked for."""

    def __init__(self) -> None:
        self.urls: list[str] = []

    def Request(self, url, headers=None):  # noqa: N802 - mirrors urllib
        self.urls.append(url)
        return url

    def urlopen(self, request, timeout=None):
        opener = self

        class _Response:
            def read(self):
                return _ODDS_HTML.encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        del opener
        return _Response()


class CaptureOddsTestBase(unittest.TestCase):
    def setUp(self) -> None:
        # The HTML parser has its own tests; these are about which races
        # get captured and how the readings are stamped.
        patcher = patch(
            "boat_prediction.db.capture_odds.parse_win_place_odds",
            return_value=_parsed(),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.engine = _engine()
        self.addCleanup(self.engine.dispose)
        with Session(self.engine) as session:
            loader.ensure_reference_data(session)
            venue = session.scalar(select(Venue).where(Venue.code == "24"))
            session.add(
                Race(
                    venue_id=venue.id,
                    race_date=RACE_DATE,
                    race_number=1,
                    status="scheduled",
                    scheduled_deadline_at=DEADLINE,
                )
            )
            session.commit()


class FindDueRacesTest(CaptureOddsTestBase):
    def test_a_race_is_due_at_its_lead_time(self) -> None:
        with Session(self.engine) as session:
            due, considered = capture_odds.find_due_races(
                session,
                DEADLINE - dt.timedelta(minutes=10),
                race_date=RACE_DATE,
                lead_minutes=(10,),
            )

            self.assertEqual(considered, 1)
            self.assertEqual(len(due), 1)
            self.assertEqual(due[0].lead_minutes, 10)
            self.assertEqual(due[0].venue_code, "24")

    def test_a_race_outside_every_window_is_not_due(self) -> None:
        with Session(self.engine) as session:
            due, _ = capture_odds.find_due_races(
                session,
                DEADLINE - dt.timedelta(minutes=30),
                race_date=RACE_DATE,
                lead_minutes=(10, 2),
            )

            self.assertEqual(due, [])

    def test_a_race_past_its_deadline_is_not_due(self) -> None:
        with Session(self.engine) as session:
            due, _ = capture_odds.find_due_races(
                session,
                DEADLINE + dt.timedelta(minutes=10),
                race_date=RACE_DATE,
                lead_minutes=(10, 2),
            )

            self.assertEqual(due, [])

    def test_a_round_already_captured_is_not_due_again(self) -> None:
        # What makes overlapping cron runs safe: the check is against the
        # snapshots themselves, so no state is kept outside the database.
        with Session(self.engine) as session:
            capture_odds.capture_due_odds(
                session,
                now=DEADLINE - dt.timedelta(minutes=10),
                race_date=RACE_DATE,
                lead_minutes=(10,),
                opener=FakeOpener(),
                sleeper=lambda _: None,
                clock=_clock_at(DEADLINE - dt.timedelta(minutes=10)),
            )
            session.commit()

            due, _ = capture_odds.find_due_races(
                session,
                DEADLINE - dt.timedelta(minutes=9),
                race_date=RACE_DATE,
                lead_minutes=(10,),
            )

            self.assertEqual(due, [])

    def test_only_the_nearest_window_fires_when_two_overlap(self) -> None:
        with Session(self.engine) as session:
            due, _ = capture_odds.find_due_races(
                session,
                DEADLINE - dt.timedelta(minutes=4),
                race_date=RACE_DATE,
                lead_minutes=(5, 3),
                tolerance_minutes=2,
            )

            self.assertEqual(len(due), 1)
            self.assertEqual(due[0].lead_minutes, 5)

    def test_a_race_with_no_deadline_is_never_due(self) -> None:
        with Session(self.engine) as session:
            session.execute(
                Race.__table__.update().values(scheduled_deadline_at=None)
            )
            session.commit()

            due, considered = capture_odds.find_due_races(
                session,
                DEADLINE - dt.timedelta(minutes=10),
                race_date=RACE_DATE,
                lead_minutes=(10,),
            )

            self.assertEqual((due, considered), ([], 0))

    def test_rejects_an_empty_lead_list_and_a_zero_tolerance(self) -> None:
        with Session(self.engine) as session:
            with self.assertRaises(capture_odds.CaptureOddsError):
                capture_odds.find_due_races(
                    session, DEADLINE, race_date=RACE_DATE, lead_minutes=()
                )
            with self.assertRaises(capture_odds.CaptureOddsError):
                capture_odds.find_due_races(
                    session, DEADLINE, race_date=RACE_DATE, tolerance_minutes=0
                )


class CaptureDueOddsTest(CaptureOddsTestBase):
    def test_stores_a_pre_deadline_reading_available_before_the_deadline(self) -> None:
        with Session(self.engine) as session:
            result = capture_odds.capture_due_odds(
                session,
                now=DEADLINE - dt.timedelta(minutes=10),
                race_date=RACE_DATE,
                lead_minutes=(10,),
                opener=FakeOpener(),
                sleeper=lambda _: None,
                clock=_clock_at(DEADLINE - dt.timedelta(minutes=10)),
            )
            session.commit()

            self.assertEqual(result.fetched, 1)
            self.assertEqual(result.stats.snapshots, 3)  # win + place_low + place_high
            snapshot = session.scalar(select(OddsSnapshot))
            # The whole point of this job: usable while betting is open.
            observed_at = snapshot.observed_at
            if observed_at.tzinfo is None:
                observed_at = observed_at.replace(tzinfo=dt.UTC)
            self.assertLess(observed_at, DEADLINE)
            self.assertEqual(snapshot.available_at, snapshot.observed_at)
            self.assertFalse(snapshot.is_closing)

    def test_requests_the_expected_url(self) -> None:
        opener = FakeOpener()
        with Session(self.engine) as session:
            capture_odds.capture_due_odds(
                session,
                now=DEADLINE - dt.timedelta(minutes=10),
                race_date=RACE_DATE,
                lead_minutes=(10,),
                opener=opener,
                sleeper=lambda _: None,
            )

            self.assertEqual(
                opener.urls,
                ["https://www.boatrace.jp/owpc/pc/race/oddstf?rno=1&jcd=24&hd=20260601"],
            )

    def test_makes_no_request_when_nothing_is_due(self) -> None:
        opener = FakeOpener()
        with Session(self.engine) as session:
            result = capture_odds.capture_due_odds(
                session,
                now=DEADLINE - dt.timedelta(hours=3),
                race_date=RACE_DATE,
                lead_minutes=(10,),
                opener=opener,
                sleeper=lambda _: None,
            )

            self.assertEqual(opener.urls, [])
            self.assertEqual(result.fetched, 0)
            self.assertEqual(result.races_considered, 1)

    def test_readings_accumulate_rather_than_replace(self) -> None:
        # The archive loader replaces a race's snapshots; this one must
        # not, or there is no time series.
        with Session(self.engine) as session:
            for lead in (10, 2):
                capture_odds.capture_due_odds(
                    session,
                    now=DEADLINE - dt.timedelta(minutes=lead),
                    race_date=RACE_DATE,
                    lead_minutes=(lead,),
                    tolerance_minutes=1,
                    opener=FakeOpener(),
                    sleeper=lambda _: None,
                    clock=_clock_at(DEADLINE - dt.timedelta(minutes=lead)),
                )
                session.commit()

            self.assertEqual(session.query(OddsSnapshot).count(), 6)

    def test_one_failing_race_does_not_stop_the_others(self) -> None:
        class HalfBrokenOpener(FakeOpener):
            def urlopen(self, request, timeout=None):
                if "rno=1&" in str(request):
                    raise OSError("connection reset")
                return super().urlopen(request, timeout=timeout)

        with Session(self.engine) as session:
            venue = session.scalar(select(Venue).where(Venue.code == "24"))
            session.add(
                Race(
                    venue_id=venue.id,
                    race_date=RACE_DATE,
                    race_number=2,
                    status="scheduled",
                    scheduled_deadline_at=DEADLINE,
                )
            )
            session.commit()

            result = capture_odds.capture_due_odds(
                session,
                now=DEADLINE - dt.timedelta(minutes=10),
                race_date=RACE_DATE,
                lead_minutes=(10,),
                opener=HalfBrokenOpener(),
                sleeper=lambda _: None,
            )
            session.commit()

            self.assertEqual(result.fetched, 1)
            self.assertEqual(len(result.failed), 1)
            self.assertEqual(session.query(OddsSnapshot).count(), 3)

    def test_spaces_requests_and_does_not_sleep_after_the_last(self) -> None:
        sleeps: list[float] = []
        with Session(self.engine) as session:
            venue = session.scalar(select(Venue).where(Venue.code == "24"))
            session.add(
                Race(
                    venue_id=venue.id,
                    race_date=RACE_DATE,
                    race_number=2,
                    status="scheduled",
                    scheduled_deadline_at=DEADLINE,
                )
            )
            session.commit()

            capture_odds.capture_due_odds(
                session,
                now=DEADLINE - dt.timedelta(minutes=10),
                race_date=RACE_DATE,
                lead_minutes=(10,),
                opener=FakeOpener(),
                sleeper=sleeps.append,
            )

            self.assertEqual(sleeps, [3.0])

    def test_rejects_a_delay_under_one_second(self) -> None:
        with Session(self.engine) as session, self.assertRaises(capture_odds.CaptureOddsError):
            capture_odds.capture_due_odds(session, delay_seconds=0.1)


class LoadOddsObservationTest(CaptureOddsTestBase):
    def test_is_idempotent_at_the_same_observed_at(self) -> None:
        observed_at = DEADLINE - dt.timedelta(minutes=10)
        with Session(self.engine) as session:
            for _ in range(2):
                stats = loader.load_odds_observation(
                    session, "24", RACE_DATE, 1, _parsed(), observed_at
                )
                session.commit()

            self.assertEqual(session.query(OddsSnapshot).count(), 3)
            self.assertEqual(stats.skipped_already_observed, 3)

    def test_accepts_a_non_closing_page(self) -> None:
        # load_odds_day rejects these; this function exists for them.
        with Session(self.engine) as session:
            stats = loader.load_odds_observation(
                session, "24", RACE_DATE, 1, _parsed(is_closing=False), DEADLINE
            )

            self.assertEqual(stats.snapshots, 3)
            self.assertEqual(stats.skipped_not_closing, 0)

    def test_reports_a_race_that_is_not_loaded_yet(self) -> None:
        with Session(self.engine) as session:
            stats = loader.load_odds_observation(
                session, "24", RACE_DATE, 12, _parsed(), DEADLINE
            )

            self.assertEqual(stats.skipped_race_not_found, 1)
            self.assertEqual(stats.snapshots, 0)


if __name__ == "__main__":
    unittest.main()


class LeadTimeBracketsPreviewTest(unittest.TestCase):
    """The default lead times must bracket 直前情報 publication.

    Without a reading from before the exhibition data appears, the
    market's reaction to it cannot be measured -- and unlike a modelling
    choice, that cannot be fixed later, because the price at that moment
    goes unrecorded. Locking the property here rather than the numbers,
    so a future retune cannot quietly drop it.
    """

    OBSERVED_PUBLICATION_MINUTES = 29
    """The earliest the live capture found 直前情報 complete on 2026-08-01
    (range 13-29 minutes before the deadline). A lead time must sit
    further out than this to be on the other side of publication."""

    def test_at_least_one_lead_precedes_preview_publication(self) -> None:
        self.assertTrue(
            any(lead > self.OBSERVED_PUBLICATION_MINUTES for lead in DEFAULT_LEAD_MINUTES),
            f"no lead in {DEFAULT_LEAD_MINUTES} is before 直前情報 publication",
        )

    def test_at_least_one_lead_follows_preview_publication(self) -> None:
        """The other side of the bracket."""
        self.assertTrue(
            any(lead < self.OBSERVED_PUBLICATION_MINUTES for lead in DEFAULT_LEAD_MINUTES)
        )

    def test_leads_are_distinct_and_descending_when_sorted(self) -> None:
        self.assertEqual(len(set(DEFAULT_LEAD_MINUTES)), len(DEFAULT_LEAD_MINUTES))
        self.assertTrue(all(lead > 0 for lead in DEFAULT_LEAD_MINUTES))
