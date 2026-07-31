"""Tests for boat_prediction.db.loader.load_odds_day.

Runs against in-memory SQLite with `PRAGMA foreign_keys=ON`, same pattern
as test_db_loader.py. `RaceOdds`/`WinPlaceOdds` fixtures are built
directly rather than through real fetched HTML, same reasoning as the
other loader tests: no official body's data is committed here.
"""

from __future__ import annotations

import datetime as dt
import unittest
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from boat_prediction.db import loader
from boat_prediction.db.models import Base, OddsSnapshot, Race, Venue
from boat_prediction.odds_source import RaceOdds, WinPlaceOdds

JST = ZoneInfo("Asia/Tokyo")
RACE_DATE = dt.date(2026, 6, 1)
VENUE_CODE = "24"
RACE_NUMBER = 1
DEADLINE = dt.datetime(2026, 6, 1, 8, 41, tzinfo=dt.UTC)


def _engine():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection, _record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine


def _race_odds(
    *,
    is_closing: bool = True,
    entries: tuple[WinPlaceOdds, ...] | None = None,
) -> RaceOdds:
    if entries is None:
        entries = (
            WinPlaceOdds(
                lane_number=1,
                racer_name="齋藤和政",
                win_odds=2.1,
                place_odds_low=1.1,
                place_odds_high=1.4,
            ),
            WinPlaceOdds(
                lane_number=2,
                racer_name="山田太郎",
                win_odds=5.6,
                place_odds_low=1.8,
                place_odds_high=2.5,
            ),
        )
    return RaceOdds(is_closing=is_closing, entries=entries)


class LoadOddsDayTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = _engine()
        self.addCleanup(self.engine.dispose)

    def _seed_race(self, session: Session, *, with_deadline: bool = True) -> None:
        loader.ensure_reference_data(session)
        session.commit()

        venue = session.scalar(select(Venue).where(Venue.code == VENUE_CODE))
        race = Race(
            venue_id=venue.id,
            race_date=RACE_DATE,
            race_number=RACE_NUMBER,
            scheduled_deadline_at=DEADLINE if with_deadline else None,
        )
        session.add(race)
        session.commit()

    def test_inserts_win_and_place_snapshots(self) -> None:
        with Session(self.engine) as session:
            self._seed_race(session)

            stats = loader.load_odds_day(
                session, VENUE_CODE, RACE_DATE, RACE_NUMBER, _race_odds()
            )
            session.commit()

            # 2 lanes * 3 values (win, place_low, place_high) = 6.
            self.assertEqual(stats.snapshots, 6)
            rows = session.scalars(select(OddsSnapshot)).all()
            self.assertEqual(len(rows), 6)
            # Naive on purpose: SQLite drops tzinfo on read-back (see
            # test_db_loader.py's identical comment).
            naive_deadline = DEADLINE.replace(tzinfo=None)
            for row in rows:
                self.assertEqual(row.observed_at, naive_deadline)
                self.assertEqual(row.available_at, naive_deadline)
                self.assertTrue(row.is_closing)
            bet_types = {row.bet_type for row in rows}
            self.assertEqual(bet_types, {"win", "place_low", "place_high"})

    def test_reloading_the_same_race_replaces_rather_than_duplicates(self) -> None:
        with Session(self.engine) as session:
            self._seed_race(session)

            loader.load_odds_day(session, VENUE_CODE, RACE_DATE, RACE_NUMBER, _race_odds())
            session.commit()
            loader.load_odds_day(session, VENUE_CODE, RACE_DATE, RACE_NUMBER, _race_odds())
            session.commit()

            self.assertEqual(session.query(OddsSnapshot).count(), 6)

    def test_non_closing_page_is_skipped(self) -> None:
        with Session(self.engine) as session:
            self._seed_race(session)

            stats = loader.load_odds_day(
                session, VENUE_CODE, RACE_DATE, RACE_NUMBER, _race_odds(is_closing=False)
            )

            self.assertEqual(stats.skipped_not_closing, 1)
            self.assertEqual(stats.snapshots, 0)
            self.assertEqual(session.query(OddsSnapshot).count(), 0)

    def test_race_not_found_is_skipped_not_raised(self) -> None:
        with Session(self.engine) as session:
            loader.ensure_reference_data(session)
            session.commit()

            stats = loader.load_odds_day(
                session, VENUE_CODE, RACE_DATE, RACE_NUMBER, _race_odds()
            )

            self.assertEqual(stats.skipped_race_not_found, 1)
            self.assertEqual(stats.snapshots, 0)

    def test_missing_deadline_is_skipped_not_raised(self) -> None:
        with Session(self.engine) as session:
            self._seed_race(session, with_deadline=False)

            stats = loader.load_odds_day(
                session, VENUE_CODE, RACE_DATE, RACE_NUMBER, _race_odds()
            )

            self.assertEqual(stats.skipped_no_deadline, 1)
            self.assertEqual(stats.snapshots, 0)

    def test_scratched_lane_with_no_odds_writes_no_rows_for_it(self) -> None:
        with Session(self.engine) as session:
            self._seed_race(session)
            entries = (
                WinPlaceOdds(
                    lane_number=1,
                    racer_name="欠場選手",
                    win_odds=None,
                    place_odds_low=None,
                    place_odds_high=None,
                ),
                WinPlaceOdds(
                    lane_number=2,
                    racer_name="山田太郎",
                    win_odds=5.6,
                    place_odds_low=1.8,
                    place_odds_high=2.5,
                ),
            )

            stats = loader.load_odds_day(
                session, VENUE_CODE, RACE_DATE, RACE_NUMBER, _race_odds(entries=entries)
            )

            self.assertEqual(stats.snapshots, 3)
            self.assertEqual(stats.skipped_missing_value, 3)
            rows = session.scalars(select(OddsSnapshot)).all()
            self.assertTrue(all(row.combination == "2" for row in rows))


if __name__ == "__main__":
    unittest.main()
