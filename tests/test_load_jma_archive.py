"""Tests for boat_prediction.db.load_jma_archive.

Uses a fake `parse` (see module docstring on `load_jma_archive`'s
`parse` parameter) so no real fetched JMA HTML needs to exist on disk --
just a placeholder file at the expected path, to exercise file
discovery, the ledger, and error recovery, following
test_load_odds_archive.py's pattern.
"""

from __future__ import annotations

import datetime as dt
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import create_engine, event

from boat_prediction.db import load_jma_archive
from boat_prediction.db.models import Base, WeatherObservation
from boat_prediction.db.session import create_session_factory
from boat_prediction.jma_weather_source import DailyWeather, JmaWeatherSourceError


def _make_raw_root(root: Path, venue_year_months: list[tuple[str, int, int]]) -> None:
    """Create placeholder JMA pages at the paths `load_jma_archive`
    expects, for the given (venue_code, year, month) triples.

    Content is distinct per file (not a fixed literal) because the
    ledger key is the file's content hash: two files sharing one
    literal placeholder string would hash identically and the second
    would be mistaken for an already-loaded duplicate of the first."""
    for venue_code, year, month in venue_year_months:
        venue_dir = root / venue_code
        venue_dir.mkdir(parents=True, exist_ok=True)
        (venue_dir / f"{year:04d}{month:02d}.html").write_text(
            f"placeholder {venue_code} {year:04d}{month:02d}", encoding="utf-8"
        )


def _fake_parse(_html: str, year: int, month: int) -> tuple[DailyWeather, ...]:
    return (DailyWeather(**{**_FIELDS, "date_iso": f"{year:04d}-{month:02d}-01"}),)


