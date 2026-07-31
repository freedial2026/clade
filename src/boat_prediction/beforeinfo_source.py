"""Official data source: BOATRACE pre-race information (直前情報).

Source: `https://www.boatrace.jp/owpc/pc/race/beforeinfo?rno={R}&jcd={venue}&hd={YYYYMMDD}`

This is the exhibition-run and water-surface data that
`db/models.py`'s `ExhibitionEntry` docstring and `tasks/HANDOFF.md`
previously recorded as "not archived anywhere officially, so it would
need to be captured live going forward". **That is wrong**: these pages
do serve past dates. Verified by fetching 2025-07-31 (a year back) and
2026-07-30 for venue 04 and getting fully populated exhibition times,
tilt angles, parts-replacement labels, start-exhibition courses/STs and
surface weather. The earlier conclusion appears to have come from
probing a venue on a date it did not race, which returns an empty page
shell indistinguishable from "no retention". The true retention
boundary is not yet established -- probe it before assuming any
particular start date (`odds_source.EARLIEST_RETAINED_DATE` is 2017-04-01
for the odds pages, which may or may not apply here).

This matters because these are the only genuinely pre-race measurements
this project has access to: exhibition time and tilt are set during the
pre-race exhibition run, before betting closes, so unlike the K-file
exhibition values (which this project only learns about from the
post-race results file, and therefore stamps with results-time
availability) they can legitimately feed a pre-deadline prediction.

## Leakage trap in the weather block -- read before using it

The surface-weather block carries its own observation label, and the
label takes two different forms:

- `"5R時点"` -- observed at race 5. For race 6+ this is genuinely
  *earlier* information and is safe to use.
- `"17:43現在"` -- a wall-clock time with no race reference. Race 1 gets
  this form, because there is no previous race to reference.

Fetched live before race 1, that clock time is current and safe.
Fetched from the archive afterwards, **it is the day's latest
observation** -- confirmed by fetching venue 04 on 2025-07-31, where
race 1 (deadline 11:53) reported `17:43現在`, minutes after the final
race of the day closed at 17:40. Attributing that to race 1 would feed
a model six hours of future weather.

`parse_beforeinfo` therefore reports the label faithfully rather than
normalizing it away, and `SurfaceWeather.is_safe_for_race` implements
the rule: only the `"NR時点"` form, with `N < race_number`, is
provably pre-race. Archival callers must treat the clock-time form as
unusable; a live caller that fetched the page itself before the
deadline knows its own fetch time and may override.

Per-boat exhibition values carry no such caveat -- they were confirmed
to differ per race across races 1/6/12 of the same venue-day, so they
are race-specific rather than a shared latest value.

robots.txt on `www.boatrace.jp` has no disallow rules.
`https://www.boatrace.jp/owpc/pc/extra/policy.html` prohibits
"large-volume access/transmission that interferes with site operation"
and redistribution beyond private use, so like `odds_source.py` this
module rate-limits (default 3s) and does not parallelize or retry
aggressively. Fetched pages are not redistributed in this repository.

Requires the `official-data` extra (`beautifulsoup4`).
"""

from __future__ import annotations

import re
import time
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .race_id import VALID_VENUE_CODES

BEFOREINFO_URL = (
    "https://www.boatrace.jp/owpc/pc/race/beforeinfo?rno={rno}&jcd={jcd}&hd={hd}"
)
_USER_AGENT = "boat-prediction-research/0.1 (non-commercial research project)"
DEFAULT_REQUEST_DELAY_SECONDS = 3.0

_TOBAN_RE = re.compile(r"toban=(\d+)")
_REFERENCE_RACE_RE = re.compile(r"^(\d{1,2})R時点$")
_CLOCK_LABEL_RE = re.compile(r"^\d{1,2}:\d{2}現在$")
_IS_TYPE_RE = re.compile(r"^is-type(\d)$")
_IS_WIND_RE = re.compile(r"^is-wind(\d+)$")
_WEATHER_TITLE_PREFIX = "水面気象情報"


class BeforeInfoSourceError(ValueError):
    """Raised for invalid input or a failed fetch/parse."""


