import tempfile
import unittest
from datetime import date
from pathlib import Path

from boat_prediction.b_file_source import (
    BFileSourceError,
    b_file_url,
    download_b_file,
    download_month,
)


class BFileUrlTest(unittest.TestCase):
    def test_matches_the_confirmed_url_pattern(self) -> None:
        self.assertEqual(
            b_file_url(date(2026, 6, 1)),
            "https://www1.mbrace.or.jp/od2/B/202606/b260601.lzh",
        )

    def test_pads_single_digit_month_and_day(self) -> None:
        self.assertEqual(
            b_file_url(date(2026, 1, 5)),
            "https://www1.mbrace.or.jp/od2/B/202601/b260105.lzh",
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
    def __init__(self, payload: bytes = b"fake-lzh-bytes") -> None:
        self.requests: list[str] = []
        self._payload = payload

    def Request(self, url: str, headers: dict | None = None) -> str:
        return url

    def urlopen(self, request: str, timeout: float | None = None) -> FakeResponse:
        self.requests.append(request)
        return FakeResponse(self._payload)


class DownloadBFileTest(unittest.TestCase):
    def test_writes_the_response_body_to_the_expected_filename(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest_dir = Path(tmp)
            opener = FakeOpener(payload=b"hello-lzh")

            path = download_b_file(date(2026, 6, 1), dest_dir, opener=opener)

            self.assertEqual(path.name, "b260601.lzh")
            self.assertEqual(path.read_bytes(), b"hello-lzh")
            self.assertEqual(opener.requests, ["https://www1.mbrace.or.jp/od2/B/202606/b260601.lzh"])

    def test_wraps_a_failed_request_in_bfilesourceerror(self) -> None:
        class FailingOpener:
            def Request(self, url: str, headers: dict | None = None) -> str:
                return url

            def urlopen(self, request: str, timeout: float | None = None):
                raise OSError("connection refused")

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(BFileSourceError):
                download_b_file(date(2026, 6, 1), Path(tmp), opener=FailingOpener())


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
        self.assertEqual(sleeps, [2.0] * 27)

    def test_rejects_a_delay_under_one_second(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(BFileSourceError):
                download_month(2026, 6, Path(tmp), delay_seconds=0.1, opener=FakeOpener())


if __name__ == "__main__":
    unittest.main()
