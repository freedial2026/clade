"""Official data source: BOATRACE closing odds (締切時オッズ).

Source: `https://www.boatrace.jp/owpc/pc/race/oddstf?rno={R}&jcd={venue}&hd={YYYYMMDD}`
(win/place odds), plus `.../race/index?hd={YYYYMMDD}` to discover which
venues raced on a given day.

**Retention window, established by probing (see tasks/HANDOFF.md)**:
these pages retain history back to **2017-04-01** and no earlier
(2017-03-01 and every probed date before it return no odds across 8
venues; 2017-04-01 onward returns data). That boundary falls exactly on
the Japanese fiscal-year start, so it is probably a fiscal-year-based
retention policy. Whether it is *fixed* (keep from FY2017 on) or
*rolling* (keep the last N fiscal years) could not be determined from a
single point in time -- if rolling, FY2017 data would age out on
2027-04-01, so older years are worth capturing sooner rather than later.

**What this source is and is not**: only ONE odds observation per race
is retained -- the closing value, after vote aggregation completed
("締切時オッズは、発売票数の集計が完了した時点でのオッズを表示しています").
There is no historical time series, so `odds_snapshots`'s
`UNIQUE(race_id, bet_type, combination, observed_at)` can only ever be
populated with a single `observed_at` per race from this source, and the
`OD_ODDS_STALE` / `OD_ODDS_SHARP_CHANGE` skip reasons (guide §15.1)
cannot be evaluated retroactively. Correspondingly these odds become
available only *at* the deadline, so a leakage-safe backtest using them
must set `prediction_at` to the deadline, not earlier.

Because a live/in-progress race would render current rather than final
odds on the same URL, `parse_win_place_odds` reports whether the page
actually carried the 締切時オッズ marker instead of assuming it.

Rate-limited like the other sources in this package (default 3s).
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

INDEX_URL = "https://www.boatrace.jp/owpc/pc/race/index?hd={hd}"
ODDS_URL = "https://www.boatrace.jp/owpc/pc/race/oddstf?rno={rno}&jcd={jcd}&hd={hd}"
_USER_AGENT = "boat-prediction-research/0.1 (non-commercial research project)"
DEFAULT_REQUEST_DELAY_SECONDS = 3.0

EARLIEST_RETAINED_DATE = date(2017, 4, 1)
CLOSING_ODDS_MARKER = "締切時オッズ"

_JCD_RE = re.compile(r"jcd=(\d{2})")


class OddsSourceError(ValueError):
    """Raised for invalid input or a failed fetch/parse."""


@dataclass(frozen=True)
class WinPlaceOdds:
    lane_number: int
    racer_name: str
    win_odds: float | None
    place_odds_low: float | None
    place_odds_high: float | None


@dataclass(frozen=True)
class RaceOdds:
    is_closing: bool
    entries: tuple[WinPlaceOdds, ...]


def index_url(target_date: date) -> str:
    return INDEX_URL.format(hd=target_date.strftime("%Y%m%d"))


def odds_url(target_date: date, venue_code: str, race_number: int) -> str:
    if venue_code not in VALID_VENUE_CODES:
        raise OddsSourceError(f"unknown venue_code: {venue_code!r}")
    if not 1 <= race_number <= 12:
        raise OddsSourceError(f"race_number out of range 1-12: {race_number!r}")
    return ODDS_URL.format(rno=race_number, jcd=venue_code, hd=target_date.strftime("%Y%m%d"))


def _fetch(url: str, opener: object | None) -> str:
    opener = opener or urllib.request
    request = opener.Request(url, headers={"User-Agent": _USER_AGENT})
    max_retries = 3
    for attempt in range(max_retries):
        try:
            with opener.urlopen(request, timeout=20) as response:
                return response.read().decode("utf-8", errors="replace")
        except (TimeoutError, OSError) as exc:
            if attempt < max_retries - 1:
                wait_seconds = 2 ** attempt
                time.sleep(wait_seconds)
                continue
            raise OddsSourceError(f"failed to fetch {url}: {exc}") from exc
        except Exception as exc:
            raise OddsSourceError(f"failed to fetch {url}: {exc}") from exc


def fetch_odds_page(
    target_date: date,
    venue_code: str,
    race_number: int,
    *,
    opener: object | None = None,
) -> str:
    """Fetch one race's odds page and return its raw HTML.

    `fetch_range` writes pages to disk for a bulk archive run; this
    returns the page instead, for a caller that wants to parse it now --
    `db.capture_odds`, which records live pre-deadline readings and has
    no reason to keep a file per observation.

    Applies no delay of its own: the caller owns the pacing, because it
    is the caller that knows how many pages it is about to request.
    """
    return _fetch(odds_url(target_date, venue_code, race_number), opener=opener)


def fetch_racing_venues(target_date: date, *, opener: object | None = None) -> tuple[str, ...]:
    """Return the venue codes that raced on `target_date`, read from the
    official daily index. One request per day, which avoids blindly
    probing all 24 venues (verified against the B-file parser's venue
    list for 2026-06-01: identical 12 venues)."""
    html = _fetch(index_url(target_date), opener=opener)
    codes = sorted({c for c in _JCD_RE.findall(html) if c in VALID_VENUE_CODES})
    return tuple(codes)


MIN_QUOTED_ODDS = 1.0
"""Below this a cell is not a quote.

