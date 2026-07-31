"""Parser for BOATRACE K-file (race results) text.

Parses the fixed-width, Shift-JIS text produced by
`official_source.extract_k_file_text()` into per-venue, per-race result
records. The format was confirmed by manually downloading and
inspecting real files (see tasks/HANDOFF.md):

- One K-file covers one calendar day across every venue racing that
  day. Each venue's section is delimited by a `{venue_code}KBGN` /
  `{venue_code}KEND` marker pair, where `venue_code` is the same
  two-digit code used by `race_id.py` (01-24) — race numbers 1-12
  repeat inside every venue's section, so parsing must key on
  `(venue_code, race_number)`, not race_number alone.
- Fields within a row are separated by literal ASCII spaces, while
  racer names contain embedded full-width (U+3000) spaces — splitting
  on ASCII space only (never on any-whitespace) keeps a name as one
  token instead of shredding it.

This parser is deliberately defensive rather than exhaustive: real
files include non-numeric finish-position codes for disqualifications,
late starts, and absences (e.g. "L1", "F0"). Those rows are still
captured (lane, registration number, name, motor/boat, exhibition
time, entry course) with `finish_position=None` and the raw code kept
in `finish_status_raw`, rather than raising or silently dropping the
row — covering every historical status code without a much larger real
sample would be guessing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

_VENUE_BEGIN_RE = re.compile(r"^(\d{2})KBGN\s*$")
_RACE_HEADER_RE = re.compile(r"^\s*(\d{1,2})R\s")
# "中止" is written with an embedded ideographic space in real files
# ("中　止"), so the marker is matched after stripping U+3000.
_CANCELLED_MARKER = "中止"
_PAYOUT_LABELS = frozenset({"単勝", "複勝", "２連単", "２連複", "拡連複", "３連単", "３連複"})


class KFileParseError(ValueError):
    """Raised when the K-file text cannot be parsed at all (empty input)."""


@dataclass(frozen=True)
class RaceEntryResult:
    finish_status_raw: str
    finish_position: int | None
    lane_number: int
    racer_registration_number: int
    racer_name: str
    motor_number: int | None
    boat_number: int | None
    exhibition_time: float | None
    entry_course: int | None
    start_timing: float | None
    race_time: str | None


@dataclass(frozen=True)
class RacePayout:
    bet_type: str
    combination: str
    payout_yen: int | None
    popularity_rank: int | None


@dataclass(frozen=True)
class ParsedRace:
    race_number: int
    entries: list[RaceEntryResult] = field(default_factory=list)
    payouts: list[RacePayout] = field(default_factory=list)
    is_cancelled: bool = False
    """True when the file marks this race 中止 (called off, typically
    weather). Such a race legitimately has no entries and no payouts, so
    without this flag it is indistinguishable from a parse failure — and
    it must be excluded from training rather than counted as a data
    quality defect. Found by validating the parser across the full
    2005-2026 archive, where whole venue-days appear cancelled."""


@dataclass(frozen=True)
class ParsedVenueDay:
    venue_code: str
    races: list[ParsedRace] = field(default_factory=list)


def _parse_float_or_none(token: str) -> float | None:
    try:
        return float(token)
    except ValueError:
        return None


def _parse_entry_line(line: str) -> RaceEntryResult | None:
    tokens = [t for t in line.split(" ") if t]
    if len(tokens) < 8:
        return None

    finish_raw, lane_raw, reg_raw, name = tokens[0], tokens[1], tokens[2], tokens[3]
    if not (lane_raw.isdigit() and len(lane_raw) == 1 and 1 <= int(lane_raw) <= 6):
        return None
    if not (reg_raw.isdigit() and len(reg_raw) == 4):
        return None

    motor_raw, boat_raw, exhibition_raw, entry_course_raw = tokens[4], tokens[5], tokens[6], tokens[7]
    trailing = tokens[8:]

    finish_position = (
        int(finish_raw) if finish_raw.isdigit() and 1 <= int(finish_raw) <= 6 else None
    )
    start_timing = _parse_float_or_none(trailing[0]) if len(trailing) >= 1 else None
    race_time = trailing[1] if len(trailing) >= 2 else None

    return RaceEntryResult(
        finish_status_raw=finish_raw,
        finish_position=finish_position,
        lane_number=int(lane_raw),
        racer_registration_number=int(reg_raw),
        racer_name=name.strip("　"),
        motor_number=int(motor_raw) if motor_raw.isdigit() else None,
        boat_number=int(boat_raw) if boat_raw.isdigit() else None,
        exhibition_time=_parse_float_or_none(exhibition_raw),
        entry_course=int(entry_course_raw) if entry_course_raw.isdigit() else None,
        start_timing=start_timing,
        race_time=race_time,
    )


def _parse_payout_line(line: str, current_label: str | None) -> tuple[str | None, list[RacePayout]]:
    tokens = [t for t in line.split(" ") if t]
    if not tokens:
        return current_label, []

    if tokens[0] in _PAYOUT_LABELS:
        current_label = tokens[0]
        rest = tokens[1:]
    else:
        rest = tokens

    if current_label is None:
        return current_label, []

    payouts = []
    i = 0
    while i < len(rest):
        combination = rest[i]
        i += 1
        if i >= len(rest):
            break
        amount_raw = rest[i]
        i += 1
        payout_yen = int(amount_raw) if amount_raw.isdigit() else None

        popularity_rank = None
        if i < len(rest) and rest[i] == "人気":
            i += 1
            if i < len(rest) and rest[i].isdigit():
                popularity_rank = int(rest[i])
                i += 1

        payouts.append(
            RacePayout(
                bet_type=current_label,
                combination=combination,
                payout_yen=payout_yen,
                popularity_rank=popularity_rank,
            )
        )
    return current_label, payouts


def parse_k_file_text(text: str) -> list[ParsedVenueDay]:
    """Parse full K-file text into one `ParsedVenueDay` per venue
    section (`{code}KBGN` ... `{code}KEND`), each holding its own races
    1-12 (race numbers repeat across venues, so they must not be merged
    into one global dict keyed only by race_number)."""
    if not text.strip():
        raise KFileParseError("k-file text must not be empty")

    venues: list[ParsedVenueDay] = []
    current_venue_code: str | None = None
    current_races: dict[int, ParsedRace] = {}
    current_race: ParsedRace | None = None
    current_payout_label: str | None = None
    cancelled_races: set[int] = set()

    def flush_venue() -> None:
        if current_venue_code is not None:
            races = []
            for number in sorted(current_races):
                race = current_races[number]
                if number in cancelled_races:
                    # `replace` keeps the shared entries/payouts lists, so
                    # nothing parsed into them is lost by re-stamping the flag
                    race = replace(race, is_cancelled=True)
                races.append(race)
            venues.append(ParsedVenueDay(venue_code=current_venue_code, races=races))

    for line in text.splitlines():
        venue_match = _VENUE_BEGIN_RE.match(line.strip())
        if venue_match:
            flush_venue()
            current_venue_code = venue_match.group(1)
            current_races = {}
            current_race = None
            current_payout_label = None
            cancelled_races = set()
            continue

        header_match = _RACE_HEADER_RE.match(line)
        if header_match:
            race_number = int(header_match.group(1))
            current_race = current_races.setdefault(race_number, ParsedRace(race_number=race_number))
            if _CANCELLED_MARKER in line.replace("　", ""):
                cancelled_races.add(race_number)
            current_payout_label = None
            continue

        if current_race is None:
            continue

        entry = _parse_entry_line(line)
        if entry is not None:
            current_race.entries.append(entry)
            continue

        current_payout_label, payouts = _parse_payout_line(line, current_payout_label)
        current_race.payouts.extend(payouts)

    flush_venue()
    return venues
