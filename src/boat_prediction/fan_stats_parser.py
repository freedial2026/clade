"""Parser for BOATRACE fan-file (モーターボートファン手帳) fixed-width racer
records, as produced by `fan_file_source.extract_fan_file_text`.

One line per racer, one file per half-year period (第X期). The field
layout was taken from the official specification page
(`https://www.boatrace.jp/owpc/pc/extra/data/layout.html`, linked from
`fan_file_source.py`'s docstring), then cross-validated against every
real record in a full downloaded file (`fan2604.lzh`, 1,644 racers):
the computed field-length total matches the observed 403-character
record length exactly, and every field lands on domain-plausible values
-- `year`/`period` equal the file's own `2026`/`2` on every row,
`class` is always one of `A1`/`A2`/`B1`/`B2`, `current_ability_index`
clusters around `5000` (matching the known 能力指数 convention where
50.00 is the league-average score), course-1 statistics are
systematically stronger than other courses (matching the well-known
inside-lane advantage), and `period_from`/`period_to` equal the file's
own stated calculation window on every row.

**Format era, found by checking record length across all 50 downloaded
files (2001-2026)**: the layout below is 403 characters and holds for
every file from `fan1404.lzh` (2014) onward. Every file before that
(`fan0110.lzh` through `fan1310.lzh`, 2001-2013) is uniformly
400 characters -- a different, not-yet-reverse-engineered legacy
layout. `parse_fan_file_text` raises `FanStatsParseError` on any other
record length rather than guessing, so a caller cannot silently get
misaligned 2001-2013 data out of this parser.

Two fields carry a decimal scale that is *not* the same for both
despite an identical 4-character width, found by checking which
divisor produces realistic values against known real-world stat
ranges: `win_rate` (勝率, a weighted score conventionally 0-9-ish,
national average ~5) is `raw / 100`, while `place_rate` (複勝率, a
genuine top-2-finish percentage, national average ~30-35%) is
`raw / 10` -- the format's authors evidently budgeted 2 decimal places
for the small-range stat and 1 decimal place for the 0-100 stat within
the same 4-digit field.

Dates use two different encodings: `生年月日` (birth date) is Japanese
era format (1 era-letter + 2-digit era-year + MMDD), converted with
`_ERA_BASE_YEAR` below; `算出期間` (calculation period bounds) is a
plain 8-digit `YYYYMMDD`.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

_RECORD_LENGTH = 403
_LEGACY_RECORD_LENGTH = 400

# Gregorian year of era-year 0, so that (base + era_year) gives the
# Gregorian year (e.g. Showa 24 -> 1925 + 24 = 1949).
_ERA_BASE_YEAR = {"M": 1867, "T": 1911, "S": 1925, "H": 1988, "R": 2018}

# (field_name, char_length), in file order. Lengths are in *characters*
# of the already-decoded (Shift-JIS -> str) line, not raw bytes: the
# three fullwidth fields (name_kanji, branch, hometown) are half their
# documented byte length in this space, since each fullwidth character
# decodes from 2 Shift-JIS bytes to 1 Python character.
_BASE_FIELDS: list[tuple[str, int]] = [
    ("registration_number", 4),
    ("name_kanji", 8),
    ("name_kana", 15),
    ("branch", 2),
    ("racer_class", 2),
    ("era", 1),
    ("birth_date_raw", 6),
    ("sex", 1),
    ("age", 2),
    ("height_cm", 3),
    ("weight_kg", 2),
    ("blood_type", 2),
    ("win_rate", 4),
    ("place_rate", 4),
    ("first_place_count", 3),
    ("second_place_count", 3),
    ("start_count", 3),
    ("championship_appearance_count", 2),
    ("championship_win_count", 2),
    ("avg_start_timing", 3),
]
_COURSE_SUMMARY_FIELDS = ("entry_count", "place_rate", "avg_start_timing", "avg_start_rank")
_COURSE_SUMMARY_LENGTHS = (3, 4, 3, 3)
_RANKING_FIELDS: list[tuple[str, int]] = [
    ("prev_class", 2),
    ("prev2_class", 2),
    ("prev3_class", 2),
    ("prev_ability_index", 4),
    ("current_ability_index", 4),
    ("period_year", 4),
    ("period_number", 1),
    ("period_from_raw", 8),
    ("period_to_raw", 8),
    ("training_period", 3),
]
_POSITION_COUNT_LABELS = ("f", "l0", "l1", "k0", "k1", "s0", "s1", "s2")
_TAIL_FIELDS: list[tuple[str, int]] = [
    ("no_course_l0_count", 2),
    ("no_course_l1_count", 2),
    ("no_course_k0_count", 2),
    ("no_course_k1_count", 2),
    ("hometown", 3),
]


def _build_layout() -> list[tuple[str, int]]:
    layout = list(_BASE_FIELDS)
    for course in range(1, 7):
        for name, length in zip(_COURSE_SUMMARY_FIELDS, _COURSE_SUMMARY_LENGTHS, strict=True):
            layout.append((f"course{course}_{name}", length))
    layout += _RANKING_FIELDS
    for course in range(1, 7):
        for position in range(1, 7):
            layout.append((f"course{course}_finish{position}_count", 3))
        for label in _POSITION_COUNT_LABELS:
            layout.append((f"course{course}_{label}_count", 2))
    layout += _TAIL_FIELDS
    return layout


_FIELD_LAYOUT = _build_layout()
assert sum(length for _, length in _FIELD_LAYOUT) == _RECORD_LENGTH


class FanStatsParseError(ValueError):
    """Raised for a record that cannot be parsed without inventing or
    losing data -- an unrecognized record length, or a field that is
    not the digits/blank this layout requires."""


@dataclass(frozen=True)
class CourseSummary:
    """One course's (1-6) aggregate stats for the period."""

    entry_count: int
    place_rate: float | None
    avg_start_timing: float | None
    avg_start_rank: float | None


