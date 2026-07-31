"""Parser for BOATRACE B-file (race card / 番組表) text.

Parses the Shift-JIS text produced by
`official_source.extract_k_file_text()` (generic despite the name --
reused here for B-files too) into per-venue, per-race entry-card
records. The format was confirmed by downloading and inspecting one
real day (2026-06-01, see tasks/HANDOFF.md):

- Same overall shape as the K-file: one B-file covers every venue
  racing that day, each delimited by a `{venue_code}BBGN` /
  `{venue_code}BEND` marker pair (same two-digit `venue_code` as
  `race_id.py` / `kfile_parser.py`).
- Unlike the K-file, entry rows are **fixed-column**, not
  space-tokenized: the racer name field directly abuts the
  registration number and age with no delimiter (e.g.
  `"3637齋藤和政55愛知54B1 4.15 ..."`), so splitting on whitespace would
  shred the name. Column boundaries below were derived by verifying
  zero parse failures across all 864 real entry rows in the sample
  file -- every row was exactly 73 characters:

  ```
  [0]     lane number (1 digit)
  [2:6]   registration number (4 digits)
  [6:10]  racer name (4 chars, U+3000 ideographic-space padded)
  [10:12] age
  [12:14] branch (支部, 2 chars)
  [14:16] weight (kg)
  [16:18] racer class (A1/A2/B1/B2)
  [19:23] national win rate
  [24:29] national second-or-better rate
  [30:34] local (venue) win rate
  [35:40] local second-or-better rate
  [41:43] motor number
  [44:49] motor second-or-better rate
  [50:52] boat number
  [53:58] boat second-or-better rate
  [59:73] trailing info: current-series per-heat results plus an
          early-start-tendency indicator (早見), packed together with
          no fixed sub-boundary -- confirmed from a real sample that
          the results portion can exceed 6 characters for series with
          makeup/additional heats, which shifts where the trailing
          number starts. Kept as one raw string rather than
          guessed-split; not needed for the win-rate/motor/boat
          features this parser exists for.
  ```

- The race header line (e.g. `"１Ｒ  予選　　　　　　　　　　Ｈ１８００ｍ
  電話投票締切予定１７：４１"`) uses full-width digits/characters;
  normalizing with `unicodedata.normalize("NFKC", ...)` first makes it
  matchable with an ordinary ASCII regex. This line carries
  `races.scheduled_deadline_at`'s time component (see
  docs/domain/.../implementation_guide.md's schema) -- the only
  time-of-day anchor found in any BOATRACE data source so far.

- Each venue section opens with meeting-level metadata that precedes
  its first race header: an optional tournament title, then a "day
  banner" line, e.g.
  `"第１日          ２０２６年　６月　１日                  ボートレース大　村"`,
  giving that venue's day-N-of-series (節第N日) and the meeting date.
  Full-width digits/spaces again need NFKC normalizing first. This is
  the only place day-N-of-series appears in any BOATRACE source found
  so far, and matters because motor/boat condition and racer form both
  drift across a multi-day series. Confirmed present even for
  `is_cancelled` venues (the header is written before the cancellation
  notice); never present for `data_pending` venues (nothing is written
  yet). See `ParsedVenueDayCard.series_day`/`meeting_date`/
  `meeting_title` for field semantics and archive-wide validation
  results.

This parser is deliberately defensive: unrecognized lines (headers,
separators, blank lines) are skipped rather than raising.

Validated across the archive (132 files sampled, ~6 per year,
2005-2026): zero parse errors, zero silently-dropped entry rows, and
every parsed race carrying exactly 6 entries, so the fixed columns
above hold for all 21 years. Two findings from that sweep are handled
here: `ParsedVenueDayCard.data_pending` and `ParsedRaceCard.race_class`
(see each for why).

A separate full-archive pass over all 7,862 files (0 extract or parse
errors) accounts for every venue section that carries no race: 152 are
`data_pending`, 2 are `is_cancelled`, and none is unexplained -- so a
card-less, unflagged venue can be treated as a parse defect.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

_VENUE_BEGIN_RE = re.compile(r"^(\d{2})BBGN\s*$")
_RACE_HEADER_RE = re.compile(r"^(\d+)R\s*(.+?)\s+H(\d+)m\s+電話投票締切予定(\d{1,2}):(\d{2})")
_ENTRY_LANE_RE = re.compile(r"^[1-6] \d{4}")
_ENTRY_ROW_LEN = 73

# Marks the start of the meeting-metadata block that precedes a venue's
# first race header (see module docstring). Matched against the NFKC-
# normalized line since it can appear as full-width "＊＊＊　番組表　＊＊＊".
_MEETING_MARKER = "番組表"

# The "day banner" line giving day-N-of-series and the meeting date,
# e.g. (after NFKC normalizing full-width digits/spaces to ASCII)
# "第1日          2026年 6月 1日                  ボートレース大 村".
_DAY_BANNER_RE = re.compile(r"^第\s*(\d+)日\s+(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日\s+.+$")

# A venue section can be published as a placeholder before that venue's
# card is finalized -- the section exists but holds this notice instead
# of any race, e.g.
#   02BBGN / ボートレース戸　田 / この場のデータ更新は、いましばらくお待ちください。 / 02BEND
# Without a flag this is indistinguishable from a parse failure (the
# same defect class as `kfile_parser.ParsedRace.is_cancelled`).
_DATA_PENDING_MARKER = "しばらくお待ちください"

# A venue section can also report that the whole meeting was called off,
# after an otherwise complete 番組表 header:
#   20BBGN / ... / 開催は中止となりました。 / 20BEND
# Semantically distinct from `_DATA_PENDING_MARKER`: a pending card may
# still be published, a cancelled meeting never will be. Downstream that
# is the difference between a real gap in this archive snapshot and a
# day that correctly has no card at all.
_VENUE_CANCELLED_MARKER = "開催は中止となりました"


class BFileParseError(ValueError):
    """Raised when the B-file text cannot be parsed at all (empty input)."""


@dataclass(frozen=True)
class RaceEntryCard:
    lane_number: int
    racer_registration_number: int
    racer_name: str
    age: int
    branch: str
    weight_kg: int
    racer_class: str
    national_win_rate: float
    national_second_rate: float
    local_win_rate: float
    local_second_rate: float
    motor_number: int
    motor_second_rate: float
    boat_number: int
    boat_second_rate: float
    trailing_info_raw: str


@dataclass(frozen=True)
class ParsedRaceCard:
    race_number: int
    race_class_label: str
    distance_meters: int
    scheduled_deadline_time: str  # "HH:MM", date comes from the file/context, not this line
    entries: list[RaceEntryCard] = field(default_factory=list)

    @property
    def race_class(self) -> str:
        """`race_class_label` with layout padding removed.

        The B-file pads the class field with spaces for column
        alignment, so the same class reaches the raw label in several
        shapes ("予選", "予 選", "予  選" all occur). Grouping on the raw
        value therefore fragments one class into several subgroups --
        which matters directly for `stability.py`'s per-class subgroup
        sample sizes and for any categorical encoding of race class.

        All whitespace is removed rather than collapsed to one space:
        Japanese does not mark word boundaries with spaces here, so the
        padding carries no information, and removal is what makes the
        variants converge. Compound labels stay distinct
        ("予 選    進入固定" -> "予選進入固定" != "予選").

        Derived on access so it can never disagree with the raw label;
        the raw value is kept because it is what the file actually said.
        Serializers that walk fields (`dataclasses.asdict`) will not
        include this -- call it explicitly if the normalized value is
        needed in the output.
        """
        return re.sub(r"\s+", "", self.race_class_label)


@dataclass(frozen=True)
class ParsedVenueDayCard:
    venue_code: str
    races: list[ParsedRaceCard] = field(default_factory=list)
    # True when the section carried the "data not ready yet" notice
    # instead of a card. Distinguishes a legitimately card-less venue
    # from a parse failure -- see `_DATA_PENDING_MARKER`. Defaults False
    # so existing callers are unaffected.
    data_pending: bool = False
    # True when the section reported the meeting called off entirely
    # (`_VENUE_CANCELLED_MARKER`). Kept separate from `data_pending`
    # because only this one means "no card will ever exist".
    is_cancelled: bool = False
    # Day-N-of-series (節第N日), the meeting date, and the tournament
    # title, all read from this venue's own "day banner" line (see
    # module docstring) rather than assumed from the surrounding file --
    # `meeting_date` is a same-day cross-check against the filename, not
    # a substitute for it. `meeting_title` is `None` for a plain
    # unnamed race day (distinct from `""`: the file did not choose to
    # omit a title, there simply wasn't one on that line) and `""` never
    # occurs (an empty title line, if present, normalizes to nothing).
    # All three default `None` (never parsed for `data_pending` venues,
    # since nothing is written there) so existing callers are
    # unaffected.
    #
    # Validated across the full archive (7,862 files, 96,964 non-
    # `data_pending` venue sections, `is_cancelled` ones included since
    # the header is written before the cancellation notice): the day
    # banner matched for every one of them (0 missed), its date always
    # agreed with the filename's own date (0 mismatches), and the title
    # was 0 or 1 lines every time (94,925 with a title, 2,039 without,
    # never more than one line -- so a title never wraps).
    series_day: int | None = None
    meeting_date: date | None = None
    meeting_title: str | None = None

    @property
    def is_explained_without_races(self) -> bool:
        """Whether an empty `races` list has a known cause.

        A venue section with no races and no flag set is the signature
        of a parse failure, so callers validating an archive should
        treat that case as a defect rather than as missing data. Across
        all 7,862 archived files this is true for every card-less venue
        (152 pending + 2 cancelled, 0 unexplained).
        """
        return self.data_pending or self.is_cancelled


def _parse_entry_line(line: str) -> RaceEntryCard | None:
    if not _ENTRY_LANE_RE.match(line):
        return None
    line = line.ljust(_ENTRY_ROW_LEN)

    def cell(start: int, end: int) -> str:
        return line[start:end].strip()

    try:
        return RaceEntryCard(
            lane_number=int(line[0]),
            racer_registration_number=int(cell(2, 6)),
            racer_name=line[6:10].replace("　", "").strip(),
            age=int(cell(10, 12)),
            branch=cell(12, 14),
            weight_kg=int(cell(14, 16)),
            racer_class=cell(16, 18),
            national_win_rate=float(cell(19, 23)),
            national_second_rate=float(cell(24, 29)),
            local_win_rate=float(cell(30, 34)),
            local_second_rate=float(cell(35, 40)),
            motor_number=int(cell(41, 43)),
            motor_second_rate=float(cell(44, 49)),
            boat_number=int(cell(50, 52)),
            boat_second_rate=float(cell(53, 58)),
            trailing_info_raw=line[59:73],
        )
    except ValueError:
        return None


def parse_b_file_text(text: str) -> list[ParsedVenueDayCard]:
    """Parse full B-file text into one `ParsedVenueDayCard` per venue
    section (`{code}BBGN` ... `{code}BEND`), each holding its races
    with their entry cards (race numbers repeat across venues, so they
    must not be merged into one global dict keyed only by race
    number)."""
    if not text.strip():
        raise BFileParseError("b-file text must not be empty")

    venues: list[ParsedVenueDayCard] = []
    current_venue_code: str | None = None
    current_races: dict[int, ParsedRaceCard] = {}
    current_race_number: int | None = None
    current_data_pending = False
    current_cancelled = False
    current_series_day: int | None = None
    current_meeting_date: date | None = None
    current_meeting_title: str | None = None
    # True once the 番組表 marker has been seen but the day banner
    # (which ends the meeting-metadata block) has not -- only while
    # this holds are non-blank lines collected as the title.
    awaiting_day_banner = False

    def flush_venue() -> None:
        if current_venue_code is not None:
            venues.append(
                ParsedVenueDayCard(
                    venue_code=current_venue_code,
                    races=[current_races[number] for number in sorted(current_races)],
                    data_pending=current_data_pending,
                    is_cancelled=current_cancelled,
                    series_day=current_series_day,
                    meeting_date=current_meeting_date,
                    meeting_title=current_meeting_title,
                )
            )

    for raw_line in text.splitlines():
        venue_match = _VENUE_BEGIN_RE.match(raw_line.strip())
        if venue_match:
            flush_venue()
            current_venue_code = venue_match.group(1)
            current_races = {}
            current_race_number = None
            current_data_pending = False
            current_cancelled = False
            current_series_day = None
            current_meeting_date = None
            current_meeting_title = None
            awaiting_day_banner = False
            continue

        if current_venue_code is not None:
            if _DATA_PENDING_MARKER in raw_line:
                current_data_pending = True
                continue
            if _VENUE_CANCELLED_MARKER in raw_line:
                current_cancelled = True
                continue

        normalized = unicodedata.normalize("NFKC", raw_line).strip()

        # Meeting metadata only ever precedes the first race header, so
        # once a race is underway this block cannot fire again -- avoids
        # any chance of a later line (e.g. inside `trailing_info_raw`)
        # being mistaken for banner/title content. Before the marker is
        # seen, non-matching lines (line 1's truncated summary, blanks)
        # fall through to the header/entry checks below unchanged --
        # only once the marker has fired does every line become metadata
        # and get swallowed here (title text or the closing banner).
        if current_venue_code is not None and current_race_number is None:
            if not awaiting_day_banner:
                if _MEETING_MARKER in normalized:
                    awaiting_day_banner = True
                    continue
            else:
                banner_match = _DAY_BANNER_RE.match(normalized)
                if banner_match:
                    current_series_day = int(banner_match.group(1))
                    current_meeting_date = date(
                        int(banner_match.group(2)),
                        int(banner_match.group(3)),
                        int(banner_match.group(4)),
                    )
                    awaiting_day_banner = False
                else:
                    if normalized:
                        current_meeting_title = normalized
                continue

        header_match = _RACE_HEADER_RE.match(normalized)
        if header_match:
            race_number = int(header_match.group(1))
            current_races[race_number] = ParsedRaceCard(
                race_number=race_number,
                race_class_label=header_match.group(2),
                distance_meters=int(header_match.group(3)),
                scheduled_deadline_time=f"{int(header_match.group(4)):02d}:{header_match.group(5)}",
            )
            current_race_number = race_number
            continue

        if current_race_number is None:
            continue

        entry = _parse_entry_line(raw_line)
        if entry is not None:
            current_races[current_race_number].entries.append(entry)

    flush_venue()
    return venues
