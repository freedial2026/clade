"""Tests for boat_prediction.db.load_archive.

Uses a fake `extract_text` (see module docstring on `load_archive`'s
`extract_text` parameter) so no real `.lzh` archive needs to exist on
disk -- only a placeholder file at the expected path, to exercise file
discovery, the ledger, and error recovery. Parses real B-file/K-file
sample text borrowed from test_bfile_parser.py/test_kfile_parser.py so
the loader path underneath is exercised end to end, not just mocked out.
"""

from __future__ import annotations

import datetime as dt
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import create_engine, event
from test_bfile_parser import SAMPLE_B_FILE_TEXT
from test_kfile_parser import SAMPLE_TEXT

from boat_prediction.db import load_archive
from boat_prediction.db.models import Base, Race, RaceEntry, RaceResult
from boat_prediction.db.session import create_session_factory


def _make_raw_root(root: Path, dates_with_kind: list[tuple[dt.date, str]]) -> None:
    """Create empty placeholder files at the archive paths `load_archive`
    expects, for the given (date, "B"|"K") pairs."""
    for target_date, kind in dates_with_kind:
        subdir = "B" if kind == "B" else "K"
        prefix = "b" if kind == "B" else "k"
        directory = root / subdir / target_date.strftime("%Y%m")
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{prefix}{target_date:%y%m%d}.lzh").write_bytes(b"placeholder")


def _fake_extract(path: Path) -> str:
    if path.name.startswith("b"):
        return SAMPLE_B_FILE_TEXT
    return SAMPLE_TEXT


def _session_factory():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection, _record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine, create_session_factory(engine)


