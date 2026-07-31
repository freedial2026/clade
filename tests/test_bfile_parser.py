import unittest
from datetime import date

from boat_prediction.bfile_parser import (
    BFileParseError,
    ParsedRaceCard,
    parse_b_file_text,
)

# Small hand-written excerpt mimicking the real structure (not real
# downloaded content -- same reasoning as tests/test_kfile_parser.py):
# two venues, one race each, 2 entries per race (real races have 6).
# Row layout verified column-by-column against a real downloaded day
# (2026-06-01) -- see bfile_parser.py's module docstring.
SAMPLE_B_FILE_TEXT = """\
STARTB
24BBGN
ボートレース大村   　６月　１日  ミッドナイトボートレ  第　１日

　１Ｒ  予選　　　　          Ｈ１８００ｍ  電話投票締切予定１７：４１
-------------------------------------------------------------------------------
艇 選手 選手  年 支 体級    全国      当地     モーター   ボート   今節成績  早
番 登番  名   齢 部 重別 勝率  2率  勝率  2率  NO  2率  NO  2率  １２３４５６見
-------------------------------------------------------------------------------
1 3637齋藤和政55愛知54B1 4.15 21.18 4.05 13.64 72  0.00 67  0.00              9
2 5028原田才一29福岡51A1 6.71 51.69 6.64 40.00 23 11.11 65  0.00              8
24BEND
08BBGN
ボートレース常滑   　６月　１日  第９回愛知・名古屋ア  第　６日

　１Ｒ  朝トコ小判Ｒ          Ｈ１８００ｍ  電話投票締切予定１０：３２
-------------------------------------------------------------------------------
艇 選手 選手  年 支 体級    全国      当地     モーター   ボート   今節成績  早
番 登番  名   齢 部 重別 勝率  2率  勝率  2率  NO  2率  NO  2率  １２３４５６見
-------------------------------------------------------------------------------
1 4015前野竜一46山口59A2 6.04 38.89 6.43 39.13 21 38.97 65 31.11 4322332 54  10
08BEND
"""


class ParseBFileTextTest(unittest.TestCase):
    def test_rejects_empty_input(self) -> None:
        with self.assertRaises(BFileParseError):
            parse_b_file_text("")

    def test_splits_venues_by_bbgn_bend_markers(self) -> None:
        venues = parse_b_file_text(SAMPLE_B_FILE_TEXT)

        self.assertEqual([v.venue_code for v in venues], ["24", "08"])

    def test_parses_race_header_time_and_distance(self) -> None:
        venues = parse_b_file_text(SAMPLE_B_FILE_TEXT)
        race = venues[0].races[0]

        self.assertEqual(race.race_number, 1)
        self.assertEqual(race.race_class_label, "予選")
        self.assertEqual(race.distance_meters, 1800)
        self.assertEqual(race.scheduled_deadline_time, "17:41")

    def test_parses_entry_rate_and_number_fields(self) -> None:
        venues = parse_b_file_text(SAMPLE_B_FILE_TEXT)
        entry = venues[0].races[0].entries[0]

        self.assertEqual(entry.lane_number, 1)
        self.assertEqual(entry.racer_registration_number, 3637)
        self.assertEqual(entry.racer_name, "齋藤和政")
        self.assertEqual(entry.age, 55)
        self.assertEqual(entry.branch, "愛知")
        self.assertEqual(entry.weight_kg, 54)
        self.assertEqual(entry.racer_class, "B1")
        self.assertEqual(entry.national_win_rate, 4.15)
        self.assertEqual(entry.national_second_rate, 21.18)
        self.assertEqual(entry.local_win_rate, 4.05)
        self.assertEqual(entry.local_second_rate, 13.64)
        self.assertEqual(entry.motor_number, 72)
        self.assertEqual(entry.motor_second_rate, 0.0)
        self.assertEqual(entry.boat_number, 67)
        self.assertEqual(entry.boat_second_rate, 0.0)

    def test_handles_race_class_labels_with_embedded_spaces(self) -> None:
        # "朝トコ小判Ｒ" is a real class label seen in the wild; the
        # important regression case is a label with an internal run of
        # spaces (e.g. "予選     進入固定"), which earlier broke the
        # non-greedy \S+? version of the header regex.
        venues = parse_b_file_text(SAMPLE_B_FILE_TEXT)
        race = venues[1].races[0]

        self.assertEqual(race.race_class_label, "朝トコ小判R")  # Ｒ -> R via NFKC
        self.assertEqual(race.scheduled_deadline_time, "10:32")

    def test_long_trailing_info_does_not_break_the_fixed_rate_fields(self) -> None:
        # Regression: a longer current-series-results run (more than 6
        # chars, seen for series with makeup heats) must not corrupt
        # the preceding fixed-position rate/number fields.
        venues = parse_b_file_text(SAMPLE_B_FILE_TEXT)
        entry = venues[1].races[0].entries[0]

        self.assertEqual(entry.boat_number, 65)
        self.assertEqual(entry.boat_second_rate, 31.11)
        self.assertEqual(entry.trailing_info_raw, "4322332 54  10")

    def test_venue_sections_do_not_merge_race_numbers(self) -> None:
        venues = parse_b_file_text(SAMPLE_B_FILE_TEXT)

        self.assertEqual(len(venues[0].races), 1)
        self.assertEqual(len(venues[1].races), 1)
        self.assertEqual(len(venues[0].races[0].entries), 2)
        self.assertEqual(len(venues[1].races[0].entries), 1)

    def test_normal_venue_is_not_flagged_data_pending(self) -> None:
        venues = parse_b_file_text(SAMPLE_B_FILE_TEXT)

        self.assertFalse(venues[0].data_pending)
        self.assertFalse(venues[1].data_pending)