_FIELDS = {
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


def _session_factory():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection, _record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine, create_session_factory(engine)


class LoadJmaArchiveTest(unittest.TestCase):
    def test_raises_on_missing_raw_root(self) -> None:
        engine, factory = _session_factory()
        self.addCleanup(engine.dispose)

        with self.assertRaises(load_jma_archive.JmaArchiveLoadError):
            load_jma_archive.load_jma_archive(
                Path("does-not-exist"), factory, parse=_fake_parse, progress=None
            )

    def test_raises_when_end_date_before_start_date(self) -> None:
        engine, factory = _session_factory()
        self.addCleanup(engine.dispose)

        with TemporaryDirectory() as tmp, self.assertRaises(load_jma_archive.JmaArchiveLoadError):
            load_jma_archive.load_jma_archive(
                Path(tmp),
                factory,
                start_date=dt.date(2026, 7, 1),
                end_date=dt.date(2026, 6, 1),
                parse=_fake_parse,
                progress=None,
            )

    def test_loads_present_files_and_counts_missing_venue_months(self) -> None:
        engine, factory = _session_factory()
        self.addCleanup(engine.dispose)

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_raw_root(root, [("24", 2026, 6)])
            ledger_path = root / "ledger.json"

            result = load_jma_archive.load_jma_archive(
                root,
                factory,
                start_date=dt.date(2026, 6, 1),
                end_date=dt.date(2026, 6, 30),
                ledger_path=ledger_path,
                parse=_fake_parse,
                progress=None,
            )

            self.assertEqual(result.loaded_files, 1)
            # 24 venues x 1 month, minus the one file present.
            self.assertEqual(result.skipped_missing, 23)
            self.assertEqual(result.failed, [])
            self.assertEqual(result.stats.observations, 1)
            with factory() as session:
                self.assertEqual(session.query(WeatherObservation).count(), 1)
            self.assertTrue(ledger_path.is_file())

    def test_second_run_skips_files_already_recorded_in_the_ledger(self) -> None:
        engine, factory = _session_factory()
        self.addCleanup(engine.dispose)

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_raw_root(root, [("24", 2026, 6)])
            ledger_path = root / "ledger.json"
            kwargs = {
                "start_date": dt.date(2026, 6, 1),
                "end_date": dt.date(2026, 6, 30),
                "ledger_path": ledger_path,
                "parse": _fake_parse,
                "progress": None,
            }

            first = load_jma_archive.load_jma_archive(root, factory, **kwargs)
            second = load_jma_archive.load_jma_archive(root, factory, **kwargs)

            self.assertEqual(first.loaded_files, 1)
            self.assertEqual(second.loaded_files, 0)
            self.assertEqual(second.skipped_already_loaded, 1)
            with factory() as session:
                self.assertEqual(session.query(WeatherObservation).count(), 1)

    def test_force_reloads_files_already_recorded_in_the_ledger(self) -> None:
        engine, factory = _session_factory()
        self.addCleanup(engine.dispose)

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_raw_root(root, [("24", 2026, 6)])
            ledger_path = root / "ledger.json"
            kwargs = {
                "start_date": dt.date(2026, 6, 1),
                "end_date": dt.date(2026, 6, 30),
                "ledger_path": ledger_path,
                "parse": _fake_parse,
                "progress": None,
            }

            load_jma_archive.load_jma_archive(root, factory, **kwargs)
            forced = load_jma_archive.load_jma_archive(root, factory, force=True, **kwargs)

            self.assertEqual(forced.loaded_files, 1)
            self.assertEqual(forced.skipped_already_loaded, 0)

    def test_dry_run_does_not_write_to_the_database_or_ledger(self) -> None:
        engine, factory = _session_factory()
        self.addCleanup(engine.dispose)

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_raw_root(root, [("24", 2026, 6)])
            ledger_path = root / "ledger.json"

            result = load_jma_archive.load_jma_archive(
                root,
                factory,
                start_date=dt.date(2026, 6, 1),
                end_date=dt.date(2026, 6, 30),
                ledger_path=ledger_path,
                dry_run=True,
                parse=_fake_parse,
                progress=None,
            )

            self.assertEqual(result.loaded_files, 1)
            self.assertFalse(ledger_path.is_file())
            with factory() as session:
                self.assertEqual(session.query(WeatherObservation).count(), 0)

    def test_a_file_that_fails_to_parse_is_recorded_and_does_not_stop_the_run(self) -> None:
        engine, factory = _session_factory()
        self.addCleanup(engine.dispose)

        def parse_or_raise(html: str, year: int, month: int) -> tuple[DailyWeather, ...]:
            if html == "bad":
                raise JmaWeatherSourceError("boom")
            return _fake_parse(html, year, month)

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "24").mkdir(parents=True)
            (root / "24" / "202606.html").write_text("bad", encoding="utf-8")
            (root / "01").mkdir(parents=True)
            (root / "01" / "202606.html").write_text("ok", encoding="utf-8")
            ledger_path = root / "ledger.json"

            result = load_jma_archive.load_jma_archive(
                root,
                factory,
                start_date=dt.date(2026, 6, 1),
                end_date=dt.date(2026, 6, 30),
                ledger_path=ledger_path,
                parse=parse_or_raise,
                progress=None,
            )

            self.assertEqual(len(result.failed), 1)
            self.assertIn("24", result.failed[0][0])
            self.assertEqual(result.loaded_files, 1)
            with factory() as session:
                self.assertEqual(session.query(WeatherObservation).count(), 1)

    def test_spans_multiple_months_for_the_same_venue(self) -> None:
        engine, factory = _session_factory()
        self.addCleanup(engine.dispose)

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_raw_root(root, [("24", 2026, 6), ("24", 2026, 7)])
            ledger_path = root / "ledger.json"

            result = load_jma_archive.load_jma_archive(
                root,
                factory,
                start_date=dt.date(2026, 6, 1),
                end_date=dt.date(2026, 7, 31),
                ledger_path=ledger_path,
                parse=_fake_parse,
                progress=None,
            )

            self.assertEqual(result.loaded_files, 2)
            with factory() as session:
                self.assertEqual(session.query(WeatherObservation).count(), 2)


if __name__ == "__main__":
    unittest.main()
