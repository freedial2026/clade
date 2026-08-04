"""Official per-race result page, for results that arrive today.

Source: `https://www.boatrace.jp/owpc/pc/race/raceresult?rno={R}&jcd={venue}&hd={YYYYMMDD}`.

The K-file already carries every result, but it is a daily archive, so
`ingest_daily results` runs at 02:00 and today's predictions cannot be
checked against today's races until tomorrow. This page is published
within minutes of each race, which is the gap it closes.

**It is not a substitute for the K-file.** The K-file stays the
authoritative record — it is the official archive, it carries fields this
page does not, and it is what 21 years of loaded data came from. What
this adds is *timing*, and the loader keeps the two apart for exactly
that reason: mixing fetch-time and next-day availability in one column
would make a feature query mean different things depending on whether the
archive had arrived yet.

**The empty-page trap, which caught this project twice.** A venue that is
not racing on a date returns a ~13 KB shell with a 200 status,
indistinguishable from a real page by status code alone. So does a race
that has not run yet. `parse_raceresult` therefore reports
`has_result` from the presence of an actual finishing order rather than
assuming a fetched page contains one.

Requires the `official-data` extra (`beautifulsoup4`). Rate-limited by
the caller, like every other source here.
"""

from __future__ import annotations

import re
import urllib.request
from dataclasses import dataclass
from datetime import date

from .race_id import VALID_VENUE_CODES

RACERESULT_URL = "https://www.boatrace.jp/owpc/pc/race/raceresult?rno={rno}&jcd={jcd}&hd={hd}"
_USER_AGENT = "boat-prediction-research/0.1 (non-commercial research project)"

_PLACE_DIGITS = {"１": 1, "２": 2, "３": 3, "４": 4, "５": 5, "６": 6}
_YEN_RE = re.compile(r"[¥￥,\s]")


class RaceResultSourceError(ValueError):
    """Raised for invalid input or an unparsable page."""


@dataclass(frozen=True)
class LaneResult:
    lane_number: int
    finish_position: int | None
    """`None` when the boat carries a status code instead of a placing --
    F, 失, 転 and so on. The raw text is kept in `status` rather than
    being mapped, since no mapping has been verified against the K-file's
    own vocabulary."""
    status: str | None
    racer_registration_number: int | None
    race_time_raw: str | None
    start_timing_sec: float | None


@dataclass(frozen=True)
class ResultPayout:
    bet_type: str
    combination: str
    amount_yen: int
    popularity_rank: int | None


@dataclass(frozen=True)
class RaceResultPage:
    lanes: tuple[LaneResult, ...]
    payouts: tuple[ResultPayout, ...]
    winning_method: str | None

    @property
    def has_result(self) -> bool:
        """True only when a finishing order is actually present.

        A page fetched for a venue that is not racing, or for a race that
        has not run, returns a shell with a 200 status. Nothing downstream
        may treat that as "no winner"."""
        return any(lane.finish_position is not None for lane in self.lanes)

    @property
    def winning_lane(self) -> int | None:
        for lane in self.lanes:
            if lane.finish_position == 1:
                return lane.lane_number
        return None


def raceresult_url(target_date: date, venue_code: str, race_number: int) -> str:
    if venue_code not in VALID_VENUE_CODES:
        raise RaceResultSourceError(f"unknown venue_code: {venue_code!r}")
    if not 1 <= race_number <= 12:
        raise RaceResultSourceError(f"race_number out of range 1-12: {race_number!r}")
    return RACERESULT_URL.format(
        rno=race_number, jcd=venue_code, hd=target_date.strftime("%Y%m%d")
    )


def fetch_raceresult_html(
    target_date: date, venue_code: str, race_number: int, *, opener: object | None = None
) -> str:
    """Fetch one race's result page. Pacing is the caller's business, as
    with every other source in this package."""
    opener = opener or urllib.request
    url = raceresult_url(target_date, venue_code, race_number)
    request = opener.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with opener.urlopen(request, timeout=20) as response:
            return response.read().decode("utf-8", errors="replace")
    except Exception as exc:
        raise RaceResultSourceError(f"failed to fetch {url}: {exc}") from exc


