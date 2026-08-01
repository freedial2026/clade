"""Backfill 直前情報 from the unofficial Boatrace Open API.

Source: `https://boatraceopenapi.github.io/previews/v3/{YYYY}/{YYYYMMDD}.json`,
a community project (MIT, GitHub Pages) that publishes the official
site's 直前情報 as JSON. It is **not** official, disclaims accuracy, and
its repository is marked deprecated in favour of a successor — so nothing
here trusts it on its word.

**Cross-validated against the official page before being used at all**
(2026-07-30, 5 races, 150 boat-level values): 147 matched exactly. The
three that did not were all `start_timing`, and all differed only in
sign — the API writes a flying start as a negative number where
`beforeinfo_source` keeps the magnitude and a separate `is_flying` flag.
Identical information, different convention, converted below.

Why this exists when `beforeinfo_source` already fetches the same pages:
scope and courtesy. The API covers **2023-05-01 onward**, roughly 170,000
races, and serving it costs boatrace.jp nothing. Scraping that range
directly would be ~500,000 requests against a site whose terms prohibit
large-volume access. The live capture keeps using the official page,
where it belongs — the API updates only every few hours and could never
serve a pre-deadline decision.

**Two things the API does not carry, both of which matter:**

1. **The weather observation label.** The official page states whether a
   reading is `"NR時点"` (observed at race N — safe for later races) or
   `"HH:MM現在"` (a wall clock, which fetched after the fact is the day's
   *last* reading). The API gives the numbers alone. Confirmed on a real
   page: 平和島 1R carried `'17:48現在'`, minutes after the final race of
   the day, while the API presented the same values with nothing to mark
   them. So weather from this source is adapted with
   `reference_race_number=None`, which makes
   `SurfaceWeather.is_safe_for_race()` refuse it for every race. It is
   still stored rather than discarded — the values may become datable
   later — but it cannot be used as a feature by accident.

2. **Parts replacement and propeller changes.** Absent entirely. The
   adapter reports this through `parts_known=False` rather than writing
   `False`, which would assert an absence that was never observed.

**The feed is not shape-stable.** On most dates a race's `boats` is a
list; on others it is a dict keyed by lane number as a string
(`{"1": {...}, ..., "6": {...}}`), with identical inner records. A first
backfill run failed on 112 of 1,188 days for exactly this, so both shapes
are accepted below. That is the practical cost of a third-party mirror
and the reason the run reports failures per day rather than stopping.
"""

from __future__ import annotations

import datetime as dt
import json
import urllib.request
from dataclasses import dataclass

from .beforeinfo_source import (
    BoatBeforeInfo,
    RaceBeforeInfo,
    StartExhibitionEntry,
    SurfaceWeather,
)
from .race_id import VALID_VENUE_CODES

PREVIEWS_URL = "https://boatraceopenapi.github.io/previews/v3/{year}/{stamp}.json"
EARLIEST_DATE = dt.date(2023, 5, 1)
"""First date the v3 previews feed serves. Probed, not taken on trust:
2023-05-01 returns 200 and 2024-08-01 on the v2 path returns 404."""

_USER_AGENT = "boat-prediction-research/0.1 (non-commercial research project)"

OPENAPI_WEATHER_LABEL = "openapi:unlabelled"
"""Recorded in place of the page's own label, so a row from this source is
distinguishable from one the live capture read off the official page."""


class BoatraceOpenApiError(ValueError):
    """Raised for an unusable response or an out-of-range date."""


@dataclass(frozen=True)
class PreviewRace:
    """One race from the feed, adapted to `beforeinfo_source`'s types so
    the existing loader consumes both sources unchanged."""

    race_date: dt.date
    venue_code: str
    race_number: int
    info: RaceBeforeInfo


def previews_url(target_date: dt.date) -> str:
    if target_date < EARLIEST_DATE:
        raise BoatraceOpenApiError(
            f"{target_date} precedes the feed's earliest date {EARLIEST_DATE}"
        )
    return PREVIEWS_URL.format(year=target_date.year, stamp=target_date.strftime("%Y%m%d"))