Odds here are payout multipliers that include the stake, so 1.00 is the
floor -- and 1.00 is exactly the smallest value in the 213,729 archived
snapshots. The page nonetheless renders `0.0` for a boat with no quote
(an absent 欠場 entry), which parsed as a real 0.0 for 2,309 rows.
That is not merely wrong, it is dangerous: market normalisation divides
by the odds, so a stored 0.0 turns into an infinite implied probability
rather than a missing one.
"""


def _parse_odds_cell(text: str) -> tuple[float | None, float | None]:
    """Parse an odds cell: '5.6' -> (5.6, None); '2.0-2.8' -> (2.0, 2.8);
    non-numeric (欠場 etc.) and any value below `MIN_QUOTED_ODDS`
    -> (None, None)."""

    def _quote(value: str) -> float | None:
        try:
            parsed = float(value)
        except ValueError:
            return None
        return parsed if parsed >= MIN_QUOTED_ODDS else None

    text = text.strip()
    if "-" in text:
        low, _, high = text.partition("-")
        return _quote(low), _quote(high)
    return _quote(text), None


def _section_rows(soup: object, heading: str) -> list:
    node = soup.find("span", string=heading)
    if node is None:
        return []
    table = node.find_next("table")
    return table.find_all("tbody") if table is not None else []


def parse_win_place_odds(html: str) -> RaceOdds:
    """Parse a fetched odds page into per-lane win/place odds. Returns
    an empty `entries` tuple when the page carries no odds table (a
    venue that did not race that day renders the page shell only)."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    is_closing = CLOSING_ODDS_MARKER in html

    win: dict[int, tuple[str, float | None]] = {}
    for tbody in _section_rows(soup, "単勝オッズ"):
        cells = tbody.find_all("td")
        if len(cells) < 3:
            continue
        lane_text = cells[0].get_text(strip=True)
        if not lane_text.isdigit():
            continue
        value, _ = _parse_odds_cell(cells[2].get_text(strip=True))
        win[int(lane_text)] = (cells[1].get_text(strip=True), value)

    place: dict[int, tuple[float | None, float | None]] = {}
    for tbody in _section_rows(soup, "複勝オッズ"):
        cells = tbody.find_all("td")
        if len(cells) < 3:
            continue
        lane_text = cells[0].get_text(strip=True)
        if not lane_text.isdigit():
            continue
        place[int(lane_text)] = _parse_odds_cell(cells[2].get_text(strip=True))

    entries = []
    for lane in sorted(win):
        racer_name, win_odds = win[lane]
        low, high = place.get(lane, (None, None))
        entries.append(
            WinPlaceOdds(
                lane_number=lane,
                racer_name=racer_name,
                win_odds=win_odds,
                place_odds_low=low,
                place_odds_high=high,
            )
        )
    return RaceOdds(is_closing=is_closing, entries=tuple(entries))


def fetch_range(
    start_date: date,
    end_date: date,
    dest_dir: Path,
    *,
    delay_seconds: float = DEFAULT_REQUEST_DELAY_SECONDS,
    opener: object | None = None,
    sleep: object = time.sleep,
    log: object = print,
) -> int:
    """Fetch closing-odds pages for every racing venue/race in the date
    range, saving raw HTML to dest_dir/{YYYYMMDD}/{venue}_{race}.html.
    Idempotent: existing files are skipped, so a run can resume after
    interruption. Returns the number of pages newly written."""
    if delay_seconds < 1.0:
        raise OddsSourceError(f"delay_seconds must be >= 1.0, got {delay_seconds!r}")
    if start_date < EARLIEST_RETAINED_DATE:
        raise OddsSourceError(
            f"start_date {start_date} precedes the retention window "
            f"({EARLIEST_RETAINED_DATE}); no odds are available before it"
        )
    if end_date < start_date:
        raise OddsSourceError(f"end_date {end_date} precedes start_date {start_date}")

    written = 0
    current = start_date
    while current <= end_date:
        day_dir = dest_dir / current.strftime("%Y%m%d")
        venues_marker = day_dir / "_venues.txt"

        if venues_marker.exists():
            venues = tuple(v for v in venues_marker.read_text(encoding="utf-8").split() if v)
        else:
            venues = fetch_racing_venues(current, opener=opener)
            sleep(delay_seconds)
            day_dir.mkdir(parents=True, exist_ok=True)
            venues_marker.write_text("\n".join(venues), encoding="utf-8")

        for venue_code in venues:
            for race_number in range(1, 13):
                dest_path = day_dir / f"{venue_code}_{race_number:02d}.html"
                if dest_path.exists():
                    continue
                html = _fetch(odds_url(current, venue_code, race_number), opener=opener)
                dest_path.write_text(html, encoding="utf-8")
                written += 1
                sleep(delay_seconds)

        log(f"{current.isoformat()}: {len(venues)} venues, {written} pages written so far")
        current = date.fromordinal(current.toordinal() + 1)
    return written
