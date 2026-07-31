"""Tests for boat_prediction.db.load_odds_archive.

Uses a fake `parse` (see module docstring on `load_odds_archive`'s
`parse` parameter) so no real fetched odds HTML needs to exist on disk --
just a placeholder file at the expected path, to exercise file discovery,
the ledger, and error recovery, following test_db_load_archive.py's
pattern.
"""

from __future__ import annotations

import datetime as dt
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import create_engine, event, select

from boat_prediction.db import load_odds_archive, loader
from boat_prediction.db.models import Base, OddsSnapshot, Race, Venue
from boat_prediction.db.session import create_session_factory
from boat_prediction.odds_source import OddsSourceError, RaceOdds, WinPlaceOdds

RACE_DATE = dt.date(2026, 6, 1)
DEADLINE = dt.datetime(2026, 6, 1, 8, 41, tzinfo=dt.UTC)


def _make_raw_root(root: Path, day_venue_races: list[tuple[dt.date, str, int]]) -> None:
    """Create placeholder odds pages at the paths `load_odds_archive`
    expects, for the given (date, venue_code, race_number) triples."""
    for target_date, venue_code, race_number in day_venue_races:
        day_dir = root / target_date.strftime("%Y%m%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        (day_dir / f"{venue_code}_{race_number:02d}.html").write_text(
            "placeholder", encoding="utf-8"
        )


def _fake_parse(_html: str) -> RaceOdds:
    return RaceOdds(
        is_closing=True,
        entries=(
            WinPlaceOdds(
                lane_number=1,
                racer_name="齋藤和政",
                win_odds=2.1,
                place_odds_low=1.1,
                place_odds_high=1.4,
            ),
        ),
    )


def _session_factory():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection, _record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine, create_session_factory(engine)


def _seed_race(factory, venue_code: str, race_number: int) -> None:
    with factory() as session:
        loader.ensure_reference_data(session)
        session.commit()
        venue = session.scalar(select(Venue).where(Venue.code == venue_code))
        session.add(
            Race(
                venue_id=venue.id,
                race_date=RACE_DATE,
                race_number=race_number,
                scheduled_deadline_at=DEADLINE,
            )
        )
        session.commit()


