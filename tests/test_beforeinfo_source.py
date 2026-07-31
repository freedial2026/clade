"""Tests for boat_prediction.beforeinfo_source.

HTML fixtures are hand-written to mirror the real page's structure (not
real downloaded content -- same reasoning as tests/test_odds_source.py:
no official body's data is committed to this repository). The structure
they mirror was read off two real fetched pages, one completed
historical race and one same-day race whose exhibition run had not yet
happened -- see the module docstring for what each confirmed.
"""

from __future__ import annotations

import datetime as dt
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from boat_prediction.beforeinfo_source import (
    BeforeInfoSourceError,
    SurfaceWeather,
    beforeinfo_url,
    fetch_range,
    parse_beforeinfo,
    venues_from_odds_archive,
)


def _boat_tbody(
    lane: int,
    toban: int,
    name: str,
    weight: str = "52.0kg",
    exhibition: str = "6.88",
    tilt: str = "-0.5",
    propeller: str = "",
    parts: str = "",
    adjustment: str = "0.0",
) -> str:
    parts_html = f'<li>{parts}</li>' if parts else ""
    return f"""
<tbody class="is-fs12">
 <tr>
  <td class="is-boatColor{lane} is-fs14" rowspan="4">{lane}</td>
  <td rowspan="4"><a href="/owpc/pc/data/racersearch/profile?toban={toban}"><img/></a></td>
  <td class="is-fs18 is-fBold" rowspan="4">
    <a href="/owpc/pc/data/racersearch/profile?toban={toban}">{name}</a></td>
  <td rowspan="2">{weight}</td>
  <td rowspan="4">{exhibition}</td>
  <td rowspan="4">{tilt}</td>
  <td rowspan="4">{propeller}</td>
  <td class="is-p5-5" rowspan="4"><ul class="labelGroup1">{parts_html}</ul></td>
  <td>R</td><td></td>
 </tr>
 <tr><td>進入</td><td></td></tr>
 <tr><td rowspan="2">{adjustment}</td><td>ST</td><td></td></tr>
 <tr><td>着順</td><td></td></tr>
</tbody>"""


def _start_row(lane: int, timing: str) -> str:
    return f"""
 <tr><td colspan="3"><div class="table1_boatImage1">
   <span class="table1_boatImage1Number is-type{lane}">{lane}</span>
   <span class="table1_boatImage1Time">{timing}</span>
 </div></td></tr>"""


def _weather_block(
    label: str = "5R時点",
    air: str = "29.0℃",
    water: str = "29.0℃",
    wind: str = "2m",
    wind_dir_class: str = "is-wind10",
    wave: str = "3cm",
    weather_text: str = "晴",
) -> str:
    return f"""
<div class="weather1">
 <p class="weather1_title">水面気象情報　{label}</p>
 <div class="weather1_body">
  <div class="weather1_bodyUnit is-direction">
   <div class="weather1_bodyUnitLabel">
    <span class="weather1_bodyUnitLabelTitle">気温</span>
    <span class="weather1_bodyUnitLabelData">{air}</span></div></div>
  <div class="weather1_bodyUnit is-weather">
   <p class="weather1_bodyUnitImage is-weather1"></p>
   <div class="weather1_bodyUnitLabel">
    <span class="weather1_bodyUnitLabelTitle">{weather_text}</span></div></div>
  <div class="weather1_bodyUnit is-wind">
   <div class="weather1_bodyUnitLabel">
    <span class="weather1_bodyUnitLabelTitle">風速</span>
    <span class="weather1_bodyUnitLabelData">{wind}</span></div></div>
  <div class="weather1_bodyUnit is-windDirection">
   <p class="weather1_bodyUnitImage {wind_dir_class}"></p></div>
  <div class="weather1_bodyUnit is-waterTemperature">
   <div class="weather1_bodyUnitLabel">
    <span class="weather1_bodyUnitLabelTitle">水温</span>
    <span class="weather1_bodyUnitLabelData">{water}</span></div></div>
  <div class="weather1_bodyUnit is-wave">
   <div class="weather1_bodyUnitLabel">
    <span class="weather1_bodyUnitLabelTitle">波高</span>
    <span class="weather1_bodyUnitLabelData">{wave}</span></div></div>
 </div>
</div>"""


def _page(
    boats: str | None = None,
    start_rows: str | None = None,
    weather: str | None = None,
) -> str:
    if boats is None:
        boats = _boat_tbody(1, 4665, "加藤啓太") + _boat_tbody(2, 5045, "平川香織")
    if start_rows is None:
        start_rows = _start_row(1, ".11") + _start_row(2, ".12")
    if weather is None:
        weather = _weather_block()
    return f"""<html><body>
{weather}
<table class="is-w748">{boats}</table>
<table class="is-w238">
 <thead><tr><th colspan="3">スタート展示</th></tr>
        <tr class="is-thColor1"><th>コース</th><th>並び</th><th>ST</th></tr></thead>
 <tbody class="is-p10-0">{start_rows}</tbody>
</table>
</body></html>"""