@dataclass(frozen=True)
class BoatBeforeInfo:
    """One boat's pre-race exhibition measurements.

    `adjustment_weight_kg` is 調整重量 -- ballast added so every boat
    meets the minimum combined weight. It is set at pre-race inspection
    and is present on the page even before the exhibition run, unlike
    `exhibition_time_sec`/`tilt_angle`.
    """

    lane_number: int
    racer_registration_number: int | None
    racer_name: str
    weight_kg: float | None
    adjustment_weight_kg: float | None
    exhibition_time_sec: float | None
    tilt_angle: float | None
    propeller_changed: bool
    parts_replaced: tuple[str, ...]


@dataclass(frozen=True)
class StartExhibitionEntry:
    """One position in the start-exhibition (スタート展示) line-up.

    `course_number` is the entry course actually taken in the exhibition
    (1 = innermost), and `lane_number` is which boat took it. These
    differ whenever boats swap courses (進入変更), which is exactly what
    `entry_course.py` (P3-T001) models -- and the exhibition is the only
    pre-race observation of it.
    """

    course_number: int
    lane_number: int
    start_timing_sec: float | None
    is_flying: bool


@dataclass(frozen=True)
class SurfaceWeather:
    """Water-surface conditions, with the page's own observation label.

    See the module docstring: `raw_label` is preserved verbatim because
    which *form* it takes determines whether the reading is usable at
    all. `wind_direction_code`/`weather_icon_code` are the site's own
    numeric icon codes, kept raw rather than mapped to compass points or
    condition names, since no mapping has been verified.
    """

    raw_label: str
    reference_race_number: int | None
    air_temperature_c: float | None
    water_temperature_c: float | None
    wind_speed_ms: float | None
    wind_direction_code: int | None
    wave_height_cm: float | None
    weather_text: str | None
    weather_icon_code: int | None

    def is_safe_for_race(self, race_number: int) -> bool:
        """True only when this reading is *provably* from before
        `race_number`, i.e. the `"NR時点"` form with `N < race_number`.

        The wall-clock form returns False: an archival fetch cannot tell
        it apart from the day's final reading. A live caller that
        fetched the page itself before the deadline knows its own fetch
        time and can disregard this.
        """
        return self.reference_race_number is not None and (
            self.reference_race_number < race_number
        )


@dataclass(frozen=True)
class RaceBeforeInfo:
    boats: tuple[BoatBeforeInfo, ...]
    start_exhibition: tuple[StartExhibitionEntry, ...]
    weather: SurfaceWeather | None

    @property
    def has_exhibition_data(self) -> bool:
        """False before the exhibition run has happened (the page exists
        from early in the day, with the boat list filled in but every
        exhibition time blank)."""
        return any(boat.exhibition_time_sec is not None for boat in self.boats)


def beforeinfo_url(target_date: date, venue_code: str, race_number: int) -> str:
    if venue_code not in VALID_VENUE_CODES:
        raise BeforeInfoSourceError(f"unknown venue_code: {venue_code!r}")
    if not 1 <= race_number <= 12:
        raise BeforeInfoSourceError(f"race_number out of range 1-12: {race_number!r}")
    return BEFOREINFO_URL.format(
        rno=race_number, jcd=venue_code, hd=target_date.strftime("%Y%m%d")
    )


def fetch_beforeinfo_html(
    target_date: date, venue_code: str, race_number: int, *, opener: object | None = None
) -> str:
    opener = opener or urllib.request
    url = beforeinfo_url(target_date, venue_code, race_number)
    request = opener.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with opener.urlopen(request, timeout=20) as response:
            return response.read().decode("utf-8", errors="replace")
    except Exception as exc:
        raise BeforeInfoSourceError(f"failed to fetch {url}: {exc}") from exc


def _number(text: str, *, strip_suffix: str = "") -> float | None:
    text = text.strip()
    if strip_suffix and text.endswith(strip_suffix):
        text = text[: -len(strip_suffix)]
    text = text.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_start_timing(text: str) -> tuple[float | None, bool]:
    """`'.11'` -> `(0.11, False)`; `'F.01'` -> `(0.01, True)`; blank ->
    `(None, False)`. A flying start is recorded as such rather than
    dropped, matching `kfile_parser`'s handling of F results."""
    text = text.strip()
    if not text:
        return None, False
    is_flying = text.startswith("F")
    if is_flying:
        text = text[1:].strip()
    if text.startswith("."):
        text = "0" + text
    try:
        return float(text), is_flying
    except ValueError:
        return None, is_flying