# A venue published as a placeholder before its card was finalized:
# the section exists but carries a notice instead of any race. Structure
# copied from a real occurrence (2021-02-02 venue 02) -- see
# bfile_parser._DATA_PENDING_MARKER.
PENDING_VENUE_TEXT = """\
STARTB
02BBGN

ボートレース戸　田
この場のデータ更新は、いましばらくお待ちください。

02BEND
"""


class DataPendingVenueTest(unittest.TestCase):
    def test_placeholder_venue_is_flagged_not_dropped(self) -> None:
        venues = parse_b_file_text(PENDING_VENUE_TEXT)

        self.assertEqual(len(venues), 1)
        self.assertEqual(venues[0].venue_code, "02")
        self.assertTrue(venues[0].data_pending)

    def test_placeholder_venue_has_no_races(self) -> None:
        # The point of the flag: zero races here is a real publication
        # state, not a parse failure, and callers must be able to tell
        # the two apart before counting it as missing data.
        venues = parse_b_file_text(PENDING_VENUE_TEXT)

        self.assertEqual(venues[0].races, [])

    def test_flag_does_not_leak_into_the_next_venue(self) -> None:
        venues = parse_b_file_text(PENDING_VENUE_TEXT + SAMPLE_B_FILE_TEXT)

        self.assertTrue(venues[0].data_pending)
        self.assertEqual([v.data_pending for v in venues[1:]], [False, False])

    def test_pending_is_not_reported_as_cancelled(self) -> None:
        # A pending card may still be published; a cancelled meeting
        # never will be. Conflating them would misreport a gap in this
        # archive snapshot as a day that legitimately has no card.
        venues = parse_b_file_text(PENDING_VENUE_TEXT)

        self.assertFalse(venues[0].is_cancelled)


# A meeting called off entirely, after an otherwise complete header.
# Structure copied from a real occurrence (2006-09-17 venues 20 and 23).
CANCELLED_VENUE_TEXT = """\
STARTB
20BBGN
若　松　競艇場   　９月１７日  スポーツ報知杯争奪戦  第　５日

                            ＊＊＊　番組表　＊＊＊

          スポーツ報知杯争奪戦

   第　５日          ２００６年　９月１７日                       若　松　競艇場

開催は中止となりました。

20BEND
"""