class BeforeInfoUrlTest(unittest.TestCase):
    def test_builds_the_confirmed_url_pattern(self) -> None:
        url = beforeinfo_url(dt.date(2025, 7, 31), "04", 1)

        self.assertEqual(
            url,
            "https://www.boatrace.jp/owpc/pc/race/beforeinfo?rno=1&jcd=04&hd=20250731",
        )

    def test_rejects_an_unknown_venue_code(self) -> None:
        with self.assertRaises(BeforeInfoSourceError):
            beforeinfo_url(dt.date(2025, 7, 31), "99", 1)

    def test_rejects_a_race_number_out_of_range(self) -> None:
        with self.assertRaises(BeforeInfoSourceError):
            beforeinfo_url(dt.date(2025, 7, 31), "04", 13)


class ParseBoatsTest(unittest.TestCase):
    def test_parses_every_boat_row(self) -> None:
        info = parse_beforeinfo(_page())

        self.assertEqual([b.lane_number for b in info.boats], [1, 2])

    def test_parses_identity_and_exhibition_fields(self) -> None:
        info = parse_beforeinfo(_page())
        boat = info.boats[0]

        self.assertEqual(boat.racer_registration_number, 4665)
        self.assertEqual(boat.racer_name, "加藤啓太")
        self.assertEqual(boat.weight_kg, 52.0)
        self.assertEqual(boat.exhibition_time_sec, 6.88)
        self.assertEqual(boat.tilt_angle, -0.5)

    def test_adjustment_weight_comes_from_the_third_row_not_the_first(self) -> None:
        # 調整重量 is visually stacked under 体重 in the same column, so
        # it is the third `<tr>`'s first cell, not another first-row cell.
        info = parse_beforeinfo(
            _page(boats=_boat_tbody(1, 4665, "加藤啓太", weight="52.0kg", adjustment="2.5"))
        )

        self.assertEqual(info.boats[0].weight_kg, 52.0)
        self.assertEqual(info.boats[0].adjustment_weight_kg, 2.5)

    def test_parts_replacement_labels_are_captured(self) -> None:
        info = parse_beforeinfo(_page(boats=_boat_tbody(3, 4240, "今井裕梨", parts="リング×４")))

        self.assertEqual(info.boats[0].parts_replaced, ("リング×４",))

    def test_no_parts_replacement_is_an_empty_tuple(self) -> None:
        info = parse_beforeinfo(_page())

        self.assertEqual(info.boats[0].parts_replaced, ())

    def test_blank_exhibition_time_is_none_before_the_exhibition_run(self) -> None:
        info = parse_beforeinfo(
            _page(boats=_boat_tbody(1, 4987, "島倉都", exhibition="", tilt=""))
        )

        self.assertIsNone(info.boats[0].exhibition_time_sec)
        self.assertIsNone(info.boats[0].tilt_angle)
        self.assertFalse(info.has_exhibition_data)

    def test_has_exhibition_data_is_true_once_times_are_published(self) -> None:
        info = parse_beforeinfo(_page())

        self.assertTrue(info.has_exhibition_data)


class ParseStartExhibitionTest(unittest.TestCase):
    def test_course_numbers_start_at_one_ignoring_header_rows(self) -> None:
        info = parse_beforeinfo(_page())

        self.assertEqual([e.course_number for e in info.start_exhibition], [1, 2])

    def test_course_order_differing_from_lane_order_is_preserved(self) -> None:
        # 進入変更: the boat in lane 3 took the innermost course.
        rows = _start_row(3, ".11") + _start_row(1, ".12")

        info = parse_beforeinfo(_page(start_rows=rows))

        self.assertEqual(info.start_exhibition[0].course_number, 1)
        self.assertEqual(info.start_exhibition[0].lane_number, 3)
        self.assertEqual(info.start_exhibition[1].lane_number, 1)

    def test_start_timing_leading_dot_is_parsed_as_a_fraction(self) -> None:
        info = parse_beforeinfo(_page())

        self.assertEqual(info.start_exhibition[0].start_timing_sec, 0.11)
        self.assertFalse(info.start_exhibition[0].is_flying)

    def test_flying_start_is_flagged_not_dropped(self) -> None:
        info = parse_beforeinfo(_page(start_rows=_start_row(1, "F.01")))

        self.assertEqual(info.start_exhibition[0].start_timing_sec, 0.01)
        self.assertTrue(info.start_exhibition[0].is_flying)


