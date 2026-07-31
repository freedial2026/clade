"""Official data source: BOATRACE venue ("stadium") characteristic data.

Source: `https://www.boatrace.jp/owpc/pc/data/stadium?jcd={venue_code}`,
confirmed by following the "レース場データへ" links from
`https://www.boatrace.jp/owpc/pc/extra/data/stadium/index.html` (the
page the user pointed at with `?jcd=01` — that URL itself renders the
same 24-venue index regardless of the `jcd` query param; the per-venue
detail page lives at the separate `/owpc/pc/data/stadium` path found by
inspecting its outgoing links).

Unlike `official_source.py`'s K-files and `fan_file_source.py`'s fan
files (fixed, dated archive downloads), this is a live HTML page of
rolling statistics (e.g. "last 3 months", "spring season", recalculated
as time passes) rather than a discrete historical export. It is
scraped and parsed, not downloaded as an opaque file.

robots.txt on `www.boatrace.jp` has no disallow rules; the site policy
prohibits large-volume access and reproduction/redistribution beyond
private use. This module only fetches the 24 venue pages (venue codes
01-24, from `race_id.VALID_VENUE_CODES`), rate-limited between
requests, for local research use.

Requires the `official-data` extra (`beautifulsoup4`, for HTML table
parsing; no network calls of its own).
"""

from __future__ import annotations

import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .race_id import VALID_VENUE_CODES

STADIUM_BASE_URL = "https://www.boatrace.jp/owpc/pc/data/stadium"
_USER_AGENT = "boat-prediction-research/0.1 (non-commercial research project)"
DEFAULT_REQUEST_DELAY_SECONDS = 3.0

KIMARITE_KEYS = ("nige", "makuri", "sashi", "makuri_sashi", "nuki", "megumare")
_KIMARITE_LABELS = ("逃げ", "捲り", "差し", "捲り差し", "抜き", "恵まれ")


class VenueDataSourceError(ValueError):
    """Raised for invalid input or a failed fetch/parse."""


@dataclass(frozen=True)
class CourseFinishRates:
    course: int
    finish_rate: tuple[float, ...]  # index 0 = 1st place rate, ... index 5 = 6th place


@dataclass(frozen=True)
class CourseKimariteRow(CourseFinishRates):
    kimarite: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class LaneCourseRow:
    lane: int
    course_rate: tuple[float, ...]  # index 0 = course-1 acquisition rate, ... index 5 = course-6


@dataclass(frozen=True)
class VenueData:
    venue_code: str
    venue_name: str
    address: str | None
    motor: str | None
    water_quality: str | None
    tidal_range: str | None
    record: str | None
    course_kimarite: tuple[CourseKimariteRow, ...]
    course_kimarite_period: str | None
    lane_course_rate: tuple[LaneCourseRow, ...]
    lane_course_rate_period: str | None
    seasonal_course_finish_rate: dict[str, tuple[CourseFinishRates, ...]]


def stadium_url(venue_code: str) -> str:
    if venue_code not in VALID_VENUE_CODES:
        raise VenueDataSourceError(f"unknown venue_code: {venue_code!r}")
    return f"{STADIUM_BASE_URL}?jcd={venue_code}"


def fetch_venue_html(venue_code: str, *, opener: object | None = None) -> str:
    opener = opener or urllib.request
    url = stadium_url(venue_code)
    request = opener.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with opener.urlopen(request, timeout=15) as response:
            return response.read().decode("utf-8", errors="replace")
    except Exception as exc:
        raise VenueDataSourceError(f"failed to fetch {url}: {exc}") from exc


def fetch_all_venue_html(
    dest_dir: Path,
    *,
    delay_seconds: float = DEFAULT_REQUEST_DELAY_SECONDS,
    opener: object | None = None,
    sleep: object = time.sleep,
) -> dict[str, Path]:
    """Fetch all 24 venues' pages, waiting `delay_seconds` between
    requests, and save each as raw HTML to dest_dir."""
    if delay_seconds < 1.0:
        raise VenueDataSourceError(f"delay_seconds must be >= 1.0, got {delay_seconds!r}")

    dest_dir.mkdir(parents=True, exist_ok=True)
    codes = sorted(VALID_VENUE_CODES)
    paths: dict[str, Path] = {}
    for i, code in enumerate(codes):
        html = fetch_venue_html(code, opener=opener)
        path = dest_dir / f"stadium_{code}.html"
        path.write_text(html, encoding="utf-8")
        paths[code] = path
        if i < len(codes) - 1:
            sleep(delay_seconds)
    return paths


def _cell_texts(row) -> list[str]:
    return [cell.get_text(strip=True) for cell in row.find_all(["th", "td"])]


