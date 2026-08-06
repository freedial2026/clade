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

import argparse
import re
import time
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .race_id import VALID_VENUE_CODES

INDEX_URL = "https://www.boatrace.jp/owpc/pc/race/index?hd={hd}"
ODDS_URL = "https://www.boatrace.jp/owpc/pc/race/oddstf?rno={rno}&jcd={jcd}&hd={hd}"
EXACTA_ODDS_URL = "https://www.boatrace.jp/owpc/pc/race/odds2tf?rno={rno}&jcd={jcd}&hd={hd}"
TRIFECTA_ODDS_URL = "https://www.boatrace.jp/owpc/pc/race/odds3t?rno={rno}&jcd={jcd}&hd={hd}"
SANRENPUKU_ODDS_URL = "https://www.boatrace.jp/owpc/pc/race/odds3f?rno={rno}&jcd={jcd}&hd={hd}"
WIDE_ODDS_URL = "https://www.boatrace.jp/owpc/pc/race/oddsk?rno={rno}&jcd={jcd}&hd={hd}"
"""3連単, 3連複, 拡連複 -- each on its own page, unlike 2連単/2連複's shared
one. Retention was probed the same way as `EARLIEST_RETAINED_DATE` below
(tasks/CURRENT.md, 2026-08-06): a real race day/venue just before that
date returns the same page-shell-with-no-締切マーカー shape the other
odds pages return before 2017-04-01, and 2017-04-01 itself returns real
data of the same shape as a 2026 page. All three share the boundary."""
"""2連単 and 2連複, both rendered on one page.

Captured for a reason the win/place page cannot serve: these are
*separate pools* on the same race. `P(1着 = boat i)` can be read off the
単勝 pool directly, and also recovered by summing the 30 2連単
combinations that start with i. The two need not agree, and where they
disagree one pool is stale -- which is the classic pari-mutuel
inefficiency and the one thing this project has never been able to look
at, since the archive keeps a single closing snapshot of 単勝 alone.

One page yields both 2連単 and 2連複, so the second pool is free.
"""
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


EXACTA_BET_TYPE = "exacta"
QUINELLA_BET_TYPE = "quinella"
TRIFECTA_BET_TYPE = "trifecta"
SANRENPUKU_BET_TYPE = "sanrenpuku"
WIDE_BET_TYPE = "wide"
"""`odds_snapshots.bet_type` values for 2連単, 2連複, 3連単, 3連複, 拡連複.

English, matching the `win`/`place_low`/`place_high` already in that
column, rather than the Japanese `２連単` that `race_payouts` uses. The
two tables come from different sources and already disagree; making
`odds_snapshots` internally consistent is worth more than making it
match a table it is never joined to on this column.

These five also match `combination_model`/`evaluate_bet_types`'s
`BetTypeSpec.key` naming exactly, deliberately -- `odds_bet_type` on
those specs is one of these constants, so a spec and its odds rows are
never one string apart from each other.
"""


@dataclass(frozen=True)
class CombinationOdds:
    bet_type: str
    combination: str
    odds: float


@dataclass(frozen=True)
class RaceCombinationOdds:
    is_closing: bool
    entries: tuple[CombinationOdds, ...]


def index_url(target_date: date) -> str:
    return INDEX_URL.format(hd=target_date.strftime("%Y%m%d"))


def _race_url(template: str, target_date: date, venue_code: str, race_number: int) -> str:
    if venue_code not in VALID_VENUE_CODES:
        raise OddsSourceError(f"unknown venue_code: {venue_code!r}")
    if not 1 <= race_number <= 12:
        raise OddsSourceError(f"race_number out of range 1-12: {race_number!r}")
    return template.format(rno=race_number, jcd=venue_code, hd=target_date.strftime("%Y%m%d"))


def odds_url(target_date: date, venue_code: str, race_number: int) -> str:
    return _race_url(ODDS_URL, target_date, venue_code, race_number)


def exacta_odds_url(target_date: date, venue_code: str, race_number: int) -> str:
    return _race_url(EXACTA_ODDS_URL, target_date, venue_code, race_number)


def trifecta_odds_url(target_date: date, venue_code: str, race_number: int) -> str:
    return _race_url(TRIFECTA_ODDS_URL, target_date, venue_code, race_number)


def sanrenpuku_odds_url(target_date: date, venue_code: str, race_number: int) -> str:
    return _race_url(SANRENPUKU_ODDS_URL, target_date, venue_code, race_number)