class ParseWeatherTest(unittest.TestCase):
    def test_parses_every_surface_measurement(self) -> None:
        info = parse_beforeinfo(_page())
        weather = info.weather

        self.assertEqual(weather.air_temperature_c, 29.0)
        self.assertEqual(weather.water_temperature_c, 29.0)
        self.assertEqual(weather.wind_speed_ms, 2.0)
        self.assertEqual(weather.wave_height_cm, 3.0)
        self.assertEqual(weather.weather_text, "晴")
        self.assertEqual(weather.wind_direction_code, 10)

    def test_reference_race_form_is_parsed_into_a_race_number(self) -> None:
        info = parse_beforeinfo(_page(weather=_weather_block(label="5R時点")))

        self.assertEqual(info.weather.reference_race_number, 5)

    def test_clock_time_form_has_no_reference_race_number(self) -> None:
        info = parse_beforeinfo(_page(weather=_weather_block(label="17:43現在")))

        self.assertIsNone(info.weather.reference_race_number)
        self.assertEqual(info.weather.raw_label, "17:43現在")


class WeatherLeakageGateTest(unittest.TestCase):
    """The rule this project's leakage safety rests on for this source --
    see beforeinfo_source.py's module docstring for the real-page
    evidence behind it."""

    def _weather(self, label: str, reference: int | None) -> SurfaceWeather:
        return SurfaceWeather(
            raw_label=label,
            reference_race_number=reference,
            air_temperature_c=None,
            water_temperature_c=None,
            wind_speed_ms=None,
            wind_direction_code=None,
            wave_height_cm=None,
            weather_text=None,
            weather_icon_code=None,
        )

    def test_earlier_reference_race_is_safe(self) -> None:
        weather = self._weather("5R時点", 5)

        self.assertTrue(weather.is_safe_for_race(6))
        self.assertTrue(weather.is_safe_for_race(12))

    def test_same_or_later_reference_race_is_not_safe(self) -> None:
        weather = self._weather("5R時点", 5)

        self.assertFalse(weather.is_safe_for_race(5))
        self.assertFalse(weather.is_safe_for_race(4))

    def test_clock_time_form_is_never_safe_for_an_archival_fetch(self) -> None:
        # Race 1 gets this form. Fetched from the archive it is the
        # day's *latest* reading -- confirmed on a real page where race
        # 1 (deadline 11:53) reported 17:43現在.
        weather = self._weather("17:43現在", None)

        self.assertFalse(weather.is_safe_for_race(1))
        self.assertFalse(weather.is_safe_for_race(12))


class ParseEmptyPageTest(unittest.TestCase):
    def test_a_page_with_no_race_yields_empty_results_rather_than_raising(self) -> None:
        info = parse_beforeinfo("<html><body><p>no race</p></body></html>")

        self.assertEqual(info.boats, ())
        self.assertEqual(info.start_exhibition, ())
        self.assertIsNone(info.weather)
        self.assertFalse(info.has_exhibition_data)


class FetchRangeTest(unittest.TestCase):
    def test_rejects_too_short_a_delay(self) -> None:
        with TemporaryDirectory() as tmp, self.assertRaises(BeforeInfoSourceError):
            fetch_range(
                dt.date(2025, 7, 31),
                dt.date(2025, 7, 31),
                Path(tmp),
                venues_for_date=lambda _d, opener=None: ("04",),
                delay_seconds=0.1,
            )

    def test_rejects_end_date_before_start_date(self) -> None:
        with TemporaryDirectory() as tmp, self.assertRaises(BeforeInfoSourceError):
            fetch_range(
                dt.date(2025, 8, 1),
                dt.date(2025, 7, 31),
                Path(tmp),
                venues_for_date=lambda _d, opener=None: ("04",),
            )


class VenuesFromOddsArchiveTest(unittest.TestCase):
    def test_reads_the_venue_list_the_odds_fetch_already_wrote(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            day = root / "20250801"
            day.mkdir()
            (day / "_venues.txt").write_text("04\n24\n12", encoding="utf-8")

            lookup = venues_from_odds_archive(root)

            self.assertEqual(lookup(dt.date(2025, 8, 1)), ("04", "24", "12"))

    def test_a_day_the_odds_archive_does_not_cover_yields_no_venues(self) -> None:
        # Lets a range wider than the odds archive skip uncovered days
        # rather than fail -- see the function's docstring.
        with TemporaryDirectory() as tmp:
            lookup = venues_from_odds_archive(Path(tmp))

            self.assertEqual(lookup(dt.date(2025, 8, 1)), ())


class FetchRangeVenueLookupTest(unittest.TestCase):
    def test_skips_days_with_no_venues_without_making_requests(self) -> None:
        def exploding_opener(*_args, **_kwargs):  # pragma: no cover - must not run
            raise AssertionError("no request should be made for an uncovered day")

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            written = fetch_range(
                dt.date(2025, 8, 1),
                dt.date(2025, 8, 3),
                root / "dest",
                venues_for_date=lambda _d, opener=None: (),
                opener=exploding_opener,
                sleep=lambda _s: None,
                log=lambda _m: None,
            )

            self.assertEqual(written, 0)


if __name__ == "__main__":
    unittest.main()