class LoadOddsArchiveTest(unittest.TestCase):
    def test_raises_on_missing_raw_root(self) -> None:
        engine, factory = _session_factory()
        self.addCleanup(engine.dispose)

        with self.assertRaises(load_odds_archive.OddsArchiveLoadError):
            load_odds_archive.load_odds_archive(
                Path("does-not-exist"), factory, parse=_fake_parse, progress=None
            )

    def test_raises_when_end_date_before_start_date(self) -> None:
        engine, factory = _session_factory()
        self.addCleanup(engine.dispose)

        with TemporaryDirectory() as tmp, self.assertRaises(load_odds_archive.OddsArchiveLoadError):
            load_odds_archive.load_odds_archive(
                Path(tmp),
                factory,
                start_date=dt.date(2026, 6, 2),
                end_date=dt.date(2026, 6, 1),
                parse=_fake_parse,
                progress=None,
            )

    def test_loads_present_files_and_counts_missing_dates(self) -> None:
        engine, factory = _session_factory()
        self.addCleanup(engine.dispose)
        _seed_race(factory, "24", 1)

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_raw_root(root, [(RACE_DATE, "24", 1)])
            ledger_path = root / "ledger.json"

            result = load_odds_archive.load_odds_archive(
                root,
                factory,
                start_date=RACE_DATE,
                end_date=RACE_DATE + dt.timedelta(days=1),
                ledger_path=ledger_path,
                parse=_fake_parse,
                progress=None,
            )

            self.assertEqual(result.loaded_files, 1)
            self.assertEqual(result.skipped_missing, 1)
            self.assertEqual(result.failed, [])
            self.assertEqual(result.stats.snapshots, 3)
            with factory() as session:
                self.assertEqual(session.query(OddsSnapshot).count(), 3)
            self.assertTrue(ledger_path.is_file())

    def test_second_run_skips_files_already_recorded_in_the_ledger(self) -> None:
        engine, factory = _session_factory()
        self.addCleanup(engine.dispose)
        _seed_race(factory, "24", 1)

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_raw_root(root, [(RACE_DATE, "24", 1)])
            ledger_path = root / "ledger.json"
            kwargs = {
                "start_date": RACE_DATE,
                "end_date": RACE_DATE,
                "ledger_path": ledger_path,
                "parse": _fake_parse,
                "progress": None,
            }

            first = load_odds_archive.load_odds_archive(root, factory, **kwargs)
            second = load_odds_archive.load_odds_archive(root, factory, **kwargs)

            self.assertEqual(first.loaded_files, 1)
            self.assertEqual(second.loaded_files, 0)
            self.assertEqual(second.skipped_already_loaded, 1)
            with factory() as session:
                self.assertEqual(session.query(OddsSnapshot).count(), 3)

    def test_force_reloads_files_already_recorded_in_the_ledger(self) -> None:
        engine, factory = _session_factory()
        self.addCleanup(engine.dispose)
        _seed_race(factory, "24", 1)

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_raw_root(root, [(RACE_DATE, "24", 1)])
            ledger_path = root / "ledger.json"
            kwargs = {
                "start_date": RACE_DATE,
                "end_date": RACE_DATE,
                "ledger_path": ledger_path,
                "parse": _fake_parse,
                "progress": None,
            }

            load_odds_archive.load_odds_archive(root, factory, **kwargs)
            forced = load_odds_archive.load_odds_archive(root, factory, force=True, **kwargs)

            self.assertEqual(forced.loaded_files, 1)
            self.assertEqual(forced.skipped_already_loaded, 0)

    def test_dry_run_does_not_write_to_the_database_or_ledger(self) -> None:
        engine, factory = _session_factory()
        self.addCleanup(engine.dispose)
        _seed_race(factory, "24", 1)

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_raw_root(root, [(RACE_DATE, "24", 1)])
            ledger_path = root / "ledger.json"

            result = load_odds_archive.load_odds_archive(
                root,
                factory,
                start_date=RACE_DATE,
                end_date=RACE_DATE,
                ledger_path=ledger_path,
                dry_run=True,
                parse=_fake_parse,
                progress=None,
            )

            self.assertEqual(result.loaded_files, 1)
            self.assertFalse(ledger_path.is_file())
            with factory() as session:
                self.assertEqual(session.query(OddsSnapshot).count(), 0)

    def test_a_file_that_fails_to_parse_is_recorded_and_does_not_stop_the_run(self) -> None:
        engine, factory = _session_factory()
        self.addCleanup(engine.dispose)
        _seed_race(factory, "24", 1)
        _seed_race(factory, "24", 2)

        def parse_or_raise(html: str) -> RaceOdds:
            if html == "bad":
                raise OddsSourceError("boom")
            return _fake_parse(html)

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            day_dir = root / RACE_DATE.strftime("%Y%m%d")
            day_dir.mkdir(parents=True)
            (day_dir / "24_01.html").write_text("bad", encoding="utf-8")
            (day_dir / "24_02.html").write_text("ok", encoding="utf-8")
            ledger_path = root / "ledger.json"

            result = load_odds_archive.load_odds_archive(
                root,
                factory,
                start_date=RACE_DATE,
                end_date=RACE_DATE,
                ledger_path=ledger_path,
                parse=parse_or_raise,
                progress=None,
            )

            self.assertEqual(len(result.failed), 1)
            self.assertIn("24_01.html", result.failed[0][0])
            self.assertEqual(result.loaded_files, 1)
            with factory() as session:
                self.assertEqual(session.query(OddsSnapshot).count(), 3)


if __name__ == "__main__":
    unittest.main()