def fetch_previews_json(target_date: dt.date, *, opener: object | None = None) -> dict:
    opener = opener or urllib.request
    request = opener.Request(previews_url(target_date), headers={"User-Agent": _USER_AGENT})
    try:
        with opener.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise BoatraceOpenApiError(f"failed to fetch previews for {target_date}: {exc}") from exc


def _start_timing(value) -> tuple[float | None, bool]:
    """Convert the API's signed start timing to this project's convention.

    The API writes a flying start as a negative number; `beforeinfo_source`
    keeps the magnitude and flags it. This was the only disagreement found
    when the two sources were compared on real races, and it is a
    convention rather than a discrepancy.
    """
    if value is None:
        return None, False
    number = float(value)
    return abs(number), number < 0


def _boat_entries(race: dict):
    """The race's boat records, whichever shape the feed used.

    Most dates give a list; some give a dict keyed by lane number as a
    string. Iterating the dict directly yields its keys, which is how a
    first backfill lost 112 days to `string indices must be integers`.
    """
    boats = race.get("boats") or ()
    if isinstance(boats, dict):
        return list(boats.values())
    return list(boats)


def _boat(entry: dict) -> tuple[BoatBeforeInfo, StartExhibitionEntry]:
    lane = int(entry["racer_boat_number"])
    timing, is_flying = _start_timing(entry.get("racer_start_timing"))
    boat = BoatBeforeInfo(
        lane_number=lane,
        racer_registration_number=None,
        racer_name="",
        weight_kg=entry.get("racer_weight"),
        adjustment_weight_kg=entry.get("racer_weight_adjustment"),
        exhibition_time_sec=entry.get("racer_exhibition_time"),
        tilt_angle=entry.get("racer_tilt_adjustment"),
        # Not carried by this source. `parts_known=False` on the loader is
        # what keeps these from being written as an observed absence.
        propeller_changed=False,
        parts_replaced=(),
    )
    start = StartExhibitionEntry(
        course_number=int(entry.get("racer_course_number") or lane),
        lane_number=lane,
        start_timing_sec=timing,
        is_flying=is_flying,
    )
    return boat, start


def _weather(race: dict) -> SurfaceWeather:
    return SurfaceWeather(
        raw_label=OPENAPI_WEATHER_LABEL,
        # None on purpose: without the page's label this reading cannot be
        # placed in time, so `is_safe_for_race` must refuse it.
        reference_race_number=None,
        air_temperature_c=race.get("air_temperature"),
        water_temperature_c=race.get("water_temperature"),
        wind_speed_ms=race.get("wind_speed"),
        wind_direction_code=race.get("wind_direction_number"),
        wave_height_cm=race.get("wave_height"),
        weather_text=None,
        weather_icon_code=race.get("weather_number"),
    )


def parse_previews(payload: dict, *, race_date: dt.date | None = None) -> list[PreviewRace]:
    """Adapt one day's feed into `PreviewRace` records.

    A race whose venue code is not one of the 24 known ones is skipped
    rather than raising: this is a third-party feed and one malformed
    entry must not cost the day.
    """
    races = payload.get("previews")
    if races is None:
        raise BoatraceOpenApiError("response has no 'previews' key")

    out: list[PreviewRace] = []
    for race in races:
        venue_code = f"{int(race['stadium_number']):02d}"
        if venue_code not in VALID_VENUE_CODES:
            continue
        stated = race.get("date")
        day = dt.date.fromisoformat(stated) if stated else race_date
        if day is None:
            raise BoatraceOpenApiError("race has no date and none was supplied")

        boats, starts = [], []
        for entry in _boat_entries(race):
            boat, start = _boat(entry)
            boats.append(boat)
            starts.append(start)

        out.append(
            PreviewRace(
                race_date=day,
                venue_code=venue_code,
                race_number=int(race["number"]),
                info=RaceBeforeInfo(
                    boats=tuple(boats),
                    start_exhibition=tuple(starts),
                    weather=_weather(race),
                ),
            )
        )
    return out


def fetch_day(target_date: dt.date, *, opener: object | None = None) -> list[PreviewRace]:
    return parse_previews(
        fetch_previews_json(target_date, opener=opener), race_date=target_date
    )