def _parse_period_note(table) -> str | None:
    node = table.find_next("p", class_="h-alignR")
    return node.get_text(strip=True) if node else None


def _parse_course_kimarite_table(table) -> tuple[CourseKimariteRow, ...]:
    rows = []
    for tr in table.find_all("tr", class_="is-p10-0"):
        cells = _cell_texts(tr)
        if len(cells) != 13:
            raise VenueDataSourceError(f"expected 13 cells in kimarite row, got {len(cells)}")
        course = int(cells[0])
        finish_rate = tuple(float(c) for c in cells[1:7])
        kimarite = dict(zip(KIMARITE_KEYS, (float(c) for c in cells[7:13])))
        rows.append(CourseKimariteRow(course=course, finish_rate=finish_rate, kimarite=kimarite))
    return tuple(rows)


def _parse_course_finish_rate_table(table) -> tuple[CourseFinishRates, ...]:
    rows = []
    for tr in table.find_all("tr", class_="is-p10-0"):
        cells = _cell_texts(tr)
        if len(cells) != 7:
            raise VenueDataSourceError(f"expected 7 cells in finish-rate row, got {len(cells)}")
        course = int(cells[0])
        finish_rate = tuple(float(c) for c in cells[1:7])
        rows.append(CourseFinishRates(course=course, finish_rate=finish_rate))
    return tuple(rows)


def _parse_lane_course_rate_table(table) -> tuple[LaneCourseRow, ...]:
    rows = []
    for tr in table.find_all("tr", class_="is-p10-0"):
        cells = _cell_texts(tr)
        if len(cells) != 7:
            raise VenueDataSourceError(f"expected 7 cells in lane-course row, got {len(cells)}")
        lane = int(cells[0])
        course_rate = tuple(float(c) for c in cells[1:7])
        rows.append(LaneCourseRow(lane=lane, course_rate=course_rate))
    return tuple(rows)


def parse_venue_html(venue_code: str, html: str) -> VenueData:
    """Parse one venue's fetched stadium page into structured data.
    Raises VenueDataSourceError if the expected sections are missing
    (the page layout is presumed stable but not contractually so)."""
    from bs4 import BeautifulSoup

    if venue_code not in VALID_VENUE_CODES:
        raise VenueDataSourceError(f"unknown venue_code: {venue_code!r}")

    soup = BeautifulSoup(html, "html.parser")

    name_node = soup.find("span", class_="heading1_mainLabel")
    if name_node is None:
        raise VenueDataSourceError(f"could not find venue name heading for {venue_code!r}")
    venue_name = name_node.get_text(strip=True)

    memo: dict[str, str] = {}
    memo_dl = soup.find("dl", class_="list1")
    if memo_dl is not None:
        dts = memo_dl.find_all("dt")
        dds = memo_dl.find_all("dd")
        memo = {dt.get_text(strip=True): dd.get_text(strip=True) for dt, dd in zip(dts, dds)}

    kimarite_heading = soup.find("span", string="コース別入着率＆決まり手")
    if kimarite_heading is None:
        raise VenueDataSourceError(f"could not find course/kimarite table for {venue_code!r}")
    kimarite_table = kimarite_heading.find_next("table")
    course_kimarite = _parse_course_kimarite_table(kimarite_table)
    course_kimarite_period = _parse_period_note(kimarite_table)

    lane_heading = soup.find("span", string="枠番別コース取得率")
    if lane_heading is None:
        raise VenueDataSourceError(f"could not find lane/course table for {venue_code!r}")
    lane_table = lane_heading.find_next("table")
    lane_course_rate = _parse_lane_course_rate_table(lane_table)
    lane_course_rate_period = _parse_period_note(lane_table)

    seasonal: dict[str, tuple[CourseFinishRates, ...]] = {}
    for label, key in (("春季", "spring"), ("夏季", "summer"), ("秋季", "autumn"), ("冬季", "winter")):
        season_heading = soup.find("span", class_="title7_mainLabel", string=label)
        if season_heading is None:
            continue
        season_table = season_heading.find_next("table")
        seasonal[key] = _parse_course_finish_rate_table(season_table)

    return VenueData(
        venue_code=venue_code,
        venue_name=venue_name,
        address=memo.get("所在地"),
        motor=memo.get("モーター"),
        water_quality=memo.get("水質"),
        tidal_range=memo.get("干満差"),
        record=memo.get("レコード"),
        course_kimarite=course_kimarite,
        course_kimarite_period=course_kimarite_period,
        lane_course_rate=lane_course_rate,
        lane_course_rate_period=lane_course_rate_period,
        seasonal_course_finish_rate=seasonal,
    )
