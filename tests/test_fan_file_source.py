import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from boat_prediction.fan_file_source import (
    FAN_FILE_BASE_URL,
    FAN_INDEX_URL,
    FanFileSourceError,
    download_all,
    download_fan_file,
    extract_fan_file_text,
    list_fan_file_urls,
)

try:
    import pylhasa  # noqa: F401

    HAS_PYLHASA = True
except ImportError:
    HAS_PYLHASA = False

SAMPLE_INDEX_HTML = """
<li> <span>2026年</span>
  <span><a href="/static_extra/pc_static/download/data/kibetsu/fan2510.lzh">前期</a></span>
  <span><a href="/static_extra/pc_static/download/data/kibetsu/fan2604.lzh">後期</a></span>
</li>
<li> <span>2025年</span>
  <span><a href="/static_extra/pc_static/download/data/kibetsu/fan2410.lzh">前期</a></span>
  <span><a href="/static_extra/pc_static/download/data/kibetsu/fan2504.lzh">後期</a></span>
</li>
"""


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
        payload = (
            SAMPLE_INDEX_HTML.encode("utf-8") if request == FAN_INDEX_URL else self._payload
        )
        return FakeResponse(payload)


class ListFanFileUrlsTest(unittest.TestCase):
    def test_extracts_every_linked_fan_file_in_order_without_duplicates(self) -> None:
        opener = FakeOpener()

        urls = list_fan_file_urls(opener=opener)

        self.assertEqual(
            urls,
            [
                f"{FAN_FILE_BASE_URL}/fan2510.lzh",
                f"{FAN_FILE_BASE_URL}/fan2604.lzh",
                f"{FAN_FILE_BASE_URL}/fan2410.lzh",
                f"{FAN_FILE_BASE_URL}/fan2504.lzh",
            ],
        )

    def test_raises_when_the_index_page_has_no_recognizable_links(self) -> None:
        class EmptyIndexOpener(FakeOpener):
            def urlopen(self, request: str, timeout: float | None = None) -> FakeResponse:
                self.requests.append(request)
                return FakeResponse(b"<html>nothing here</html>")

        with self.assertRaises(FanFileSourceError):
            list_fan_file_urls(opener=EmptyIndexOpener())

    def test_wraps_a_failed_index_fetch_in_fanfilesourceerror(self) -> None:
        class FailingOpener:
            def Request(self, url: str, headers: dict | None = None) -> str:
                return url

            def urlopen(self, request: str, timeout: float | None = None):
                raise OSError("connection refused")

        with self.assertRaises(FanFileSourceError):
            list_fan_file_urls(opener=FailingOpener())


class DownloadFanFileTest(unittest.TestCase):
    def test_writes_the_response_body_using_the_url_basename(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest_dir = Path(tmp)
            opener = FakeOpener(payload=b"hello-lzh")

            path = download_fan_file(f"{FAN_FILE_BASE_URL}/fan2510.lzh", dest_dir, opener=opener)

            self.assertEqual(path.name, "fan2510.lzh")
            self.assertEqual(path.read_bytes(), b"hello-lzh")

    def test_wraps_a_failed_request_in_fanfilesourceerror(self) -> None:
        class FailingOpener:
            def Request(self, url: str, headers: dict | None = None) -> str:
                return url

            def urlopen(self, request: str, timeout: float | None = None):
                raise OSError("connection refused")

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FanFileSourceError):
                download_fan_file(
                    f"{FAN_FILE_BASE_URL}/fan2510.lzh", Path(tmp), opener=FailingOpener()
                )


class DownloadAllTest(unittest.TestCase):
    def test_downloads_every_discovered_file_and_sleeps_between_requests(self) -> None:
        sleeps: list[float] = []
        opener = FakeOpener()

        with tempfile.TemporaryDirectory() as tmp:
            paths = download_all(
                Path(tmp), delay_seconds=2.0, opener=opener, sleep=sleeps.append
            )

        self.assertEqual([p.name for p in paths], ["fan2510.lzh", "fan2604.lzh", "fan2410.lzh", "fan2504.lzh"])
        # one request for the index page + one per file
        self.assertEqual(len(opener.requests), 5)
        self.assertEqual(sleeps, [2.0] * 3)

    def test_rejects_a_delay_under_one_second(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FanFileSourceError):
                download_all(Path(tmp), delay_seconds=0.1, opener=FakeOpener())


class ExtractFanFileTextTest(unittest.TestCase):
    def test_rejects_a_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "does-not-exist.lzh"
            with self.assertRaises(FanFileSourceError):
                extract_fan_file_text(missing)

    @unittest.skipUnless(HAS_PYLHASA, "pylhasa (the 'official-data' extra) is not installed")
    def test_decodes_and_concatenates_all_archive_members_as_shift_jis(self) -> None:
        entry_a = MagicMock(is_dir=False)
        entry_a.read.return_value = "大村".encode("shift_jis")
        entry_b = MagicMock(is_dir=False)
        entry_b.read.return_value = "住之江".encode("shift_jis")

        with tempfile.TemporaryDirectory() as tmp:
            lzh_path = Path(tmp) / "fan2510.lzh"
            lzh_path.write_bytes(b"not a real archive, pylhasa.open is mocked below")

            with patch("pylhasa.open", return_value=[entry_a, entry_b]):
                text = extract_fan_file_text(lzh_path)

        self.assertEqual(text, "大村住之江")

    @unittest.skipUnless(HAS_PYLHASA, "pylhasa (the 'official-data' extra) is not installed")
    def test_rejects_an_archive_with_no_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lzh_path = Path(tmp) / "fan2510.lzh"
            lzh_path.write_bytes(b"placeholder")

            with patch("pylhasa.open", return_value=[]):
                with self.assertRaises(FanFileSourceError):
                    extract_fan_file_text(lzh_path)


if __name__ == "__main__":
    unittest.main()