class LoadArchiveTest(unittest.TestCase):
    def test_raises_on_missing_raw_root(self) -> None:
        engine, factory = _session_factory()
        self.addCleanup(engine.dispose)

        with self.assertRaises(load_archive.ArchiveLoadError):
            load_archive.load_archive(
                Path("does-not-exist"), factory, extract_text=_fake_extract, progress=None
            )

    def test_raises_when_end_date_before_start_date(self) -> None:
        engine, factory = _session_factory()
        self.addCleanup(engine.dispose)

        with TemporaryDirectory() as tmp, self.assertRaises(load_archive.ArchiveLoadError):
            load_archive.load_archive(
                Path(tmp),
                factory,
                start_date=dt.date(2026, 6, 2),
                end_date=dt.date(2026, 6, 1),
                extract_text=_fake_extract,
                progress=None,
            )

    def test_loads_present_files_and_counts_missing_dates(self) -> None:
        engine, factory = _session_factory()
        self.addCleanup(engine.dispose)

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_raw_root(root, [(dt.date(2026, 6, 1), "B"), (dt.date(2026, 6, 1), "K")])
            ledger_path = root / "ledger.json"

            result = load_archive.load_archive(
                root,
                factory,
                start_date=dt.date(2026, 6, 1),
                end_date=dt.date(2026, 6, 2),
                ledger_path=ledger_path,
                extract_text=_fake_extract,
                progress=None,
            )

            self.assertEqual(result.loaded_files, 2)
            # 2026-06-02 has neither file -> 2 misses; 2026-06-01's B/K
            # files are both present -> 0 misses that day.
            self.assertEqual(result.skipped_missing, 2)
            self.assertEqual(result.failed, [])
            with factory() as session:
                self.assertGreater(session.query(Race).count(), 0)
                self.assertGreater(session.query(RaceEntry).count(), 0)
                self.assertGreater(session.query(RaceResult).count(), 0)
            self.assertTrue(ledger_path.is_file())

    def test_second_run_skips_files_already_recorded_in_the_ledger(self) -> None:
        engine, factory = _session_factory()
        self.addCleanup(engine.dispose)

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_raw_root(root, [(dt.date(2026, 6, 1), "B"), (dt.date(2026, 6, 1), "K")])
            ledger_path = root / "ledger.json"
            kwargs = {
                "start_date": dt.date(2026, 6, 1),
                "end_date": dt.date(2026, 6, 1),
                "ledger_path": ledger_path,
                "extract_text": _fake_extract,
                "progress": None,
            }

            first = load_archive.load_archive(root, factory, **kwargs)
            second = load_archive.load_archive(root, factory, **kwargs)

            self.assertEqual(first.loaded_files, 2)
            self.assertEqual(second.loaded_files, 0)
            self.assertEqual(second.skipped_already_loaded, 2)
            # Idempotent DB writes, not just a ledger skip: even if the
            # ledger were wrong, re-loading must not duplicate rows.
            # (4, not 2: the B/K sample fixtures use disjoint venues/race
            # numbers -- venues 24/08 from the B-file, 01/02 from the
            # K-file -- so both files' races add up rather than overlap.)
            with factory() as session:
                self.assertEqual(session.query(Race).count(), 4)

    def test_force_reloads_files_already_recorded_in_the_ledger(self) -> None:
        engine, factory = _session_factory()
        self.addCleanup(engine.dispose)

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_raw_root(root, [(dt.date(2026, 6, 1), "K")])
            ledger_path = root / "ledger.json"
            kwargs = {
                "start_date": dt.date(2026, 6, 1),
                "end_date": dt.date(2026, 6, 1),
                "ledger_path": ledger_path,
                "extract_text": _fake_extract,
                "progress": None,
            }

            load_archive.load_archive(root, factory, **kwargs)
            forced = load_archive.load_archive(root, factory, force=True, **kwargs)

            self.assertEqual(forced.loaded_files, 1)
            self.assertEqual(forced.skipped_already_loaded, 0)

    def test_dry_run_does_not_write_to_the_database_or_ledger(self) -> None:
        engine, factory = _session_factory()
        self.addCleanup(engine.dispose)

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_raw_root(root, [(dt.date(2026, 6, 1), "K")])
            ledger_path = root / "ledger.json"

            result = load_archive.load_archive(
                root,
                factory,
                start_date=dt.date(2026, 6, 1),
                end_date=dt.date(2026, 6, 1),
                ledger_path=ledger_path,
                dry_run=True,
                extract_text=_fake_extract,
                progress=None,
            )

            self.assertEqual(result.loaded_files, 1)
            self.assertFalse(ledger_path.is_file())
            with factory() as session:
                self.assertEqual(session.query(Race).count(), 0)

    def test_a_second_date_still_finds_reference_data_after_dry_run_rollback(self) -> None:
        # Regression: rolling back a dry-run file must not also roll
        # back the venues/data_sources seeded before the loop -- see
        # load_archive's comment on why ensure_reference_data is
        # committed unconditionally before the per-file rollback.
        engine, factory = _session_factory()
        self.addCleanup(engine.dispose)

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_raw_root(
                root, [(dt.date(2026, 6, 1), "K"), (dt.date(2026, 6, 2), "K")]
            )
            ledger_path = root / "ledger.json"

            result = load_archive.load_archive(
                root,
                factory,
                start_date=dt.date(2026, 6, 1),
                end_date=dt.date(2026, 6, 2),
                ledger_path=ledger_path,
                dry_run=True,
                extract_text=_fake_extract,
                progress=None,
            )

            self.assertEqual(result.loaded_files, 2)
            self.assertEqual(result.failed, [])

    def test_a_file_that_fails_to_parse_is_recorded_and_does_not_stop_the_run(self) -> None:
        engine, factory = _session_factory()
        self.addCleanup(engine.dispose)

        def extract_or_blank(path: Path) -> str:
            if path.parent.name == "202606":
                return ""  # empty text -> parse error for either file kind
            return _fake_extract(path)

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_raw_root(
                root, [(dt.date(2026, 6, 1), "K"), (dt.date(2026, 7, 1), "K")]
            )
            ledger_path = root / "ledger.json"

            result = load_archive.load_archive(
                root,
                factory,
                start_date=dt.date(2026, 6, 1),
                end_date=dt.date(2026, 7, 1),
                ledger_path=ledger_path,
                extract_text=extract_or_blank,
                progress=None,
            )

            self.assertEqual(len(result.failed), 1)
            self.assertIn("202606", result.failed[0][0])
            # The later good file still loaded despite the earlier failure.
            self.assertEqual(result.loaded_files, 1)
            with factory() as session:
                self.assertEqual(session.query(Race).count(), 2)


if __name__ == "__main__":
    unittest.main()
