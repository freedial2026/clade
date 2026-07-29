"""Tests for kfile_parser.py.

Uses a small, hand-written excerpt that mimics the real K-file format's
structure (confirmed by manually downloading and inspecting real files —
see tasks/HANDOFF.md) rather than committing actual downloaded official
data to the repository.
"""

import unittest

from boat_prediction.kfile_parser import KFileParseError, parse_k_file_text

# Two venues, two races each; race 1 of venue 01 has a normal 6-boat
# finish, race 2 of venue 02 includes one disqualified ("S0") entry to
# exercise the defensive non-numeric-finish path.
SAMPLE_TEXT = """STARTK
01KBGN
テスト場［成績］      1/ 1      サンプル

   1R       予選　　　　                 H1800m  晴れ  風  無風　 0m  波　  0cm
  着 艇 登番 　選　手　名　　ﾓｰﾀｰ ﾎﾞｰﾄ 展示 進入 ｽﾀｰﾄﾀｲﾐﾝｸ ﾚｰｽﾀｲﾑ 逃げ
-------------------------------------------------------------------------------
  01  1 1001 山　田　　太　郎 10   11  6.70   1    0.10     1.48.0
  02  2 1002 佐　藤　　次　郎 20   22  6.75   2    0.12     1.49.0
  03  3 1003 鈴　木　　三　郎 30   33  6.80   3    0.14     1.50.0
  04  4 1004 田　中　　四　郎 40   44  6.85   4    0.16     1.51.0
  05  5 1005 高　橋　　五　郎 50   55  6.90   5    0.18     1.52.0
  06  6 1006 渡　辺　　六　郎 60   66  6.95   6    0.20     1.53.0

        単勝     1          150
        複勝     1          110  2          120
        ２連単   1-2        300  人気     2
        ２連複   1-2        250  人気     2
        拡連複   1-2        130  人気     1
                 1-3        180  人気     3
                 2-3        220  人気     4
        ３連単   1-2-3      900  人気     5
        ３連複   1-2-3      400  人気     2


01KEND
02KBGN
別テスト場［成績］    1/ 1      サンプル２

   2R       予選　　　　                 H1800m  曇り  風  南　　 2m  波　  1cm
  着 艇 登番 　選　手　名　　ﾓｰﾀｰ ﾎﾞｰﾄ 展示 進入 ｽﾀｰﾄﾀｲﾐﾝｸ ﾚｰｽﾀｲﾑ
-------------------------------------------------------------------------------
  01  3 2001 中　村　　一　郎 11   12  6.60   3    0.11     1.47.5
  S0  6 2002 小　林　　二　郎 21   23  6.65   6   S .        .  .
  02  1 2003 加　藤　　三　郎 31   32  6.70   1    0.13     1.49.0
  03  4 2004 吉　田　　四　郎 41   42  6.75   4    0.15     1.50.5
  04  2 2005 山　本　　五　郎 51   52  6.80   2    0.17     1.51.5
  05  5 2006 斎　藤　　六　郎 61   62  6.85   5    0.19     1.52.5

        単勝     3           80
        複勝     3           90  1          100
        ２連単   3-1        160  人気     1
        ２連複   1-3        140  人気     1
        拡連複   1-3         70  人気     1
                 3-4        190  人気     2
                 1-4        210  人気     3
        ３連単   3-1-4      500  人気     3
        ３連複   1-3-4      260  人気     2


02KEND
FINALK
"""