def _int_or_none(text: str) -> int | None:
    text = text.strip()
    return int(text) if text.isdigit() else None


def _float_or_none(text: str) -> float | None:
    text = text.strip().replace("\xa0", "")
    if not text:
        return None
    # start timings print as ".05"; a flying start prints as "F.05"
    flying = text.startswith("F")
    text = text.lstrip("F").lstrip()
    try:
        value = float(text)
    except ValueError:
        return None
    return -value if flying else value


def _table_after(soup, heading: str):
    node = soup.find(string=lambda s: s and s.strip() == heading)
    return node.find_parent("table") if node is not None else None


def parse_raceresult(html: str) -> RaceResultPage:
    """Parse a fetched result page.

    Returns a page with empty `lanes` rather than raising when the race
    has not run: the caller distinguishes those through `has_result`, and
    a run that treated "not yet" as an error would stop the whole capture
    every time it reached a race in progress.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")

    starts: dict[int, float | None] = {}
    start_table = _table_after(soup, "スタート情報")
    if start_table is not None:
        for row in start_table.find_all("tr"):
            text = row.get_text(" ", strip=True)
            parts = text.split()
            # The winning lane's row has the 決まり手 appended after the
            # timing ("1 .05    逃げ"), so the whole remainder does not
            # parse as a number. Taking only the first token after the
            # lane keeps it -- and losing it would have been silent, and
            # would have hit lane 1 precisely because it won.
            if len(parts) >= 2 and parts[0].isdigit():
                starts[int(parts[0])] = _float_or_none(parts[1])

    lanes: list[LaneResult] = []
    result_table = _table_after(soup, "着")
    if result_table is not None:
        for row in result_table.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in row.find_all("td")]
            if len(cells) < 3:
                continue
            place_raw, lane_raw, racer_raw = cells[0], cells[1], cells[2]
            lane = _int_or_none(lane_raw)
            if lane is None or not 1 <= lane <= 6:
                continue
            position = _PLACE_DIGITS.get(place_raw.strip()) or _int_or_none(place_raw)
            registration = _int_or_none(racer_raw.split()[0]) if racer_raw.split() else None
            lanes.append(
                LaneResult(
                    lane_number=lane,
                    finish_position=position,
                    status=None if position is not None else (place_raw.strip() or None),
                    racer_registration_number=registration,
                    race_time_raw=cells[3].strip() if len(cells) > 3 else None,
                    start_timing_sec=starts.get(lane),
                )
            )

    payouts: list[ResultPayout] = []
    payout_table = _table_after(soup, "勝式")
    if payout_table is not None:
        bet_type = ""
        for row in payout_table.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in row.find_all("td")]
            if len(cells) < 3:
                continue
            if cells[0]:
                bet_type = cells[0]
            amount = _YEN_RE.sub("", cells[2])
            if not bet_type or not cells[1] or not amount.isdigit():
                continue
            payouts.append(
                ResultPayout(
                    bet_type=bet_type,
                    combination=cells[1].replace(" ", ""),
                    amount_yen=int(amount),
                    popularity_rank=_int_or_none(cells[3]) if len(cells) > 3 else None,
                )
            )

    method_table = _table_after(soup, "決まり手")
    winning_method = None
    if method_table is not None:
        # The heading sits in a td of the same table, so it has to be
        # excluded by name -- taking the first non-empty cell returns the
        # heading itself.
        values = [
            c.get_text(" ", strip=True)
            for row in method_table.find_all("tr")
            for c in row.find_all("td")
        ]
        winning_method = next((v for v in values if v and v != "決まり手"), None)

    return RaceResultPage(
        lanes=tuple(sorted(lanes, key=lambda lane: lane.lane_number)),
        payouts=tuple(payouts),
        winning_method=winning_method,
    )