@dataclass(frozen=True)
class CoursePositionCounts:
    """One course's (1-6) finish-position and irregular-finish counts.

    `finish_counts[0]` is the 1st-place count for this course, ...
    `finish_counts[5]` is the 6th-place count. The irregular-finish
    codes (F/L/K/S) match `kfile_parser`'s `RaceEntryResult.status`
    vocabulary: F = flying start, L0/L1 = late, K0/K1 = disqualified,
    S0/S1/S2 = capsize/sink/other stoppage.
    """

    finish_counts: tuple[int, int, int, int, int, int]
    f_count: int
    l0_count: int
    l1_count: int
    k0_count: int
    k1_count: int
    s0_count: int
    s1_count: int
    s2_count: int


@dataclass(frozen=True)
class ParsedFanRecord:
    """One racer's stats for one half-year period (第X期)."""

    registration_number: int
    name_kanji: str
    name_kana: str
    branch: str
    racer_class: str
    birth_date: dt.date | None
    sex: str
    age: int
    height_cm: int
    weight_kg: int
    blood_type: str
    win_rate: float | None
    place_rate: float | None
    first_place_count: int
    second_place_count: int
    start_count: int
    championship_appearance_count: int
    championship_win_count: int
    avg_start_timing: float | None
    # Index 0 = course 1, ..., index 5 = course 6.
    course_summaries: tuple[CourseSummary, ...]
    prev_class: str
    prev2_class: str
    prev3_class: str
    prev_ability_index: float | None
    current_ability_index: float | None
    period_year: int
    period_number: int
    period_from: dt.date | None
    period_to: dt.date | None
    training_period: int
    course_position_counts: tuple[CoursePositionCounts, ...]
    no_course_l0_count: int
    no_course_l1_count: int
    no_course_k0_count: int
    no_course_k1_count: int
    hometown: str


def _slice_fields(line: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    pos = 0
    for name, length in _FIELD_LAYOUT:
        fields[name] = line[pos : pos + length]
        pos += length
    return fields


def _int(raw: str, field: str) -> int:
    stripped = raw.strip()
    if not stripped.lstrip("-").isdigit():
        raise FanStatsParseError(f"field {field!r}: expected an integer, got {raw!r}")
    return int(stripped)


def _rate(raw: str, divisor: int) -> float | None:
    """`None` for an all-blank field (no data for that period/course),
    matching how the other parsers in this project distinguish a
    missing value from a genuine zero."""
    stripped = raw.strip()
    if not stripped:
        return None
    if not stripped.isdigit():
        return None
    return int(stripped) / divisor


def _era_date(era: str, yymmdd: str, field: str) -> dt.date | None:
    if era not in _ERA_BASE_YEAR:
        raise FanStatsParseError(f"field {field!r}: unknown era code {era!r}")
    if not yymmdd.isdigit() or len(yymmdd) != 6:
        raise FanStatsParseError(f"field {field!r}: malformed era date {yymmdd!r}")
    era_year, month, day = int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:6])
    if month == 0 or day == 0:
        return None
    try:
        return dt.date(_ERA_BASE_YEAR[era] + era_year, month, day)
    except ValueError as exc:
        raise FanStatsParseError(f"field {field!r}: invalid date {era}{yymmdd}: {exc}") from exc


def _iso_date(raw: str, field: str) -> dt.date | None:
    stripped = raw.strip()
    if not stripped:
        return None
    if len(stripped) != 8 or not stripped.isdigit():
        raise FanStatsParseError(f"field {field!r}: expected an 8-digit date, got {raw!r}")
    try:
        return dt.date(int(stripped[:4]), int(stripped[4:6]), int(stripped[6:8]))
    except ValueError as exc:
        raise FanStatsParseError(f"field {field!r}: invalid date {stripped!r}: {exc}") from exc


