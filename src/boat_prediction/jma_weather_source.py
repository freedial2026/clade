"""Supplementary data source: JMA (Japan Meteorological Agency) historical
weather, mapped to each BOATRACE venue's nearest observation station.

Source: `https://www.data.jma.go.jp/stats/etrn/` ("過去の気象データ検索").
Confirmed by manual navigation (prefecture map -> station list -> daily
value table) that this is a public, government-operated data source
covering the full `race_id.VALID_VENUE_CODES` history range (spot-
checked back to 2005-01-03). Licensed under the "公共データ利用規約
（第1.0版）" (Japan's standard open-government-data terms): reuse
including commercial use is allowed with attribution (e.g. "出典：気象
庁ホームページ"); the 気象業務法 restrictions found (第17条 forecast-
business licensing, 第23条 warning reuse) do not apply to using past
observations as model features. No `robots.txt` exists on this host.

**Scope and limitations found during research**: this source covers air
temperature, precipitation, wind speed/direction, sunshine, and (at some
stations, and only from whenever that station added the sensor) humidity
and dew point. It does **not** cover water temperature or wave height at
a venue's own racecourse -- BOATRACE's own pre-race "直前情報" is the
only source for those, and it is not archived anywhere officially, so it
would need to be captured live going forward. Tide predictions for
coastal venues exist separately at `data.jma.go.jp/kaiyou/db/tide/`, but
only from 2011 onward.

Each JMA observation point is one of two kinds, distinguished by its
`block_no` and requiring a different view-script prefix:
- AMeDAS ("a"): 4-digit `block_no` (e.g. `0351`) -> `daily_a1.php`
- Weather station office ("s"): 5-digit `block_no` starting `47`
  (e.g. `47651`) -> `daily_s1.php` (a superset of elements)

`VENUE_STATIONS` below is a one-time manual mapping (not automatically
discovered, unlike `fan_file_source.py`'s index-scrape approach) built by
matching each venue's city/ward (from `venue_data_source.py`'s already-
fetched `address`/`venue_name`) against the station list for its
prefecture. Most map to an exact same-place station (e.g. venue 24
大村 -> AMeDAS "大村"); a few use the nearest neighboring station where
no station shares the venue's exact place name (noted per entry).
"""

from __future__ import annotations

import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .race_id import VALID_VENUE_CODES

DAILY_BASE_URL = "https://www.data.jma.go.jp/stats/etrn/view"
_USER_AGENT = "boat-prediction-research/0.1 (non-commercial research project)"
DEFAULT_REQUEST_DELAY_SECONDS = 1.5

# venue_code -> (prec_no, block_no, station_name, note)
VENUE_STATIONS: dict[str, tuple[str, str, str, str]] = {
    "01": ("42", "0351", "桐生", "exact match"),
    "02": ("43", "0363", "さいたま", "nearest to Toda; no station named Toda"),
    "03": ("44", "0370", "江戸川臨海", "exact match (Edogawa Rinkai)"),
    "04": ("44", "0371", "羽田", "nearest to Heiwajima (Ota ward)"),
    "05": ("44", "1133", "府中", "nearest to Tamagawa venue"),
    "06": ("50", "0988", "三ヶ日", "nearest to Lake Hamana (Kosai city)"),
    "07": ("51", "1344", "蒲郡", "exact match"),
    "08": ("51", "1555", "セントレア", "nearest to Tokoname (Centrair airport)"),
    "09": ("53", "47651", "津", "exact match"),
    "10": ("57", "1071", "三国", "exact match"),
    "11": ("60", "0586", "大津", "nearest to Biwako (Otsu city)"),
    "12": ("62", "47772", "大阪", "nearest to Suminoe (Osaka city)"),
    "13": ("63", "1588", "西宮", "nearest to Amagasaki"),
    "14": ("71", "47895", "徳島", "nearest to Naruto; no station named Naruto"),
    "15": ("72", "47890", "多度津", "nearest to Marugame"),
    "16": ("66", "0670", "玉野", "nearest to Kojima (Kurashiki city)"),
    "17": ("67", "1326", "廿日市津田", "exact match (Hatsukaichi Tsuda)"),
    "18": ("81", "0776", "下松", "nearest to Tokuyama (Shunan city)"),
    "19": ("81", "47762", "下関", "exact match"),
    "20": ("82", "0780", "八幡", "nearest to Wakamatsu (Kitakyushu)"),
    "21": ("82", "1527", "曽根", "nearest to Ashiya-machi, Fukuoka"),
    "22": ("82", "47807", "福岡", "exact match"),
    "23": ("85", "1610", "唐津", "exact match"),
    "24": ("84", "1084", "大村", "exact match"),
}

assert set(VENUE_STATIONS) == VALID_VENUE_CODES


class JmaWeatherSourceError(ValueError):
    """Raised for invalid input or a failed fetch/parse."""


