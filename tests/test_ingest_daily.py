"""Tests for `db.ingest_daily`. No network: the opener is a fake."""

from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from boat_prediction.db import ingest_daily
from boat_prediction.db.models import Base


def _session_factory():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection, _record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return sessionmaker(engine), engine


class FakeOpener:
    def __init__(self, payload: bytes = b"archive-bytes") -> None:
        self.payload = payload
        self.urls: list[str] = []

    def Request(self, url, headers=None):  # noqa: N802 - mirrors urllib
        self.urls.append(url)
        return url

    def urlopen(self, request, timeout=None):
        payload = self.payload

        class _Response:
            def read(self):
                return payload

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return _Response()


class DefaultDateForTest(unittest.TestCase):
    def test_card_is_today_in_jst(self) -> None:
        # 2026-06-01 23:00 UTC is already 2026-06-02 in JST; using UTC
        # here would ingest the wrong racing day.
        now = dt.datetime(2026, 6, 1, 23, 0, tzinfo=dt.UTC)

        self.assertEqual(
            ingest_daily.default_date_for(ingest_daily.KIND_CARD, now=now),
            dt.date(2026, 6, 2),
        )

    def test_results_is_yesterday_in_jst(self) -> None:
        now = dt.datetime(2026, 6, 1, 23, 0, tzinfo=dt.UTC)

        self.assertEqual(
            ingest_daily.default_date_for(ingest_daily.KIND_RESULTS, now=now),
            dt.date(2026, 6, 1),
        )


class DownloadForTest(unittest.TestCase):
    def test_writes_the_card_where_the_archive_loader_looks(self) -> None:
        opener = FakeOpener()
        with tempfile.TemporaryDirectory() as tmp:
            path = ingest_daily.download_for(
                ingest_daily.KIND_CARD, dt.date(2026, 6, 1), Path(tmp), opener=opener
            )

            self.assertEqual(path, Path(tmp) / "B" / "202606" / "b260601.lzh")
            self.assertTrue(path.is_file())
            self.assertEqual(
                opener.urls, ["https://www1.mbrace.or.jp/od2/B/202606/b260601.lzh"]
            )

    def test_writes_the_results_where_the_archive_loader_looks(self) -> None:
        opener = FakeOpener()
        with tempfile.TemporaryDirectory() as tmp:
            path = ingest_daily.download_for(
                ingest_daily.KIND_RESULTS, dt.date(2026, 6, 1), Path(tmp), opener=opener
            )

            self.assertEqual(path, Path(tmp) / "K" / "202606" / "k260601.lzh")
            self.assertEqual(
                opener.urls, ["https://www1.mbrace.or.jp/od2/K/202606/k260601.lzh"]
            )

    def test_rejects_an_unknown_kind(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ingest_daily.IngestDailyError):
                ingest_daily.download_for("weather", dt.date(2026, 6, 1), Path(tmp))


class IngestDayTest(unittest.TestCase):
    def test_downloads_then_loads_only_that_date(self) -> None:
        factory, engine = _session_factory()
        self.addCleanup(engine.dispose)
        opener = FakeOpener()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "raw"
            result = ingest_daily.ingest_day(
                ingest_daily.KIND_CARD,
                dt.date(2026, 6, 1),
                root,
                factory,
                ledger_path=Path(tmp) / "ledger.json",
                opener=opener,
                progress=None,
            )

            self.assertTrue((root / "B" / "202606" / "b260601.lzh").is_file())
            # The fake payload is not a real archive, so the load records
            # a per-file failure rather than raising -- what matters here
            # is that exactly one date was attempted.
            self.assertEqual(result.loaded_files + len(result.failed), 1)


if __name__ == "__main__":
    unittest.main()
