"""Tests for boat_prediction.db.load_trifecta_archive.

Uses fake parsers (see module docstring on `load_trifecta_archive`'s
`parsers` parameter) so no real fetched HTML needs to exist on disk --
just placeholder files at the expected paths, following
test_load_odds_archive.py's pattern exactly.
"""

from __future__ import annotations

import datetime as dt
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import create_engine, event, select

from boat_prediction.db import load_trifecta_archive, loader
from boat_prediction.db.models import Base, OddsSnapshot, Race, Venue
from boat_prediction.db.session import create_session_factory
from boat_prediction.odds_source import CombinationOdds, OddsSourceError, RaceCombinationOdds

RACE_DATE = dt.date(2026, 6, 1)
DEADLINE = dt.datetime(2026, 6, 1, 8, 41, tzinfo=dt.UTC)


def _make_raw_root(
    root: Path, day_venue_races_pages: list[tuple[dt.date, str, int, str]]
) -> None:
    """Create placeholder pages at the paths `load_trifecta_archive`
    expects, for the given (date, venue_code, race_number, page)
    quadruples."""
    for target_date, venue_code, race_number, page in day_venue_races_pages:
        day_dir = root / target_date.strftime("%Y%m%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        (day_dir / f"{venue_code}_{race_number:02d}_{page}.html").write_text(
            "placeholder", encoding="utf-8"
        )


def _fake_trifecta(_html: str) -> RaceCombinationOdds:
    return RaceCombinationOdds(
        is_closing=True,
        entries=(CombinationOdds(bet_type="trifecta", combination="1-2-3", odds=11.5),),
    )


def _fake_sanrenpuku(_html: str) -> RaceCombinationOdds:
    return RaceCombinationOdds(
        is_closing=True,
        entries=(CombinationOdds(bet_type="sanrenpuku", combination="1-2-3", odds=4.3),),
    )


def _fake_wide(_html: str) -> RaceCombinationOdds:
    return RaceCombinationOdds(
        is_closing=True,
        entries=(CombinationOdds(bet_type="wide", combination="1-2", odds=2.5),),
    )


_FAKE_PARSERS = {
    "odds3t": _fake_trifecta,
    "odds3f": _fake_sanrenpuku,
    "oddsk": _fake_wide,
}


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


