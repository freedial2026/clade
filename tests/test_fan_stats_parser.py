"""Tests for boat_prediction.fan_stats_parser.

Records are built programmatically from `_FIELD_LAYOUT` (not real
downloaded content -- same reasoning as tests/test_bfile_parser.py):
`_build_record` places each field's default raw text at its real
column offset, so a test only needs to name the fields it cares about
as overrides. Defaults and the field layout itself were validated
against a real downloaded file (fan2604.lzh, 1,644 records, zero parse
failures) -- see fan_stats_parser.py's module docstring for that
evidence.
"""

from __future__ import annotations

import datetime as dt
import unittest

from boat_prediction.fan_stats_parser import (
    _FIELD_LAYOUT,
    FanStatsParseError,
    parse_fan_file_text,
)

_GENERIC_DEFAULTS = {
    "registration_number": "1234",
    "name_kanji": "山田太郎" + "　" * 4,
    "name_kana": "ﾔﾏﾀﾞ ﾀﾛｳ" + " " * 7,
    "branch": "東京",
    "racer_class": "A1",
    "era": "S",
    "birth_date_raw": "600101",
    "sex": "1",
    "age": "40",
    "height_cm": "170",
    "weight_kg": "52",
    "blood_type": "A ",
    "win_rate": "0550",
    "place_rate": "0350",
    "first_place_count": "010",
    "second_place_count": "008",
    "start_count": "050",
    "championship_appearance_count": "01",
    "championship_win_count": "00",
    "avg_start_timing": "015",
    "entry_count": "010",
    "avg_start_rank": "200",
    "prev_class": "A1",
    "prev2_class": "A1",
    "prev3_class": "A1",
    "prev_ability_index": "0500",
    "current_ability_index": "0500",
    "period_year": "2026",
    "period_number": "2",
    "period_from_raw": "20251101",
    "period_to_raw": "20260430",
    "training_period": "100",
    "no_course_l0_count": "00",
    "no_course_l1_count": "00",
    "no_course_k0_count": "00",
    "no_course_k1_count": "00",
    "hometown": "東京　",
    **{f"{label}_count": "00" for label in ("f", "l0", "l1", "k0", "k1", "s0", "s1", "s2")},
    **{f"finish{p}_count": "001" for p in range(1, 7)},
}


def _generic_name(field_name: str) -> str:
    """`"course3_place_rate"` -> `"place_rate"`; anything else is
    already course-independent and passes through unchanged."""
    if field_name.startswith("course"):
        return field_name.split("_", 1)[1]
    return field_name


def _build_record(**overrides: str) -> str:
    """Build one syntactically valid 403-character fan-file record.
    `overrides` keys are full field names from `_FIELD_LAYOUT`
    (e.g. `course3_place_rate`, not just `place_rate`) so a caller can
    target one specific course; every other field gets its generic
    default, left/right-padded to that field's exact width by the
    caller (this raises via the length assert below if it isn't)."""
    parts = []
    for name, length in _FIELD_LAYOUT:
        raw = overrides.get(name, _GENERIC_DEFAULTS[_generic_name(name)])
        assert len(raw) == length, f"{name}: {raw!r} is {len(raw)} chars, expected {length}"
        parts.append(raw)
    return "".join(parts)


