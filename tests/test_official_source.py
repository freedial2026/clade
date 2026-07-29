import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from boat_prediction.official_source import (
    OfficialSourceError,
    download_k_file,
    download_month,
    extract_k_file_text,
    k_file_url,
)

try:
    import pylhasa  # noqa: F401

    HAS_PYLHASA = True
except ImportError:
    HAS_PYLHASA = False


class KFileUrlTest(unittest.TestCase):
    def test_matches_the_confirmed_url_pattern(self) -> None:
        self.assertEqual(
            k_file_url(date(2026, 6, 1)),
            "https://www1.mbrace.or.jp/od2/K/202606/k260601.lzh",
        )

    def test_pads_single_digit_month_and_day(self) -> None:
        self.assertEqual(
            k_file_url(date(2026, 1, 5)),
            "https://www1.mbrace.or.jp/od2/K/202601/k260105.lzh",
        )


class FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class FakeOpener:
    """Stands in for urllib.request without making a real network call."""

    def __init__(self, payload: bytes = b"fake-lzh-bytes") -> None:
        self.requests: list[str] = []
        self._payload = payload

    def Request(self, url: str, headers: dict | None = None) -> str:
        return url

    def urlopen(self, request: str, timeout: float | None = None) -> FakeResponse:
        self.requests.append(request)
        return FakeResponse(self._payload)


class DownloadKFileTest(unittest.TestCase):
    def test_writes_the_response_body_to_the_expected_filename(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest_dir = Path(tmp)
            opener = FakeOpener(payload=b"hello-lzh")

            path = download_k_file(date(2026, 6, 1), dest_dir, opener=opener)

            self.assertEqual(path.name, "k260601.lzh")
            self.assertEqual(path.read_bytes(), b"hello-lzh")
            self.assertEqual(opener.requests, ["https://www1.mbrace.or.jp/od2/K/202606/k260601.lzh"])

    def test_wraps_a_failed_request_in_officialsourceerror(self) -> None:
        class FailingOpener:
            def Request(self, url: str, headers: dict | None = None) -> str:
                return url

            def urlopen(self, request: str, timeout: float | None = None):
                raise OSError("connection refused")

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(OfficialSourceError):
                download_k_file(date(2026, 6, 1), Path(tmp), opener=FailingOpener())


class DownloadMonthTest(unittest.TestCase):
    def test_downloads_one_file_per_day_and_sleeps_between_requests(self) -> None:
        sleeps: list[float] = []
        opener = FakeOpener()

        with tempfile.TemporaryDirectory() as tmp:
            paths = download_month(
                2026, 2, Path(tmp), delay_seconds=2.0, opener=opener, sleep=sleeps.append
            )

        self.assertEqual(len(paths), 28)  # Feb 2026 is not a leap year
        self.assertEqual(len(opener.requests), 28)
        # sleeps between requests, not after the last one
        self.assertEqual(sleeps, [2.0] * 27)

    def test_rejects_a_delay_under_one_second(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(OfficialSourceError):
                download_month(2026, 6, Path(tmp), delay_seconds=0.1, opener=FakeOpener())


class ExtractKFileTextTest(unittest.TestCase):
    def test_rejects_a_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "does-not-exist.lzh"
            with self.assertRaises(OfficialSourceError):
                extract_k_file_text(missing)

    @unittest.skipUnless(HAS_PYLHASA, "pylhasa (the 'official-data' extra) is not installed")
    def test_decodes_the_single_archive_member_as_shift_jis(self) -> None:
        fake_entry = MagicMock()
        fake_entry.is_dir = False
        fake_entry.read.return_value = "大村".encode("shift_jis")

        with tempfile.TemporaryDirectory() as tmp:
            lzh_path = Path(tmp) / "k260601.lzh"
            lzh_path.write_bytes(b"not a real archive, pylhasa.open is mocked below")

            with patch("pylhasa.open", return_value=[fake_entry]):
                text = extract_k_file_text(lzh_path)

        self.assertEqual(text, "大村")

    @unittest.skipUnless(HAS_PYLHASA, "pylhasa (the 'official-data' extra) is not installed")
    def test_rejects_an_archive_with_more_than_one_member(self) -> None:
        entry_a, entry_b = MagicMock(is_dir=False), MagicMock(is_dir=False)

        with tempfile.TemporaryDirectory() as tmp:
            lzh_path = Path(tmp) / "k260601.lzh"
            lzh_path.write_bytes(b"placeholder")

            with patch("pylhasa.open", return_value=[entry_a, entry_b]):
                with self.assertRaises(OfficialSourceError):
                    extract_k_file_text(lzh_path)


if __name__ == "__main__":
    unittest.main()