class LoadTrifectaArchiveTest(unittest.TestCase):
    def test_raises_on_missing_raw_root(self) -> None:
        engine, factory = _session_factory()
        self.addCleanup(engine.dispose)

        with self.assertRaises(load_trifecta_archive.TrifectaArchiveLoadError):
            load_trifecta_archive.load_trifecta_archive(
                Path("does-not-exist"), factory, parsers=_FAKE_PARSERS, progress=None
            )

    def test_raises_when_end_date_before_start_date(self) -> None:
        engine, factory = _session_factory()
        self.addCleanup(engine.dispose)

        with (
            TemporaryDirectory() as tmp,
            self.assertRaises(load_trifecta_archive.TrifectaArchiveLoadError),
        ):
            load_trifecta_archive.load_trifecta_archive(
                Path(tmp),
                factory,
                start_date=dt.date(2026, 6, 2),
                end_date=dt.date(2026, 6, 1),
                parsers=_FAKE_PARSERS,
                progress=None,
            )

    def test_loads_all_three_pages_for_one_race(self) -> None:
        """The actual thing specific to this loader: one race produces
        three files, one per pool, and all three must land."""
        engine, factory = _session_factory()
        self.addCleanup(engine.dispose)
        _seed_race(factory, "24", 1)

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_raw_root(
                root,
                [
                    (RACE_DATE, "24", 1, "odds3t"),
                    (RACE_DATE, "24", 1, "odds3f"),
                    (RACE_DATE, "24", 1, "oddsk"),
                ],
            )
            ledger_path = root / "ledger.json"

            result = load_trifecta_archive.load_trifecta_archive(
                root,
                factory,
                start_date=RACE_DATE,
                end_date=RACE_DATE + dt.timedelta(days=1),
                ledger_path=ledger_path,
                parsers=_FAKE_PARSERS,
                progress=None,
            )

            self.assertEqual(result.loaded_files, 3)
            self.assertEqual(result.skipped_missing, 1)
            self.assertEqual(result.failed, [])
            self.assertEqual(result.stats.snapshots, 3)
            with factory() as session:
                bet_types = {row.bet_type for row in session.scalars(select(OddsSnapshot))}
            self.assertEqual(bet_types, {"trifecta", "sanrenpuku", "wide"})
            self.assertTrue(ledger_path.is_file())

    def test_second_run_skips_files_already_recorded_in_the_ledger(self) -> None:
        engine, factory = _session_factory()
        self.addCleanup(engine.dispose)
        _seed_race(factory, "24", 1)

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_raw_root(root, [(RACE_DATE, "24", 1, "odds3t")])
            ledger_path = root / "ledger.json"
            kwargs = {
                "start_date": RACE_DATE,
                "end_date": RACE_DATE,
                "ledger_path": ledger_path,
                "parsers": _FAKE_PARSERS,
                "progress": None,
            }

            first = load_trifecta_archive.load_trifecta_archive(root, factory, **kwargs)
            second = load_trifecta_archive.load_trifecta_archive(root, factory, **kwargs)

            self.assertEqual(first.loaded_files, 1)
            self.assertEqual(second.loaded_files, 0)
            self.assertEqual(second.skipped_already_loaded, 1)
            with factory() as session:
                self.assertEqual(session.query(OddsSnapshot).count(), 1)

    def test_force_reloads_files_already_recorded_in_the_ledger(self) -> None:
        engine, factory = _session_factory()
        self.addCleanup(engine.dispose)
        _seed_race(factory, "24", 1)

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_raw_root(root, [(RACE_DATE, "24", 1, "odds3t")])
            ledger_path = root / "ledger.json"
            kwargs = {
                "start_date": RACE_DATE,
                "end_date": RACE_DATE,
                "ledger_path": ledger_path,
                "parsers": _FAKE_PARSERS,
                "progress": None,
            }

            load_trifecta_archive.load_trifecta_archive(root, factory, **kwargs)
            forced = load_trifecta_archive.load_trifecta_archive(root, factory, force=True, **kwargs)

            self.assertEqual(forced.loaded_files, 1)
            self.assertEqual(forced.skipped_already_loaded, 0)

    def test_dry_run_does_not_write_to_the_database_or_ledger(self) -> None:
        engine, factory = _session_factory()
        self.addCleanup(engine.dispose)
        _seed_race(factory, "24", 1)

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_raw_root(root, [(RACE_DATE, "24", 1, "odds3t")])
            ledger_path = root / "ledger.json"

            result = load_trifecta_archive.load_trifecta_archive(
                root,
                factory,
                start_date=RACE_DATE,
                end_date=RACE_DATE,
                ledger_path=ledger_path,
                dry_run=True,
                parsers=_FAKE_PARSERS,
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

        def parse_or_raise(html: str) -> RaceCombinationOdds:
            if html == "bad":
                raise OddsSourceError("boom")
            return _fake_trifecta(html)

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            day_dir = root / RACE_DATE.strftime("%Y%m%d")
            day_dir.mkdir(parents=True)
            (day_dir / "24_01_odds3t.html").write_text("bad", encoding="utf-8")
            (day_dir / "24_02_odds3t.html").write_text("ok", encoding="utf-8")
            ledger_path = root / "ledger.json"

            result = load_trifecta_archive.load_trifecta_archive(
                root,
                factory,
                start_date=RACE_DATE,
                end_date=RACE_DATE,
                ledger_path=ledger_path,
                parsers={"odds3t": parse_or_raise},
                progress=None,
            )

            self.assertEqual(len(result.failed), 1)
            self.assertIn("24_01_odds3t.html", result.failed[0][0])
            self.assertEqual(result.loaded_files, 1)
            with factory() as session:
                self.assertEqual(session.query(OddsSnapshot).count(), 1)

    def test_does_not_pick_up_a_win_place_file_from_a_shared_directory(self) -> None:
        """`fetch_trifecta_family_range` can be pointed at the same
        directory a win/place `fetch_range` already populated (to share
        the `_venues.txt` marker); the two-underscore win/place filename
        shape must not be mistaken for a three-underscore trifecta one."""
        engine, factory = _session_factory()
        self.addCleanup(engine.dispose)
        _seed_race(factory, "24", 1)

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            day_dir = root / RACE_DATE.strftime("%Y%m%d")
            day_dir.mkdir(parents=True)
            (day_dir / "24_01.html").write_text("win/place page", encoding="utf-8")
            (day_dir / "24_01_odds3t.html").write_text("trifecta page", encoding="utf-8")
            ledger_path = root / "ledger.json"

            result = load_trifecta_archive.load_trifecta_archive(
                root,
                factory,
                start_date=RACE_DATE,
                end_date=RACE_DATE,
                ledger_path=ledger_path,
                parsers=_FAKE_PARSERS,
                progress=None,
            )

            self.assertEqual(result.loaded_files, 1)
            with factory() as session:
                bet_types = {row.bet_type for row in session.scalars(select(OddsSnapshot))}
            self.assertEqual(bet_types, {"trifecta"})


if __name__ == "__main__":
    unittest.main()
