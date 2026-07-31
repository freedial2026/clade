import tempfile
import unittest
from pathlib import Path

from boat_prediction.venue_data_source import (
    STADIUM_BASE_URL,
    VenueDataSourceError,
    fetch_all_venue_html,
    fetch_venue_html,
    parse_venue_html,
    stadium_url,
)

try:
    import bs4  # noqa: F401

    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

# A small hand-written excerpt mimicking the real page's structure
# (not real downloaded content, to avoid redistributing the official
# body's copyrighted data in this repository) -- same approach as
# tests/test_kfile_parser.py.
SAMPLE_VENUE_HTML = """
<html><body>
<div class="heading1"><h2><span class="heading1_mainLabel">桐　生ボートレース場</span></h2></div>
<div class="title7"><h4><span class="title7_mainLabel">コース別入着率＆決まり手</span></h4></div>
<div class="table1"><table>
<thead><tr><th>コース</th><th>1着</th><th>2着</th><th>3着</th><th>4着</th><th>5着</th><th>6着</th>
<th>逃げ</th><th>捲り</th><th>差し</th><th>捲り差し</th><th>抜き</th><th>恵まれ</th></tr></thead>
<tbody><tr class="is-p10-0"><td>1</td><td>51.9</td><td>16.5</td><td>10.0</td><td>8.3</td><td>7.5</td><td>5.6</td>
<td>97.3</td><td>0.0</td><td>0.0</td><td>0.0</td><td>2.3</td><td>0.2</td></tr></tbody>
<tbody><tr class="is-p10-0"><td>2</td><td>11.8</td><td>23.3</td><td>20.4</td><td>17.0</td><td>13.5</td><td>13.7</td>
<td>0.0</td><td>32.9</td><td>63.2</td><td>0.0</td><td>3.7</td><td>0.0</td></tr></tbody>
</table></div>
<div class="text"><p class="h-alignR">（集計期間：2026/04/01～2026/06/30　単位：％）</p></div>
<div class="title7"><h4><span class="title7_mainLabel">枠番別コース取得率</span></h4></div>
<div class="table1"><table>
<thead><tr><th>枠</th><th>1コース</th><th>2コース</th><th>3コース</th><th>4コース</th><th>5コース</th><th>6コース</th></tr></thead>
<tbody><tr class="is-p10-0"><td>1</td><td>96.9</td><td>2.2</td><td>0.3</td><td>0.4</td><td>0.0</td><td>0.0</td></tr></tbody>
</table></div>
<div class="text"><p class="h-alignR">（2026/06/30現在）</p></div>
<dl class="list1">
<dt>所在地</dt><dd>群馬県</dd>
<dt>モーター</dt><dd>減音</dd>
<dt>水質</dt><dd>淡水</dd>
<dt>干満差</dt><dd>なし</dd>
<dt>レコード</dt><dd>1.42.8 石田　章央 2004/10/27</dd>
</dl>
<div class="title7"><h4><span class="title7_mainLabel">春季</span><span class="title7_subLabel">のコース別入着率</span></h4></div>
<div class="table1"><table>
<thead><tr><th>コース</th><th>1着</th><th>2着</th><th>3着</th><th>4着</th><th>5着</th><th>6着</th></tr></thead>
<tbody><tr class="is-p10-0"><td>1</td><td>49.9</td><td>17.1</td><td>10.4</td><td>9.6</td><td>7.7</td><td>5.1</td></tr></tbody>
</table></div>
</body></html>
"""


class StadiumUrlTest(unittest.TestCase):
    def test_builds_the_confirmed_url_pattern(self) -> None:
        self.assertEqual(stadium_url("01"), f"{STADIUM_BASE_URL}?jcd=01")

    def test_rejects_an_unknown_venue_code(self) -> None:
        with self.assertRaises(VenueDataSourceError):
            stadium_url("99")


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
    def __init__(self, payload: bytes = b"<html></html>") -> None:
        self.requests: list[str] = []
        self._payload = payload

    def Request(self, url: str, headers: dict | None = None) -> str:
        return url

    def urlopen(self, request: str, timeout: float | None = None) -> FakeResponse:
        self.requests.append(request)
        return FakeResponse(self._payload)