def wide_odds_url(target_date: date, venue_code: str, race_number: int) -> str:
    return _race_url(WIDE_ODDS_URL, target_date, venue_code, race_number)


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


def fetch_exacta_odds_page(
    target_date: date,
    venue_code: str,
    race_number: int,
    *,
    opener: object | None = None,
) -> str:
    """Fetch one race's 2連単/2連複 page and return its raw HTML.

    Same contract as `fetch_odds_page`, including that pacing is the
    caller's business.
    """
    return _fetch(exacta_odds_url(target_date, venue_code, race_number), opener=opener)


def fetch_trifecta_odds_page(
    target_date: date, venue_code: str, race_number: int, *, opener: object | None = None
) -> str:
    """Fetch one race's 3連単 page. Same contract as `fetch_odds_page`."""
    return _fetch(trifecta_odds_url(target_date, venue_code, race_number), opener=opener)


def fetch_sanrenpuku_odds_page(
    target_date: date, venue_code: str, race_number: int, *, opener: object | None = None
) -> str:
    """Fetch one race's 3連複 page. Same contract as `fetch_odds_page`."""
    return _fetch(sanrenpuku_odds_url(target_date, venue_code, race_number), opener=opener)


def fetch_wide_odds_page(
    target_date: date, venue_code: str, race_number: int, *, opener: object | None = None
) -> str:
    """Fetch one race's 拡連複 page. Same contract as `fetch_odds_page`."""
    return _fetch(wide_odds_url(target_date, venue_code, race_number), opener=opener)


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


