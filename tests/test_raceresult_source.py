"""Per-race result page parser.

Fixtures are hand-written from the real page's structure rather than
copied from a download, matching every other source test here — the
official body's content is not redistributed in this repository.

Two behaviours carry the weight. A page for a race that has not run, or
for a venue not racing that day, comes back 200 with a shell, so
`has_result` must be false rather than the page reading as "nobody won".
And the winning lane's start-information row has the 決まり手 appended
after the timing, which silently cost lane 1 its start timing until it
was caught — precisely the lane that matters most, and only when it won.
"""

from __future__ import annotations

import datetime as dt
import unittest

from boat_prediction.raceresult_source import (
    RaceResultSourceError,
    parse_raceresult,
    raceresult_url,
)

RACE_DATE = dt.date(2026, 8, 3)

# The winner's row carries the technique after the timing; the others do not.
SAMPLE = """
<html><body>
<table><tr><td>着</td><td>枠</td><td>ボートレーサー</td><td>レースタイム</td></tr>
<tr><td>１</td><td>1</td><td>5111 三村　　岳人</td><td>1'51"4</td></tr>
<tr><td>２</td><td>2</td><td>4060 島田　　一生</td><td>1'52"4</td></tr>
<tr><td>３</td><td>4</td><td>3714 梶原　　　正</td><td>1'54"4</td></tr>
<tr><td>４</td><td>6</td><td>3805 山田　　真聖</td><td>1'55"8</td></tr>
<tr><td>５</td><td>5</td><td>5262 佐藤　　太郎</td><td>1'56"5</td></tr>
<tr><td>Ｆ</td><td>3</td><td>5067 鈴木　　次郎</td><td></td></tr>
</table>
<table><tr><td>スタート情報</td></tr>
<tr><td>1 .05                    \n     逃げ</td></tr>
<tr><td>2 .07</td></tr>
<tr><td>3 F.01</td></tr>
<tr><td>4 .18</td></tr>
<tr><td>5 .21</td></tr>
<tr><td>6 .14</td></tr>
</table>
<table><tr><td>勝式</td><td>組番</td><td>払戻金</td><td>人気</td></tr>
<tr><td>3連単</td><td>1 - 2 - 4</td><td>&yen;730</td><td>2</td></tr>
<tr><td></td><td></td><td></td><td></td></tr>
<tr><td>単勝</td><td>1</td><td>&yen;160</td><td>1</td></tr>
</table>
<table><tr><td>決まり手</td></tr><tr><td>逃げ</td></tr></table>
</body></html>
"""

EMPTY_SHELL = "<html><body><div>no race here</div></body></html>"


class UrlTest(unittest.TestCase):
    def test_url_shape(self) -> None:
        self.assertEqual(
            raceresult_url(RACE_DATE, "23", 1),
            "https://www.boatrace.jp/owpc/pc/race/raceresult?rno=1&jcd=23&hd=20260803",
        )

    def test_rejects_unknown_venue_and_race_number(self) -> None:
        with self.assertRaises(RaceResultSourceError):
            raceresult_url(RACE_DATE, "99", 1)
        with self.assertRaises(RaceResultSourceError):
            raceresult_url(RACE_DATE, "23", 13)


class ParseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.page = parse_raceresult(SAMPLE)

    def test_reads_the_finishing_order_by_lane(self) -> None:
        by_lane = {lane.lane_number: lane.finish_position for lane in self.page.lanes}

        self.assertEqual(by_lane[1], 1)
        self.assertEqual(by_lane[2], 2)
        self.assertEqual(by_lane[4], 3)
        self.assertEqual(self.page.winning_lane, 1)

    def test_a_status_code_is_kept_raw_with_no_placing(self) -> None:
        lane3 = next(lane for lane in self.page.lanes if lane.lane_number == 3)

        self.assertIsNone(lane3.finish_position)
        self.assertEqual(lane3.status, "Ｆ")

    def test_the_winning_lane_keeps_its_start_timing(self) -> None:
        """Its row has the 決まり手 appended after the number, which made
        the whole remainder unparsable and lost the value silently."""
        by_lane = {lane.lane_number: lane.start_timing_sec for lane in self.page.lanes}

        self.assertAlmostEqual(by_lane[1], 0.05)
        self.assertAlmostEqual(by_lane[2], 0.07)

    def test_a_flying_start_is_signed(self) -> None:
        by_lane = {lane.lane_number: lane.start_timing_sec for lane in self.page.lanes}

        self.assertAlmostEqual(by_lane[3], -0.01)

    def test_reads_payouts_with_popularity(self) -> None:
        by_type = {p.bet_type: p for p in self.page.payouts}

        self.assertEqual(by_type["単勝"].combination, "1")
        self.assertEqual(by_type["単勝"].amount_yen, 160)
        self.assertEqual(by_type["3連単"].amount_yen, 730)
        self.assertEqual(by_type["3連単"].popularity_rank, 2)

    def test_reads_the_winning_method(self) -> None:
        self.assertEqual(self.page.winning_method, "逃げ")

    def test_racer_registration_is_taken_from_the_name_cell(self) -> None:
        lane1 = next(lane for lane in self.page.lanes if lane.lane_number == 1)

        self.assertEqual(lane1.racer_registration_number, 5111)


class EmptyPageTest(unittest.TestCase):
    """A 200 with no result is the failure mode that has caught this
    project twice: a venue not racing, or a race not yet run."""

    def test_a_shell_has_no_result_rather_than_no_winner(self) -> None:
        page = parse_raceresult(EMPTY_SHELL)

        self.assertFalse(page.has_result)
        self.assertIsNone(page.winning_lane)
        self.assertEqual(page.lanes, ())

    def test_a_shell_does_not_raise(self) -> None:
        """A capture run reaching a race in progress must not stop."""
        parse_raceresult(EMPTY_SHELL)


if __name__ == "__main__":
    unittest.main()