@dataclass(frozen=True)
class DailyWeather:
    date_iso: str
    precipitation_total_mm: float | None
    precipitation_max_1h_mm: float | None
    precipitation_max_10min_mm: float | None
    temperature_avg_c: float | None
    temperature_max_c: float | None
    temperature_min_c: float | None
    humidity_avg_pct: float | None
    humidity_min_pct: float | None
    wind_avg_ms: float | None
    wind_max_ms: float | None
    wind_max_direction: str | None
    wind_max_instant_ms: float | None
    wind_max_instant_direction: str | None
    wind_prevailing_direction: str | None
    sunshine_hours: float | None


def station_type(block_no: str) -> str:
    return "s" if len(block_no) == 5 and block_no.startswith("47") else "a"


def daily_month_url(venue_code: str, year: int, month: int) -> str:
    if venue_code not in VENUE_STATIONS:
        raise JmaWeatherSourceError(f"unknown venue_code: {venue_code!r}")
    prec_no, block_no, _name, _note = VENUE_STATIONS[venue_code]
    kind = station_type(block_no)
    return (
        f"{DAILY_BASE_URL}/daily_{kind}1.php?prec_no={prec_no}&block_no={block_no}"
        f"&year={year}&month={month:02d}&day=&view="
    )


def fetch_daily_month_html(
    venue_code: str, year: int, month: int, *, opener: object | None = None
) -> str:
    opener = opener or urllib.request
    url = daily_month_url(venue_code, year, month)
    request = opener.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with opener.urlopen(request, timeout=15) as response:
            return response.read().decode("utf-8", errors="replace")
    except Exception as exc:
        raise JmaWeatherSourceError(f"failed to fetch {url}: {exc}") from exc


def _cell_value(text: str) -> float | None:
    text = text.strip()
    if text in ("", "///", "--", ")"):
        return None
    # some values carry a trailing ")" annotation (e.g. estimated); strip it
    text = text.rstrip(")").strip()
    try:
        return float(text)
    except ValueError:
        return None


def _cell_text(text: str) -> str | None:
    text = text.strip()
    return text if text and text not in ("///", "--") else None


def parse_daily_month_html(html: str, year: int, month: int) -> tuple[DailyWeather, ...]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id="tablefix1")
    if table is None:
        raise JmaWeatherSourceError("could not find the daily-values table (tablefix1)")

    rows = []
    for tr in table.find_all("tr"):
        first_td = tr.find("td")
        if first_td is None:
            continue
        day_link = first_td.find("a")
        if day_link is None or not day_link.get_text(strip=True).isdigit():
            continue

        day = int(day_link.get_text(strip=True))
        cells = tr.find_all("td")
        values = [c.get_text(strip=True) for c in cells[1:]]
        if len(values) != 17:
            raise JmaWeatherSourceError(
                f"expected 17 data columns for day {day}, got {len(values)}"
            )

        rows.append(
            DailyWeather(
                date_iso=f"{year:04d}-{month:02d}-{day:02d}",
                precipitation_total_mm=_cell_value(values[0]),
                precipitation_max_1h_mm=_cell_value(values[1]),
                precipitation_max_10min_mm=_cell_value(values[2]),
                temperature_avg_c=_cell_value(values[3]),
                temperature_max_c=_cell_value(values[4]),
                temperature_min_c=_cell_value(values[5]),
                humidity_avg_pct=_cell_value(values[6]),
                humidity_min_pct=_cell_value(values[7]),
                wind_avg_ms=_cell_value(values[8]),
                wind_max_ms=_cell_value(values[9]),
                wind_max_direction=_cell_text(values[10]),
                wind_max_instant_ms=_cell_value(values[11]),
                wind_max_instant_direction=_cell_text(values[12]),
                wind_prevailing_direction=_cell_text(values[13]),
                sunshine_hours=_cell_value(values[14]),
            )
        )
    return tuple(rows)


def fetch_all(
    dest_dir: Path,
    *,
    start_year_month: tuple[int, int] = (2005, 1),
    end_year_month: tuple[int, int] = (2026, 7),
    delay_seconds: float = DEFAULT_REQUEST_DELAY_SECONDS,
    opener: object | None = None,
    sleep: object = time.sleep,
    skip_existing: bool = True,
) -> list[Path]:
    """Fetch every venue's daily-value table for every month in range,
    saving raw HTML to dest_dir/{venue_code}/{YYYYMM}.html. Idempotent
    when skip_existing=True (safe to resume after interruption)."""
    if delay_seconds < 1.0:
        raise JmaWeatherSourceError(f"delay_seconds must be >= 1.0, got {delay_seconds!r}")

    start_y, start_m = start_year_month
    end_y, end_m = end_year_month
    months: list[tuple[int, int]] = []
    y, m = start_y, start_m
    while (y, m) <= (end_y, end_m):
        months.append((y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1

    dest_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for venue_code in sorted(VENUE_STATIONS):
        venue_dir = dest_dir / venue_code
        venue_dir.mkdir(parents=True, exist_ok=True)
        for year, month in months:
            dest_path = venue_dir / f"{year:04d}{month:02d}.html"
            if skip_existing and dest_path.exists():
                paths.append(dest_path)
                continue
            html = fetch_daily_month_html(venue_code, year, month, opener=opener)
            dest_path.write_text(html, encoding="utf-8")
            paths.append(dest_path)
            sleep(delay_seconds)
    return paths