class CancelledVenueTest(unittest.TestCase):
    def test_cancelled_venue_is_flagged_not_dropped(self) -> None:
        venues = parse_b_file_text(CANCELLED_VENUE_TEXT)

        self.assertEqual(len(venues), 1)
        self.assertEqual(venues[0].venue_code, "20")
        self.assertTrue(venues[0].is_cancelled)
        self.assertEqual(venues[0].races, [])

    def test_cancelled_is_not_reported_as_pending(self) -> None:
        venues = parse_b_file_text(CANCELLED_VENUE_TEXT)

        self.assertFalse(venues[0].data_pending)

    def test_normal_venue_is_not_flagged_cancelled(self) -> None:
        venues = parse_b_file_text(SAMPLE_B_FILE_TEXT)

        self.assertEqual([v.is_cancelled for v in venues], [False, False])


# Real header structure preceding a venue's first race (2026-06-01
# Omura, venue 24) -- marker line, title line, day banner, disclaimer,
# then the first race header. Column layout of the entry row is not
# the focus here (2 entries; real races have 6), the metadata block is.
MEETING_METADATA_TEXT = """\
STARTB
24BBGN
ボートレース大村   　６月　１日  ミッドナイトボートレ  第　１日

                            ＊＊＊　番組表　＊＊＊

          ミッドナイトボートレースｉｎ大村　２

   第　１日          ２０２６年　６月　１日                  ボートレース大　村

               −内容については主催者発行のものと照合して下さい−

　１Ｒ  予選　　　　          Ｈ１８００ｍ  電話投票締切予定１７：４１
-------------------------------------------------------------------------------
艇 選手 選手  年 支 体級    全国      当地     モーター   ボート   今節成績  早
番 登番  名   齢 部 重別 勝率  2率  勝率  2率  NO  2率  NO  2率  １２３４５６見
-------------------------------------------------------------------------------
1 3637齋藤和政55愛知54B1 4.15 21.18 4.05 13.64 72  0.00 67  0.00              9
2 5028原田才一29福岡51A1 6.71 51.69 6.64 40.00 23 11.11 65  0.00              8
24BEND
"""

# Same structure but the title line is blank -- an ordinary race day
# with no named tournament (this shape occurs in the real archive).
MEETING_NO_TITLE_TEXT = """\
STARTB
23BBGN
唐　津　競艇場   　６月１５日                        第　１日

                            ＊＊＊　番組表　＊＊＊



   第　１日          ２００５年　６月１５日                       唐　津　競艇場

               −内容については主催者発行のものと照合して下さい−

23BEND
"""


