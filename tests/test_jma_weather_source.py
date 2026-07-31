import tempfile
import unittest
from pathlib import Path

from boat_prediction.jma_weather_source import (
    DAILY_BASE_URL,
    VENUE_STATIONS,
    JmaWeatherSourceError,
    daily_month_url,
    fetch_all,
    fetch_daily_month_html,
    parse_daily_month_html,
    station_type,
)

try:
    import bs4  # noqa: F401

    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

# Two-day excerpt mimicking the real table structure (not real downloaded
# content -- same reasoning as the other parser tests in this repo).
SAMPLE_DAILY_HTML = """
<html><body>
<table id="tablefix1" class="data2_s">
<tr class="mtx"><th>日</th><th>降水量</th></tr>
<tr class="mtx" style="text-align:right;">
<td style="white-space:nowrap"><div class="a_print"><a href="hourly_a1.php?day=1">1</a></div></td>
<td class=data_0_0>0.0</td><td class=data_0_0>0.0</td><td class=data_0_0>0.0</td>
<td class=data_0_0>26.3</td><td class=data_0_0>36.0</td><td class=data_0_0>15.9</td>
<td class=data_0_0>48</td><td class=data_0_0>13</td>
<td class=data_0_0>1.9</td><td class=data_0_0>5.0</td><td class=data_0_0 style="text-align:center">南東</td>
<td class=data_0_0>9.2</td><td class=data_0_0 style="text-align:center">南東</td>
<td class=data_0_0 style="text-align:center">南東</td>
<td class=data_0_0>12.9</td><td class=data_0_0>///</td><td class=data_0_0>///</td>
</tr>
<tr class="mtx" style="text-align:right;">
<td style="white-space:nowrap"><div class="a_print"><a href="hourly_a1.php?day=2">2</a></div></td>
<td class=data_0_0>2.0</td><td class=data_0_0>1.5</td><td class=data_0_0>0.5</td>
<td class=data_0_0>22.2</td><td class=data_0_0>25.5</td><td class=data_0_0>18.2</td>
<td class=data_0_0>69</td><td class=data_0_0>53</td>
<td class=data_0_0>1.5</td><td class=data_0_0>3.1</td><td class=data_0_0 style="text-align:center">東南東</td>
<td class=data_0_0>5.0</td><td class=data_0_0 style="text-align:center">南東</td>
<td class=data_0_0 style="text-align:center">東南東</td>
<td class=data_0_0>0.0</td><td class=data_0_0>///</td><td class=data_0_0>///</td>
</tr>
</table>
</body></html>
"""


class StationTypeTest(unittest.TestCase):
    def test_four_digit_block_no_is_amedas(self) -> None:
        self.assertEqual(station_type("0351"), "a")

    def test_five_digit_47_block_no_is_station_office(self) -> None:
        self.assertEqual(station_type("47651"), "s")


class VenueStationsTest(unittest.TestCase):
    def test_covers_all_24_venue_codes(self) -> None:
        from boat_prediction.race_id import VALID_VENUE_CODES

        self.assertEqual(set(VENUE_STATIONS), VALID_VENUE_CODES)


class DailyMonthUrlTest(unittest.TestCase):
    def test_builds_amedas_url_for_kiryu(self) -> None:
        self.assertEqual(
            daily_month_url("01", 2026, 6),
            f"{DAILY_BASE_URL}/daily_a1.php?prec_no=42&block_no=0351&year=2026&month=06&day=&view=",
        )

    def test_builds_station_office_url_for_tsu(self) -> None:
        self.assertEqual(
            daily_month_url("09", 2026, 6),
            f"{DAILY_BASE_URL}/daily_s1.php?prec_no=53&block_no=47651&year=2026&month=06&day=&view=",
        )

    def test_rejects_an_unknown_venue_code(self) -> None:
        with self.assertRaises(JmaWeatherSourceError):
            daily_month_url("99", 2026, 6)


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


class FetchDailyMonthHtmlTest(unittest.TestCase):
    def test_fetches_the_expected_url(self) -> None:
        opener = FakeOpener(payload="桐生".encode())

        html = fetch_daily_month_html("01", 2026, 6, opener=opener)

        self.assertEqual(html, "桐生")
        self.assertEqual(len(opener.requests), 1)
        self.assertIn("block_no=0351", opener.requests[0])

    def test_wraps_a_failed_request(self) -> None:
        class FailingOpener:
            def Request(self, url: str, headers: dict | None = None) -> str:
                return url

            def urlopen(self, request: str, timeout: float | None = None):
                raise OSError("connection refused")

        with self.assertRaises(JmaWeatherSourceError):
            fetch_daily_month_html("01", 2026, 6, opener=FailingOpener())


@unittest.skipUnless(HAS_BS4, "beautifulsoup4 (the 'official-data' extra) is not installed")
class ParseDailyMonthHtmlTest(unittest.TestCase):
    def test_parses_each_day_row_skipping_the_header(self) -> None:
        rows = parse_daily_month_html(SAMPLE_DAILY_HTML, 2026, 6)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].date_iso, "2026-06-01")
        self.assertEqual(rows[0].temperature_avg_c, 26.3)
        self.assertEqual(rows[0].temperature_max_c, 36.0)
        self.assertEqual(rows[0].wind_max_direction, "南東")
        self.assertEqual(rows[0].sunshine_hours, 12.9)

    def test_missing_value_marker_parses_as_none(self) -> None:
        html_with_missing_humidity = SAMPLE_DAILY_HTML.replace(
            "<td class=data_0_0>48</td><td class=data_0_0>13</td>",
            "<td class=data_0_0>///</td><td class=data_0_0>///</td>",
            1,
        )
        rows = parse_daily_month_html(html_with_missing_humidity, 2026, 6)
        self.assertIsNone(rows[0].humidity_avg_pct)
        self.assertIsNone(rows[0].humidity_min_pct)

    def test_raises_when_the_table_is_missing(self) -> None:
        with self.assertRaises(JmaWeatherSourceError):
            parse_daily_month_html("<html><body>no table here</body></html>", 2026, 6)


class FetchAllTest(unittest.TestCase):
    def test_skips_files_that_already_exist(self) -> None:
        opener = FakeOpener()
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            existing_dir = dest / "01"
            existing_dir.mkdir(parents=True)
            (existing_dir / "202606.html").write_text("cached", encoding="utf-8")

            paths = fetch_all(
                dest,
                start_year_month=(2026, 6),
                end_year_month=(2026, 6),
                opener=opener,
                sleep=lambda s: None,
            )

            # 24 venues, 1 month each; venue 01's file pre-existed and
            # should not have triggered a network call
            self.assertEqual(len(paths), 24)
            self.assertNotIn(
                daily_month_url("01", 2026, 6).replace(DAILY_BASE_URL, ""),
                "".join(opener.requests),
            )
            self.assertEqual((existing_dir / "202606.html").read_text(encoding="utf-8"), "cached")

    def test_rejects_a_delay_under_one_second(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(JmaWeatherSourceError):
                fetch_all(Path(tmp), delay_seconds=0.1, opener=FakeOpener())


if __name__ == "__main__":
    unittest.main()