class ParseFanFileTextTest(unittest.TestCase):
    def test_rejects_a_record_of_the_legacy_pre_2014_length(self) -> None:
        with self.assertRaises(FanStatsParseError):
            parse_fan_file_text("0" * 400)

    def test_rejects_a_record_of_an_unrecognized_length(self) -> None:
        with self.assertRaises(FanStatsParseError):
            parse_fan_file_text("0" * 50)

    def test_blank_lines_are_skipped(self) -> None:
        text = _build_record() + "\n\n" + _build_record()

        records = parse_fan_file_text(text)

        self.assertEqual(len(records), 2)

    def test_parses_identity_and_physical_fields(self) -> None:
        record = _build_record()

        parsed = parse_fan_file_text(record)[0]

        self.assertEqual(parsed.registration_number, 1234)
        self.assertEqual(parsed.name_kanji, "山田太郎")
        self.assertEqual(parsed.name_kana, "ﾔﾏﾀﾞ ﾀﾛｳ")
        self.assertEqual(parsed.branch, "東京")
        self.assertEqual(parsed.racer_class, "A1")
        self.assertEqual(parsed.sex, "1")
        self.assertEqual(parsed.age, 40)
        self.assertEqual(parsed.height_cm, 170)
        self.assertEqual(parsed.weight_kg, 52)
        self.assertEqual(parsed.blood_type, "A")

    def test_showa_era_birth_date_converts_to_a_gregorian_date(self) -> None:
        # Showa 60-01-01 -> 1925 (era base) + 60 = 1985.
        record = _build_record(era="S", birth_date_raw="600101")

        parsed = parse_fan_file_text(record)[0]

        self.assertEqual(parsed.birth_date, dt.date(1985, 1, 1))

    def test_heisei_era_birth_date_converts_to_a_gregorian_date(self) -> None:
        # Heisei 05-04-17 -> 1988 (era base) + 5 = 1993.
        record = _build_record(era="H", birth_date_raw="050417")

        parsed = parse_fan_file_text(record)[0]

        self.assertEqual(parsed.birth_date, dt.date(1993, 4, 17))

    def test_unknown_era_code_raises(self) -> None:
        record = _build_record(era="X")

        with self.assertRaises(FanStatsParseError):
            parse_fan_file_text(record)

    def test_win_rate_and_place_rate_use_different_decimal_scales(self) -> None:
        # win_rate is raw/100 (a 0-9-ish weighted score); place_rate is
        # raw/10 (a genuine 0-100 percentage) -- same 4-char width, two
        # different real-world value ranges. See the module docstring.
        record = _build_record(win_rate="0824", place_rate="0800")

        parsed = parse_fan_file_text(record)[0]

        self.assertEqual(parsed.win_rate, 8.24)
        self.assertEqual(parsed.place_rate, 80.0)

    def test_blank_rate_field_is_none_not_zero(self) -> None:
        record = _build_record(win_rate="    ")

        parsed = parse_fan_file_text(record)[0]

        self.assertIsNone(parsed.win_rate)

    def test_course_summaries_are_ordered_course_1_through_6(self) -> None:
        record = _build_record(
            course1_entry_count="099",
            course6_entry_count="001",
        )

        parsed = parse_fan_file_text(record)[0]

        self.assertEqual(len(parsed.course_summaries), 6)
        self.assertEqual(parsed.course_summaries[0].entry_count, 99)
        self.assertEqual(parsed.course_summaries[5].entry_count, 1)

    def test_course_position_counts_capture_finish_and_irregular_counts(self) -> None:
        record = _build_record(
            course1_finish1_count="042",
            course1_f_count="03",
            course1_s2_count="01",
        )

        parsed = parse_fan_file_text(record)[0]
        course1 = parsed.course_position_counts[0]

        self.assertEqual(course1.finish_counts[0], 42)
        self.assertEqual(course1.f_count, 3)
        self.assertEqual(course1.s2_count, 1)

    def test_parses_period_and_ranking_fields(self) -> None:
        record = _build_record()

        parsed = parse_fan_file_text(record)[0]

        self.assertEqual(parsed.period_year, 2026)
        self.assertEqual(parsed.period_number, 2)
        self.assertEqual(parsed.period_from, dt.date(2025, 11, 1))
        self.assertEqual(parsed.period_to, dt.date(2026, 4, 30))
        self.assertEqual(parsed.training_period, 100)
        self.assertEqual(parsed.current_ability_index, 5.0)

    def test_hometown_strips_ideographic_space_padding(self) -> None:
        record = _build_record(hometown="大　阪")

        parsed = parse_fan_file_text(record)[0]

        self.assertEqual(parsed.hometown, "大阪")

    def test_non_digit_in_a_count_field_raises(self) -> None:
        record = _build_record(start_count="?50")

        with self.assertRaises(FanStatsParseError):
            parse_fan_file_text(record)


if __name__ == "__main__":
    unittest.main()