class ParseKFileTextTest(unittest.TestCase):
    def test_rejects_empty_text(self) -> None:
        with self.assertRaises(KFileParseError):
            parse_k_file_text("")
        with self.assertRaises(KFileParseError):
            parse_k_file_text("   \n  ")

    def test_finds_both_venues_with_their_own_race_number_1_and_2(self) -> None:
        venues = parse_k_file_text(SAMPLE_TEXT)

        self.assertEqual([v.venue_code for v in venues], ["01", "02"])
        self.assertEqual([r.race_number for r in venues[0].races], [1])
        self.assertEqual([r.race_number for r in venues[1].races], [2])

    def test_race_numbers_do_not_leak_across_venues(self) -> None:
        # Both venues use race number labels independently; this is the
        # exact bug class this test guards against (an earlier version
        # keyed only on race_number and merged every venue's races
        # together).
        venues = parse_k_file_text(SAMPLE_TEXT)

        self.assertEqual(len(venues[0].races[0].entries), 6)
        self.assertEqual(len(venues[1].races[0].entries), 6)

    def test_normal_entry_row_is_parsed_fully(self) -> None:
        venues = parse_k_file_text(SAMPLE_TEXT)
        first = venues[0].races[0].entries[0]

        self.assertEqual(first.finish_status_raw, "01")
        self.assertEqual(first.finish_position, 1)
        self.assertEqual(first.lane_number, 1)
        self.assertEqual(first.racer_registration_number, 1001)
        self.assertEqual(first.racer_name, "山　田　　太　郎")
        self.assertEqual(first.motor_number, 10)
        self.assertEqual(first.boat_number, 11)
        self.assertEqual(first.exhibition_time, 6.70)
        self.assertEqual(first.entry_course, 1)
        self.assertEqual(first.start_timing, 0.10)
        self.assertEqual(first.race_time, "1.48.0")

    def test_disqualified_entry_has_no_finish_position_but_keeps_raw_code(self) -> None:
        venues = parse_k_file_text(SAMPLE_TEXT)
        entries = venues[1].races[0].entries
        disqualified = next(e for e in entries if e.finish_status_raw == "S0")

        self.assertIsNone(disqualified.finish_position)
        self.assertEqual(disqualified.lane_number, 6)
        self.assertEqual(disqualified.racer_registration_number, 2002)
        self.assertEqual(disqualified.racer_name, "小　林　　二　郎")
        self.assertEqual(disqualified.motor_number, 21)
        self.assertEqual(disqualified.boat_number, 23)

    def test_all_six_entries_present_despite_one_being_disqualified(self) -> None:
        venues = parse_k_file_text(SAMPLE_TEXT)
        self.assertEqual(len(venues[1].races[0].entries), 6)

    def test_payout_counts_and_values_match_the_expected_ten_per_race(self) -> None:
        venues = parse_k_file_text(SAMPLE_TEXT)
        payouts = venues[0].races[0].payouts

        self.assertEqual(len(payouts), 10)
        bet_types = [p.bet_type for p in payouts]
        self.assertEqual(
            bet_types,
            ["単勝", "複勝", "複勝", "２連単", "２連複", "拡連複", "拡連複", "拡連複", "３連単", "３連複"],
        )

    def test_win_payout_parsed_correctly(self) -> None:
        venues = parse_k_file_text(SAMPLE_TEXT)
        win = venues[0].races[0].payouts[0]

        self.assertEqual(win.bet_type, "単勝")
        self.assertEqual(win.combination, "1")
        self.assertEqual(win.payout_yen, 150)
        self.assertIsNone(win.popularity_rank)

    def test_trifecta_payout_includes_popularity_rank(self) -> None:
        venues = parse_k_file_text(SAMPLE_TEXT)
        trifecta = next(p for p in venues[0].races[0].payouts if p.bet_type == "３連単")

        self.assertEqual(trifecta.combination, "1-2-3")
        self.assertEqual(trifecta.payout_yen, 900)
        self.assertEqual(trifecta.popularity_rank, 5)

    def test_place_payout_with_two_combinations_on_one_line_both_captured(self) -> None:
        venues = parse_k_file_text(SAMPLE_TEXT)
        place_payouts = [p for p in venues[0].races[0].payouts if p.bet_type == "複勝"]

        self.assertEqual(len(place_payouts), 2)
        self.assertEqual((place_payouts[0].combination, place_payouts[0].payout_yen), ("1", 110))
        self.assertEqual((place_payouts[1].combination, place_payouts[1].payout_yen), ("2", 120))

    def test_wide_quinella_continuation_lines_reuse_the_last_label(self) -> None:
        venues = parse_k_file_text(SAMPLE_TEXT)
        wide_quinella = [p for p in venues[0].races[0].payouts if p.bet_type == "拡連複"]

        self.assertEqual(len(wide_quinella), 3)
        self.assertEqual([p.combination for p in wide_quinella], ["1-2", "1-3", "2-3"])


if __name__ == "__main__":
    unittest.main()