def _parse_weather(soup: object) -> SurfaceWeather | None:
    body = soup.find("div", class_="weather1_body")
    if body is None:
        return None

    title_node = soup.find("p", class_="weather1_title")
    raw_label = ""
    if title_node is not None:
        text = title_node.get_text(" ", strip=True)
        raw_label = text.replace(_WEATHER_TITLE_PREFIX, "").strip()
        # The site separates title from label with an ideographic space.
        raw_label = raw_label.replace("　", " ").strip()

    reference_race_number = None
    match = _REFERENCE_RACE_RE.match(raw_label)
    if match:
        reference_race_number = int(match.group(1))

    def unit_value(modifier: str) -> str | None:
        unit = body.find("div", class_=lambda c: c and modifier in c)
        if unit is None:
            return None
        data = unit.find("span", class_="weather1_bodyUnitLabelData")
        return data.get_text(strip=True) if data is not None else None

    def unit_icon_code(modifier: str, pattern: re.Pattern[str]) -> int | None:
        unit = body.find("div", class_=lambda c: c and modifier in c)
        if unit is None:
            return None
        image = unit.find("p", class_="weather1_bodyUnitImage")
        if image is None:
            return None
        for name in image.get("class", []):
            found = pattern.match(name)
            if found:
                return int(found.group(1))
        return None

    weather_unit = body.find("div", class_=lambda c: c and "is-weather" in c)
    weather_text = None
    if weather_unit is not None:
        title = weather_unit.find("span", class_="weather1_bodyUnitLabelTitle")
        weather_text = title.get_text(strip=True) if title is not None else None

    return SurfaceWeather(
        raw_label=raw_label,
        reference_race_number=reference_race_number,
        air_temperature_c=_number(unit_value("is-direction") or "", strip_suffix="℃"),
        water_temperature_c=_number(unit_value("is-waterTemperature") or "", strip_suffix="℃"),
        wind_speed_ms=_number(unit_value("is-wind") or "", strip_suffix="m"),
        wind_direction_code=unit_icon_code("is-windDirection", _IS_WIND_RE),
        wave_height_cm=_number(unit_value("is-wave") or "", strip_suffix="cm"),
        weather_text=weather_text,
        weather_icon_code=unit_icon_code("is-weather", re.compile(r"^is-weather(\d+)$")),
    )


def _parse_boats(table: object) -> tuple[BoatBeforeInfo, ...]:
    """One `<tbody>` per boat, each holding four `<tr>`.

    Cells are taken positionally *within their own row* rather than from
    the flattened whole-tbody list: the first row carries the
    `rowspan`-ed identity/exhibition cells, and the third row's first
    cell is 調整重量 (visually stacked under 体重 in the same column).
    """
    boats = []
    for tbody in table.find_all("tbody"):
        rows = tbody.find_all("tr")
        if len(rows) < 3:
            continue
        head = rows[0].find_all("td")
        if len(head) < 8:
            continue

        lane_text = head[0].get_text(strip=True)
        if not lane_text.isdigit():
            continue

        toban = None
        link = head[1].find("a") or head[2].find("a")
        if link is not None:
            found = _TOBAN_RE.search(link.get("href", ""))
            if found:
                toban = int(found.group(1))

        parts = tuple(
            item.get_text(" ", strip=True)
            for item in head[7].find_all("li")
            if item.get_text(strip=True)
        )
        if not parts:
            text = head[7].get_text(" ", strip=True)
            parts = (text,) if text else ()

        adjustment_cells = rows[2].find_all("td")
        adjustment = _number(adjustment_cells[0].get_text(strip=True)) if adjustment_cells else None

        boats.append(
            BoatBeforeInfo(
                lane_number=int(lane_text),
                racer_registration_number=toban,
                racer_name=head[2].get_text(strip=True),
                weight_kg=_number(head[3].get_text(strip=True), strip_suffix="kg"),
                adjustment_weight_kg=adjustment,
                exhibition_time_sec=_number(head[4].get_text(strip=True)),
                tilt_angle=_number(head[5].get_text(strip=True)),
                propeller_changed=bool(head[6].get_text(strip=True)),
                parts_replaced=parts,
            )
        )
    return tuple(boats)