def _parse_record(line: str) -> ParsedFanRecord:
    f = _slice_fields(line)

    course_summaries = tuple(
        CourseSummary(
            entry_count=_int(f[f"course{c}_entry_count"], f"course{c}_entry_count"),
            place_rate=_rate(f[f"course{c}_place_rate"], 10),
            avg_start_timing=_rate(f[f"course{c}_avg_start_timing"], 100),
            avg_start_rank=_rate(f[f"course{c}_avg_start_rank"], 100),
        )
        for c in range(1, 7)
    )
    course_position_counts = tuple(
        CoursePositionCounts(
            finish_counts=tuple(
                _int(f[f"course{c}_finish{p}_count"], f"course{c}_finish{p}_count")
                for p in range(1, 7)
            ),
            f_count=_int(f[f"course{c}_f_count"], f"course{c}_f_count"),
            l0_count=_int(f[f"course{c}_l0_count"], f"course{c}_l0_count"),
            l1_count=_int(f[f"course{c}_l1_count"], f"course{c}_l1_count"),
            k0_count=_int(f[f"course{c}_k0_count"], f"course{c}_k0_count"),
            k1_count=_int(f[f"course{c}_k1_count"], f"course{c}_k1_count"),
            s0_count=_int(f[f"course{c}_s0_count"], f"course{c}_s0_count"),
            s1_count=_int(f[f"course{c}_s1_count"], f"course{c}_s1_count"),
            s2_count=_int(f[f"course{c}_s2_count"], f"course{c}_s2_count"),
        )
        for c in range(1, 7)
    )

    return ParsedFanRecord(
        registration_number=_int(f["registration_number"], "registration_number"),
        name_kanji=f["name_kanji"].replace("　", "").strip(),
        name_kana=f["name_kana"].strip(),
        branch=f["branch"].strip(),
        racer_class=f["racer_class"].strip(),
        birth_date=_era_date(f["era"], f["birth_date_raw"], "birth_date_raw"),
        sex=f["sex"],
        age=_int(f["age"], "age"),
        height_cm=_int(f["height_cm"], "height_cm"),
        weight_kg=_int(f["weight_kg"], "weight_kg"),
        blood_type=f["blood_type"].strip(),
        win_rate=_rate(f["win_rate"], 100),
        place_rate=_rate(f["place_rate"], 10),
        first_place_count=_int(f["first_place_count"], "first_place_count"),
        second_place_count=_int(f["second_place_count"], "second_place_count"),
        start_count=_int(f["start_count"], "start_count"),
        championship_appearance_count=_int(
            f["championship_appearance_count"], "championship_appearance_count"
        ),
        championship_win_count=_int(f["championship_win_count"], "championship_win_count"),
        avg_start_timing=_rate(f["avg_start_timing"], 100),
        course_summaries=course_summaries,
        prev_class=f["prev_class"].strip(),
        prev2_class=f["prev2_class"].strip(),
        prev3_class=f["prev3_class"].strip(),
        prev_ability_index=_rate(f["prev_ability_index"], 100),
        current_ability_index=_rate(f["current_ability_index"], 100),
        period_year=_int(f["period_year"], "period_year"),
        period_number=_int(f["period_number"], "period_number"),
        period_from=_iso_date(f["period_from_raw"], "period_from_raw"),
        period_to=_iso_date(f["period_to_raw"], "period_to_raw"),
        training_period=_int(f["training_period"], "training_period"),
        course_position_counts=course_position_counts,
        no_course_l0_count=_int(f["no_course_l0_count"], "no_course_l0_count"),
        no_course_l1_count=_int(f["no_course_l1_count"], "no_course_l1_count"),
        no_course_k0_count=_int(f["no_course_k0_count"], "no_course_k0_count"),
        no_course_k1_count=_int(f["no_course_k1_count"], "no_course_k1_count"),
        hometown=f["hometown"].replace("　", "").strip(),
    )


def parse_fan_file_text(text: str) -> list[ParsedFanRecord]:
    """Parse every racer record in one fan-file's decoded text.

    Raises `FanStatsParseError` on the first record whose length is not
    the current-format 403 characters, rather than guessing at the
    pre-2014 400-character layout -- see the module docstring."""
    records = []
    for line in text.splitlines():
        if not line.strip():
            continue
        if len(line) != _RECORD_LENGTH:
            hint = (
                f" (this is the pre-2014 {_LEGACY_RECORD_LENGTH}-character "
                "legacy layout, which this parser does not support)"
                if len(line) == _LEGACY_RECORD_LENGTH
                else ""
            )
            raise FanStatsParseError(
                f"expected a {_RECORD_LENGTH}-character record, got {len(line)}{hint}"
            )
        records.append(_parse_record(line))
    return records