def _combination_grid(soup: object, heading: str, bet_type: str) -> list[CombinationOdds]:
    """Read one of the two grids on the 2連単/2連複 page.

    Both are laid out the same way and the layout is worth stating,
    because it is not a list of combinations: the header carries the six
    *first-place* boats, and every body row holds six `(second boat,
    odds)` pairs -- one per header column. So cell `2c` of a row is the
    second boat for header column `c`, and cell `2c+1` is its price.

    The first-place lane is read from the header rather than assumed to
    be `c + 1`. On this page they coincide, but a 欠場 makes the
    assumption a silent one-column shift, and nothing downstream could
    detect a combination attributed to the wrong boat.

    2連複's grid is triangular, with blanks above the diagonal; blanks
    simply produce no entry, so the shape needs no special case. Its
    combinations come out ascending (`1-2`, never `2-1`), matching how
    `race_payouts` stores them.
    """
    node = soup.find("span", string=heading)
    if node is None:
        return []
    table = node.find_next("table")
    if table is None:
        return []
    head = table.find("thead")
    body = table.find("tbody")
    if head is None or body is None:
        return []

    head_cells = head.find_all(["td", "th"])
    first_lanes: dict[int, int] = {}
    for column in range(len(head_cells) // 2):
        text = head_cells[column * 2].get_text(strip=True)
        if text.isdigit():
            first_lanes[column] = int(text)

    entries: list[CombinationOdds] = []
    for row in body.find_all("tr"):
        cells = row.find_all(["td", "th"])
        for column, first_lane in first_lanes.items():
            if column * 2 + 1 >= len(cells):
                continue
            second_text = cells[column * 2].get_text(strip=True)
            if not second_text.isdigit():
                continue
            value, _ = _parse_odds_cell(cells[column * 2 + 1].get_text(strip=True))
            if value is None:
                continue
            entries.append(
                CombinationOdds(
                    bet_type=bet_type,
                    combination=f"{first_lane}-{int(second_text)}",
                    odds=value,
                )
            )
    return entries


def parse_exacta_odds(html: str) -> RaceCombinationOdds:
    """Parse a fetched 2連単/2連複 page into per-combination odds.

    Returns an empty `entries` tuple for the page shell a venue that did
    not race renders -- the same failure mode that `beforeinfo_source`
    records, where an empty page is indistinguishable from "no data
    retained" unless the caller already knows the venue raced.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    entries = _combination_grid(soup, "2連単オッズ", EXACTA_BET_TYPE)
    entries.extend(_combination_grid(soup, "2連複オッズ", QUINELLA_BET_TYPE))
    return RaceCombinationOdds(
        is_closing=CLOSING_ODDS_MARKER in html, entries=tuple(entries)
    )


def parse_wide_odds(html: str) -> RaceCombinationOdds:
    """Parse a fetched 拡連複 page into per-pair odds.

    Structurally identical to 2連複's grid on the 2連単/2連複 page --
    same triangular header-column-by-lowest-lane layout, same low-high
    odds range in each cell -- so this reuses `_combination_grid`
    unmodified rather than duplicating it. Verified against a real
    fetched page: 15/15 pairs, matching `combination_model.wide_probabilities`'s
    15-pair shape exactly (tasks/CURRENT.md, 2026-08-06).
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    entries = _combination_grid(soup, "拡連複オッズ", WIDE_BET_TYPE)
    return RaceCombinationOdds(is_closing=CLOSING_ODDS_MARKER in html, entries=tuple(entries))


def _trifecta_grid(soup: object, heading: str, bet_type: str) -> list[CombinationOdds]:
    """Read the 3連単/3連複 grid: six column-groups (one per header lane,
    same as `_combination_grid`), but each group is itself two-level --
    a *leading* value (second place for 3連単, the middle of the triple
    for 3連複) and a *trailing* value (third place / the highest of the
    triple) -- because a pair alone cannot address a 3-way combination.

    The leading value is only rendered once per block and left out of
    the rows below it (an HTML rowspan collapsing to a shorter `<tr>` in
    the parsed tree), so every group needs its own carried-forward
    state: 3 cells in a row means "start a new block, remember this
    leading value"; 2 cells means "reuse the group's last leading value."

    The block boundaries are not per-group -- they are driven by a value
    shared across every group in that row (the smallest lane not yet
    used, walking upward) -- so in practice all six groups switch
    between 3-cell and 2-cell rows on the very same `<tr>`, in lockstep.
    This is read off the row's own cell count divided by six rather than
    assumed, so a page that violates the lockstep would raise instead of
    silently misattributing a cell.

    Verified against real fetched pages (tasks/CURRENT.md, 2026-08-06):
    120/120 unique combinations for 3連単, 20/20 for 3連複, both with
    zero duplicates and values matching a hand-traced reading of the
    same page.
    """
    node = soup.find("span", string=heading)
    if node is None:
        return []
    table = node.find_next("table")
    if table is None:
        return []
    head = table.find("thead")
    body = table.find("tbody")
    if head is None or body is None:
        return []

    head_cells = head.find_all(["td", "th"])
    leaders: list[int] = []
    for column in range(len(head_cells) // 2):
        text = head_cells[column * 2].get_text(strip=True)
        if text.isdigit():
            leaders.append(int(text))
    n_groups = len(leaders)
    if n_groups == 0:
        return []

    # Per-group "current leading value" -- None until that group's first
    # populated block, since a lane with too few valid combinations left
    # (e.g. 3連複's lane 5/6 columns, which can run out of larger
    # partners) may never start at all.
    leading: list[int | None] = [None] * n_groups

    entries: list[CombinationOdds] = []
    for row in body.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if len(cells) % n_groups != 0:
            continue  # malformed row -- skip rather than misattribute
        stride = len(cells) // n_groups
        if stride not in (2, 3):
            continue
        for group in range(n_groups):
            texts = [
                c.get_text(strip=True) for c in cells[group * stride : (group + 1) * stride]
            ]
            if stride == 3:
                leading_text, trailing_text, odds_text = texts
                if leading_text == "":
                    continue  # this group has no entry in this block
                leading[group] = int(leading_text)
                trailing_text_value = trailing_text
            else:
                trailing_text_value, odds_text = texts
                if trailing_text_value == "" or leading[group] is None:
                    continue
            if not trailing_text_value.isdigit():
                continue
            value, _ = _parse_odds_cell(odds_text)
            if value is None:
                continue
            entries.append(
                CombinationOdds(
                    bet_type=bet_type,
                    combination=f"{leaders[group]}-{leading[group]}-{int(trailing_text_value)}",
                    odds=value,
                )
            )
    return entries


def parse_trifecta_odds(html: str) -> RaceCombinationOdds:
    """Parse a fetched 3連単 page into per-combination odds.

    `combination` is `"first-second-third"`, in finish order -- matching
    how `race_payouts.combination` writes a 3連単 (e.g. `"1-4-3"`), and
    `combination_model.encode_trifecta`'s ordering. The page lists a
    combination once, under its own first-place column, so no
    deduplication is needed.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    entries = _trifecta_grid(soup, "3連単オッズ", TRIFECTA_BET_TYPE)
    return RaceCombinationOdds(is_closing=CLOSING_ODDS_MARKER in html, entries=tuple(entries))


def parse_sanrenpuku_odds(html: str) -> RaceCombinationOdds:
    """Parse a fetched 3連複 page into per-combination odds.

    `combination` is `"low-mid-high"`, ascending -- matching how
    `race_payouts.combination` writes a 3連複 (e.g. `"1-2-3"`, always
    sorted) and `combination_model.sanrenpuku_probabilities`'s key shape.
    The page groups by the *lowest* lane in the triple rather than by
    first place (there is no "first place" for an unordered bet), and
    every unordered triple appears exactly once, under its lowest
    member's column, already in ascending order -- `_trifecta_grid`'s
    `leaders[group]-leading[group]-trailing` is `low-mid-high` here
    without any extra sort, because the page itself only ever lists a
    larger value after a smaller one within a group.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    entries = _trifecta_grid(soup, "3連複オッズ", SANRENPUKU_BET_TYPE)
    return RaceCombinationOdds(is_closing=CLOSING_ODDS_MARKER in html, entries=tuple(entries))


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


_TRIFECTA_FAMILY_PAGES: tuple[tuple[str, object], ...] = (
    ("odds3t", trifecta_odds_url),
    ("odds3f", sanrenpuku_odds_url),
    ("oddsk", wide_odds_url),
)
"""3連単/3連複/拡連複 each have their own page (unlike 2連単/2連複's shared
one), so `fetch_trifecta_family_range` writes three files per race
rather than `fetch_range`'s one."""


def fetch_trifecta_family_range(
    start_date: date,
    end_date: date,
    dest_dir: Path,
    *,
    delay_seconds: float = DEFAULT_REQUEST_DELAY_SECONDS,
    opener: object | None = None,
    sleep: object = time.sleep,
    log: object = print,
) -> int:
    """Fetch 3連単/3連複/拡連複 closing-odds pages for every racing
    venue/race in the date range, saving raw HTML to
    `dest_dir/{YYYYMMDD}/{venue}_{race}_{page}.html` (`page` one of
    `odds3t`/`odds3f`/`oddsk`).

    Same shape as `fetch_range` -- idempotent per file, resumable after
    interruption, one request every `delay_seconds` -- but three pages
    per race instead of one, since these three pools do not share a page
    the way 2連単/2連複 do. A day's `_venues.txt` marker is shared with
    `fetch_range` if `dest_dir` is reused across both, so running this
    against the same directory a win/place fetch already populated skips
    the redundant venue-discovery request rather than repeating it.

    Returns the number of pages newly written.
    """
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
                for page_name, url_fn in _TRIFECTA_FAMILY_PAGES:
                    dest_path = day_dir / f"{venue_code}_{race_number:02d}_{page_name}.html"
                    if dest_path.exists():
                        continue
                    html = _fetch(url_fn(current, venue_code, race_number), opener=opener)
                    dest_path.write_text(html, encoding="utf-8")
                    written += 1
                    sleep(delay_seconds)

        log(f"{current.isoformat()}: {len(venues)} venues, {written} pages written so far")
        current = date.fromordinal(current.toordinal() + 1)
    return written


def _main(argv: list[str] | None = None) -> int:
    """Fetch archived closing-odds pages for a date range. `--pool win`
    (default) fetches 単勝/複勝; `--pool trifecta` fetches 3連単/3連複/拡連複
    instead -- the two never ran through the same CLI before this, so
    this is the first committed entry point for either (tasks/CURRENT.md,
    2026-08-06: the original 80-day win/place archive was fetched by a
    one-off script, never checked in)."""
    parser = argparse.ArgumentParser(description=_main.__doc__)
    parser.add_argument("--start-date", type=date.fromisoformat, required=True)
    parser.add_argument("--end-date", type=date.fromisoformat, default=None)
    parser.add_argument("--dest-dir", type=Path, required=True)
    parser.add_argument(
        "--pool",
        choices=("win", "trifecta"),
        default="win",
        help="win = 単勝/複勝 (fetch_range); trifecta = 3連単/3連複/拡連複 (fetch_trifecta_family_range)",
    )
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=DEFAULT_REQUEST_DELAY_SECONDS,
        help="minimum 1.0; the site's policy prohibits large-volume access",
    )
    args = parser.parse_args(argv)

    fetch = fetch_range if args.pool == "win" else fetch_trifecta_family_range
    written = fetch(
        args.start_date,
        args.end_date or args.start_date,
        args.dest_dir,
        delay_seconds=args.delay_seconds,
    )
    print(f"done: {written} pages written into {args.dest_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