def _parse_start_exhibition(table: object) -> tuple[StartExhibitionEntry, ...]:
    """Row order is course order: the first row is course 1 (innermost).
    The boat occupying that course is the number rendered in the row, so
    a 進入変更 shows up as course order != lane order."""
    # Only rows that actually carry a boat, enumerated after filtering:
    # the table's `<thead>` contributes two header rows, so counting
    # every `<tr>` would offset every course number by two.
    boat_rows = []
    for row in table.find_all("tr"):
        number_node = row.find("span", class_="table1_boatImage1Number")
        if number_node is None:
            continue
        if number_node.get_text(strip=True).isdigit():
            boat_rows.append((row, number_node))

    entries = []
    for course_number, (row, number_node) in enumerate(boat_rows, start=1):
        lane_text = number_node.get_text(strip=True)
        time_node = row.find("span", class_="table1_boatImage1Time")
        timing, is_flying = _parse_start_timing(
            time_node.get_text(strip=True) if time_node is not None else ""
        )
        entries.append(
            StartExhibitionEntry(
                course_number=course_number,
                lane_number=int(lane_text),
                start_timing_sec=timing,
                is_flying=is_flying,
            )
        )
    return tuple(entries)


def parse_beforeinfo(html: str) -> RaceBeforeInfo:
    """Parse a fetched 直前情報 page.

    Returns empty `boats`/`start_exhibition` and `weather=None` when the
    page carries no race (a venue that did not race that day renders the
    shell only) -- the same "empty, not an error" convention as
    `odds_source.parse_win_place_odds`, so a caller walking a date range
    can distinguish "no race" from a genuine parse failure.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")

    boats: tuple[BoatBeforeInfo, ...] = ()
    for table in tables:
        classes = table.get("class") or []
        if "is-w748" in classes:
            boats = _parse_boats(table)
            break

    start_exhibition: tuple[StartExhibitionEntry, ...] = ()
    for table in tables:
        classes = table.get("class") or []
        if "is-w238" in classes:
            start_exhibition = _parse_start_exhibition(table)
            break

    return RaceBeforeInfo(
        boats=boats,
        start_exhibition=start_exhibition,
        weather=_parse_weather(soup),
    )


def fetch_range(
    start_date: date,
    end_date: date,
    dest_dir: Path,
    *,
    venues_for_date: object,
    delay_seconds: float = DEFAULT_REQUEST_DELAY_SECONDS,
    opener: object | None = None,
    sleep: object = time.sleep,
    log: object = print,
) -> int:
    """Fetch 直前情報 pages for every racing venue/race in the date range,
    saving raw HTML to `dest_dir/{YYYYMMDD}/{venue}_{race}.html` -- the
    same layout `odds_source.fetch_range` writes, so the two archives sit
    side by side and share a loader shape.

    `venues_for_date` is injected (normally
    `odds_source.fetch_racing_venues`) rather than imported, so this
    module has no dependency on the odds source and tests can supply a
    fake without any network access.

    Idempotent: existing files are skipped, so a run resumes after
    interruption. Returns the number of pages newly written.
    """
    if delay_seconds < 1.0:
        raise BeforeInfoSourceError(f"delay_seconds must be >= 1.0, got {delay_seconds!r}")
    if end_date < start_date:
        raise BeforeInfoSourceError(f"end_date {end_date} precedes start_date {start_date}")

    written = 0
    current = start_date
    while current <= end_date:
        day_dir = dest_dir / current.strftime("%Y%m%d")
        venues_marker = day_dir / "_venues.txt"

        if venues_marker.exists():
            venues = tuple(v for v in venues_marker.read_text(encoding="utf-8").split() if v)
        else:
            venues = tuple(venues_for_date(current, opener=opener))
            sleep(delay_seconds)
            day_dir.mkdir(parents=True, exist_ok=True)
            venues_marker.write_text("\n".join(venues), encoding="utf-8")

        for venue_code in venues:
            for race_number in range(1, 13):
                dest_path = day_dir / f"{venue_code}_{race_number:02d}.html"
                if dest_path.exists():
                    continue
                html = fetch_beforeinfo_html(
                    current, venue_code, race_number, opener=opener
                )
                dest_path.write_text(html, encoding="utf-8")
                written += 1
                sleep(delay_seconds)

        log(f"{current.isoformat()}: {len(venues)} venues, {written} pages written so far")
        current = date.fromordinal(current.toordinal() + 1)
    return written