class MeetingMetadataTest(unittest.TestCase):
    def test_parses_series_day_date_and_title_from_the_day_banner(self) -> None:
        venues = parse_b_file_text(MEETING_METADATA_TEXT)
        venue = venues[0]

        self.assertEqual(venue.series_day, 1)
        self.assertEqual(venue.meeting_date, date(2026, 6, 1))
        # ｉｎ -> in: NFKC normalizes full-width Latin letters too.
        self.assertEqual(venue.meeting_title, "ミッドナイトボートレースin大村 2")

    def test_race_parsing_is_unaffected_by_the_metadata_block(self) -> None:
        # The metadata block sits between BBGN and the first race header;
        # it must not swallow or corrupt the race that follows it.
        venues = parse_b_file_text(MEETING_METADATA_TEXT)
        race = venues[0].races[0]

        self.assertEqual(race.race_number, 1)
        self.assertEqual(len(race.entries), 2)
        self.assertEqual(race.entries[0].racer_name, "齋藤和政")

    def test_missing_title_line_leaves_meeting_title_none(self) -> None:
        venues = parse_b_file_text(MEETING_NO_TITLE_TEXT)
        venue = venues[0]

        self.assertEqual(venue.series_day, 1)
        self.assertEqual(venue.meeting_date, date(2005, 6, 15))
        self.assertIsNone(venue.meeting_title)

    def test_no_marker_at_all_leaves_all_three_fields_none(self) -> None:
        # SAMPLE_B_FILE_TEXT has no 番組表 marker or day banner at all
        # (only the truncated one-line summary) -- the common shape for
        # hand-written test fixtures elsewhere in this file. Metadata
        # fields must default to None rather than misparsing that
        # summary line.
        venues = parse_b_file_text(SAMPLE_B_FILE_TEXT)

        for venue in venues:
            self.assertIsNone(venue.series_day)
            self.assertIsNone(venue.meeting_date)
            self.assertIsNone(venue.meeting_title)

    def test_cancelled_venue_still_carries_meeting_metadata(self) -> None:
        # The day banner is written before the cancellation notice, so
        # a cancelled meeting's series_day/date/title are still real
        # data, not just an artifact of a card that was never finished.
        venues = parse_b_file_text(CANCELLED_VENUE_TEXT)
        venue = venues[0]

        self.assertEqual(venue.series_day, 5)
        self.assertEqual(venue.meeting_date, date(2006, 9, 17))
        self.assertEqual(venue.meeting_title, "スポーツ報知杯争奪戦")

    def test_pending_venue_has_no_meeting_metadata(self) -> None:
        # Nothing is written yet for a data_pending venue, so there is
        # no banner to read day-N-of-series or the date from.
        venues = parse_b_file_text(PENDING_VENUE_TEXT)
        venue = venues[0]

        self.assertIsNone(venue.series_day)
        self.assertIsNone(venue.meeting_date)
        self.assertIsNone(venue.meeting_title)

    def test_metadata_does_not_leak_between_venues(self) -> None:
        venues = parse_b_file_text(MEETING_METADATA_TEXT + SAMPLE_B_FILE_TEXT)

        self.assertEqual(venues[0].series_day, 1)
        for other in venues[1:]:
            self.assertIsNone(other.series_day)
            self.assertIsNone(other.meeting_date)
            self.assertIsNone(other.meeting_title)


class ExplainedWithoutRacesTest(unittest.TestCase):
    def test_both_known_causes_count_as_explained(self) -> None:
        pending = parse_b_file_text(PENDING_VENUE_TEXT)[0]
        cancelled = parse_b_file_text(CANCELLED_VENUE_TEXT)[0]

        self.assertTrue(pending.is_explained_without_races)
        self.assertTrue(cancelled.is_explained_without_races)

    def test_unflagged_empty_venue_is_not_explained(self) -> None:
        # This is the signature of a parse failure, and the reason the
        # flags exist: callers must be able to tell it apart from a
        # venue that legitimately has no card.
        empty = parse_b_file_text("STARTB\n07BBGN\n\n07BEND\n")[0]

        self.assertEqual(empty.races, [])
        self.assertFalse(empty.is_explained_without_races)


class RaceClassNormalizationTest(unittest.TestCase):
    def test_padded_label_variants_normalize_to_one_class(self) -> None:
        # The B-file pads the class field for column alignment, so the
        # same class reaches race_class_label in several shapes.
        # Grouping on the raw value would split one class into three.
        labels = ["予選", "予 選", "予  選"]
        normalized = {
            ParsedRaceCard(
                race_number=1,
                race_class_label=label,
                distance_meters=1800,
                scheduled_deadline_time="17:41",
            ).race_class
            for label in labels
        }

        self.assertEqual(normalized, {"予選"})

    def test_compound_label_stays_distinct_from_its_prefix(self) -> None:
        compound = ParsedRaceCard(
            race_number=1,
            race_class_label="予 選    進入固定",
            distance_meters=1800,
            scheduled_deadline_time="17:41",
        )

        self.assertEqual(compound.race_class, "予選進入固定")
        self.assertNotEqual(compound.race_class, "予選")

    def test_raw_label_is_preserved(self) -> None:
        race = ParsedRaceCard(
            race_number=1,
            race_class_label="予  選",
            distance_meters=1800,
            scheduled_deadline_time="17:41",
        )

        self.assertEqual(race.race_class_label, "予  選")
        self.assertEqual(race.race_class, "予選")


if __name__ == "__main__":
    unittest.main()