class FetchVenueHtmlTest(unittest.TestCase):
    def test_fetches_the_expected_url(self) -> None:
        opener = FakeOpener(payload="桐生".encode("utf-8"))

        html = fetch_venue_html("01", opener=opener)

        self.assertEqual(html, "桐生")
        self.assertEqual(opener.requests, [f"{STADIUM_BASE_URL}?jcd=01"])

    def test_wraps_a_failed_request_in_venuedatasourceerror(self) -> None:
        class FailingOpener:
            def Request(self, url: str, headers: dict | None = None) -> str:
                return url

            def urlopen(self, request: str, timeout: float | None = None):
                raise OSError("connection refused")

        with self.assertRaises(VenueDataSourceError):
            fetch_venue_html("01", opener=FailingOpener())


class FetchAllVenueHtmlTest(unittest.TestCase):
    def test_fetches_all_24_venues_and_sleeps_between_requests(self) -> None:
        sleeps: list[float] = []
        opener = FakeOpener()

        with tempfile.TemporaryDirectory() as tmp:
            paths = fetch_all_venue_html(
                Path(tmp), delay_seconds=2.0, opener=opener, sleep=sleeps.append
            )

        self.assertEqual(len(paths), 24)
        self.assertEqual(paths["01"].name, "stadium_01.html")
        self.assertEqual(len(opener.requests), 24)
        self.assertEqual(sleeps, [2.0] * 23)

    def test_rejects_a_delay_under_one_second(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(VenueDataSourceError):
                fetch_all_venue_html(Path(tmp), delay_seconds=0.1, opener=FakeOpener())


@unittest.skipUnless(HAS_BS4, "beautifulsoup4 (the 'official-data' extra) is not installed")
class ParseVenueHtmlTest(unittest.TestCase):
    def test_parses_all_sections_of_the_sample_page(self) -> None:
        venue = parse_venue_html("01", SAMPLE_VENUE_HTML)

        self.assertEqual(venue.venue_name, "桐　生ボートレース場")
        self.assertEqual(venue.address, "群馬県")
        self.assertEqual(venue.water_quality, "淡水")
        self.assertEqual(venue.tidal_range, "なし")

        self.assertEqual(len(venue.course_kimarite), 2)
        row1 = venue.course_kimarite[0]
        self.assertEqual(row1.course, 1)
        self.assertEqual(row1.finish_rate, (51.9, 16.5, 10.0, 8.3, 7.5, 5.6))
        self.assertEqual(row1.kimarite["nige"], 97.3)
        self.assertEqual(venue.course_kimarite_period, "（集計期間：2026/04/01～2026/06/30　単位：％）")

        self.assertEqual(len(venue.lane_course_rate), 1)
        self.assertEqual(venue.lane_course_rate[0].course_rate, (96.9, 2.2, 0.3, 0.4, 0.0, 0.0))

        self.assertIn("spring", venue.seasonal_course_finish_rate)
        self.assertEqual(venue.seasonal_course_finish_rate["spring"][0].course, 1)

    def test_rejects_an_unknown_venue_code(self) -> None:
        with self.assertRaises(VenueDataSourceError):
            parse_venue_html("99", SAMPLE_VENUE_HTML)

    def test_raises_when_the_kimarite_section_is_missing(self) -> None:
        minimal_html = (
            '<div class="heading1"><h2>'
            '<span class="heading1_mainLabel">テストレース場</span></h2></div>'
        )
        with self.assertRaises(VenueDataSourceError):
            parse_venue_html("01", minimal_html)


if __name__ == "__main__":
    unittest.main()
