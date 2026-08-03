import tempfile
import unittest
from datetime import date
from pathlib import Path

from boat_prediction.odds_source import (
    EARLIEST_RETAINED_DATE,
    EXACTA_BET_TYPE,
    QUINELLA_BET_TYPE,
    OddsSourceError,
    exacta_odds_url,
    fetch_racing_venues,
    fetch_range,
    index_url,
    odds_url,
    parse_exacta_odds,
    parse_win_place_odds,
)

try:
    import bs4  # noqa: F401

    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

# Hand-written excerpt mimicking the real page structure (not real
# downloaded content -- same reasoning as the other parser tests here).
SAMPLE_ODDS_HTML = """
<html><body>
<h3>締切時オッズ</h3>
<div><span class="title7_mainLabel">単勝オッズ</span></div>
<table>
  <thead><tr><th>枠</th><th>ボートレーサー</th><th>単勝オッズ</th></tr></thead>
  <tbody><tr>
    <td class="is-fs14 is-fBold is-boatColor1">1</td>
    <td class="is-p20-0"><span class="is-fs18 is-fBold">齋藤　　和政</span></td>
    <td class="oddsPoint ">5.6</td>
  </tr></tbody>
  <tbody><tr>
    <td class="is-fs14 is-fBold is-boatColor2">2</td>
    <td class="is-p20-0"><span class="is-fs18 is-fBold">原田　才一郎</span></td>
    <td class="oddsPoint ">1.1</td>
  </tr></tbody>
  <tbody><tr>
    <td class="is-fs14 is-fBold is-boatColor3">3</td>
    <td class="is-p20-0"><span class="is-fs18 is-fBold">村上　宗太郎</span></td>
    <td class="oddsPoint ">欠場</td>
  </tr></tbody>
</table>
<div><span class="title7_mainLabel">複勝オッズ</span></div>
<table>
  <thead><tr><th>枠</th><th>ボートレーサー</th><th>複勝オッズ</th></tr></thead>
  <tbody><tr>
    <td class="is-fs14 is-fBold is-boatColor1">1</td>
    <td class="is-p20-0"><span class="is-fs18 is-fBold">齋藤　　和政</span></td>
    <td class="oddsPoint ">2.0-2.8</td>
  </tr></tbody>
  <tbody><tr>
    <td class="is-fs14 is-fBold is-boatColor2">2</td>
    <td class="is-p20-0"><span class="is-fs18 is-fBold">原田　才一郎</span></td>
    <td class="oddsPoint ">1.1-1.4</td>
  </tr></tbody>
</table>
</body></html>
"""

def _exacta_grid(heading: str, cells: list[list[str]]) -> str:
    """Build one 2連単/2連複 grid the way the real page lays it out: the
    header carries the six first-place boats, and each body row holds six
    `(second boat, odds)` pairs, one per header column.

    Built programmatically rather than pasted, so a test can state the
    grid it means and the shape stays obviously rectangular.
    """
    head = "".join(
        f'<th class="is-boatColor{lane}">{lane}</th><th>選手{lane}</th>'
        for lane in range(1, 7)
    )
    body = "".join(
        "<tr>" + "".join(f'<td>{value}</td>' for value in row) + "</tr>" for row in cells
    )
    return (
        f'<div><span class="title7_mainLabel">{heading}</span></div>'
        f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
    )


# first lane 1..6 across the columns; second lane and odds down the rows.
SAMPLE_EXACTA_HTML = (
    "<html><body>"
    + _exacta_grid(
        "2連単オッズ",
        [
            ["2", "1.8", "1", "8.2", "1", "24.0", "1", "43.6", "1", "36.3", "1", "137.4"],
            ["3", "8.4", "3", "35.8", "2", "52.6", "2", "92.7", "2", "68.0", "2", "296.8"],
            ["4", "11.1", "4", "40.9", "4", "62.3", "3", "148.4", "3", "130.1", "3", "436.5"],
            ["5", "8.4", "5", "37.4", "5", "71.3", "5", "87.3", "4", "101.6", "4", "337.2"],
            ["6", "33.5", "6", "88.3", "6", "168.6", "6", "218.2", "6", "195.2", "5", "322.6"],
        ],
    )
    + _exacta_grid(
        "2連複オッズ",
        [
            ["2", "2.0", "", "", "", "", "", "", "", "", "", ""],
            ["3", "5.1", "3", "18.1", "", "", "", "", "", "", "", ""],
            ["4", "6.9", "4", "21.5", "4", "27.8", "", "", "", "", "", ""],
            ["5", "6.6", "5", "16.8", "5", "29.3", "5", "32.6", "", "", "", ""],
            ["6", "22.4", "6", "63.5", "6", "71.4", "6", "71.4", "6", "71.4", "", ""],
        ],
    )
    + "</body></html>"
)

SAMPLE_INDEX_HTML = """
<a href="/owpc/pc/race/racelist?rno=1&jcd=01&hd=20260601">出走表</a>
<a href="/owpc/pc/race/oddstf?rno=1&jcd=01&hd=20260601">オッズ</a>
<a href="/owpc/pc/race/racelist?rno=1&jcd=24&hd=20260601">出走表</a>
<a href="/owpc/pc/race/oddstf?rno=1&jcd=99&hd=20260601">bogus venue</a>
"""


class UrlTest(unittest.TestCase):
    def test_index_url(self) -> None:
        self.assertEqual(
            index_url(date(2026, 6, 1)),
            "https://www.boatrace.jp/owpc/pc/race/index?hd=20260601",
        )

    def test_odds_url(self) -> None:
        self.assertEqual(
            odds_url(date(2026, 6, 1), "24", 1),
            "https://www.boatrace.jp/owpc/pc/race/oddstf?rno=1&jcd=24&hd=20260601",
        )

    def test_rejects_unknown_venue(self) -> None:
        with self.assertRaises(OddsSourceError):
            odds_url(date(2026, 6, 1), "99", 1)

    def test_rejects_race_number_out_of_range(self) -> None:
        with self.assertRaises(OddsSourceError):
            odds_url(date(2026, 6, 1), "24", 13)


class FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class FakeOpener:
    def __init__(self) -> None:
        self.requests: list[str] = []

    def Request(self, url: str, headers: dict | None = None) -> str:
        return url

    def urlopen(self, request: str, timeout: float | None = None) -> FakeResponse:
        self.requests.append(request)
        body = SAMPLE_INDEX_HTML if "race/index" in request else SAMPLE_ODDS_HTML
        return FakeResponse(body.encode("utf-8"))


class FetchRacingVenuesTest(unittest.TestCase):
    def test_extracts_only_valid_venue_codes_deduplicated(self) -> None:
        opener = FakeOpener()

        venues = fetch_racing_venues(date(2026, 6, 1), opener=opener)

        self.assertEqual(venues, ("01", "24"))  # "99" is not a real venue

    def test_wraps_a_failed_fetch(self) -> None:
        class FailingOpener:
            def Request(self, url: str, headers: dict | None = None) -> str:
                return url

            def urlopen(self, request: str, timeout: float | None = None):
                raise OSError("connection refused")

        with self.assertRaises(OddsSourceError):
            fetch_racing_venues(date(2026, 6, 1), opener=FailingOpener())


@unittest.skipUnless(HAS_BS4, "beautifulsoup4 (the 'official-data' extra) is not installed")
class ParseWinPlaceOddsTest(unittest.TestCase):
    def test_parses_win_and_place_odds_per_lane(self) -> None:
        result = parse_win_place_odds(SAMPLE_ODDS_HTML)

        self.assertTrue(result.is_closing)
        self.assertEqual(len(result.entries), 3)

        first = result.entries[0]
        self.assertEqual(first.lane_number, 1)
        self.assertEqual(first.racer_name, "齋藤　　和政")
        self.assertEqual(first.win_odds, 5.6)
        self.assertEqual(first.place_odds_low, 2.0)
        self.assertEqual(first.place_odds_high, 2.8)

    def test_non_numeric_odds_become_none_without_dropping_the_lane(self) -> None:
        result = parse_win_place_odds(SAMPLE_ODDS_HTML)
        absent = result.entries[2]

        self.assertEqual(absent.lane_number, 3)
        self.assertIsNone(absent.win_odds)
        # lane 3 has no place row at all in the sample
        self.assertIsNone(absent.place_odds_low)

    def test_page_without_the_closing_marker_is_flagged_not_closing(self) -> None:
        live = SAMPLE_ODDS_HTML.replace("締切時オッズ", "オッズ")

        self.assertFalse(parse_win_place_odds(live).is_closing)

    def test_page_with_no_odds_table_yields_no_entries(self) -> None:
        result = parse_win_place_odds("<html><body>no odds here</body></html>")

        self.assertEqual(result.entries, ())


class FetchRangeTest(unittest.TestCase):
    def test_refuses_a_start_date_before_the_retention_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(OddsSourceError):
                fetch_range(date(2016, 1, 1), date(2016, 1, 2), Path(tmp), opener=FakeOpener())

    def test_refuses_an_inverted_range(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(OddsSourceError):
                fetch_range(date(2026, 6, 2), date(2026, 6, 1), Path(tmp), opener=FakeOpener())

    def test_rejects_a_delay_under_one_second(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(OddsSourceError):
                fetch_range(
                    date(2026, 6, 1), date(2026, 6, 1), Path(tmp), delay_seconds=0.1,
                    opener=FakeOpener(),
                )

    def test_writes_one_file_per_venue_race_and_is_idempotent(self) -> None:
        opener = FakeOpener()
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)

            written = fetch_range(
                date(2026, 6, 1), date(2026, 6, 1), dest,
                opener=opener, sleep=lambda s: None, log=lambda m: None,
            )

            # 2 venues x 12 races
            self.assertEqual(written, 24)
            self.assertTrue((dest / "20260601" / "01_01.html").exists())
            self.assertTrue((dest / "20260601" / "24_12.html").exists())

            requests_before = len(opener.requests)
            rerun = fetch_range(
                date(2026, 6, 1), date(2026, 6, 1), dest,
                opener=opener, sleep=lambda s: None, log=lambda m: None,
            )

            self.assertEqual(rerun, 0)
            # nothing refetched, not even the daily index (cached in _venues.txt)
            self.assertEqual(len(opener.requests), requests_before)

    def test_earliest_retained_date_is_the_probed_boundary(self) -> None:
        self.assertEqual(EARLIEST_RETAINED_DATE, date(2017, 4, 1))


class UnquotableOddsTest(unittest.TestCase):
    def test_a_zero_cell_parses_as_absent_not_as_zero(self) -> None:
        # The page renders 0.0 for a boat with no quote. Market
        # normalisation divides by the odds, so a stored 0.0 becomes an
        # infinite implied probability rather than a missing one --
        # 2,309 archived snapshots had it.
        from boat_prediction.odds_source import _parse_odds_cell

        self.assertEqual(_parse_odds_cell("0.0"), (None, None))
        self.assertEqual(_parse_odds_cell("0.0-0.0"), (None, None))

    def test_the_floor_of_one_is_still_a_quote(self) -> None:
        from boat_prediction.odds_source import _parse_odds_cell

        self.assertEqual(_parse_odds_cell("1.0"), (1.0, None))
        self.assertEqual(_parse_odds_cell("2.0-2.8"), (2.0, 2.8))


@unittest.skipUnless(HAS_BS4, "beautifulsoup4 not installed")
class ParseExactaOddsTest(unittest.TestCase):
    """The 2連単/2連複 grid.

    The failure this guards against is silent: the grid is read by
    column, so an off-by-one in the header mapping attributes a price to
    the wrong boat and every number downstream stays plausible.
    """

    def _by_type(self, bet_type: str) -> dict[str, float]:
        result = parse_exacta_odds(SAMPLE_EXACTA_HTML)
        return {e.combination: e.odds for e in result.entries if e.bet_type == bet_type}

    def test_reads_all_thirty_exacta_combinations(self) -> None:
        exacta = self._by_type(EXACTA_BET_TYPE)

        self.assertEqual(len(exacta), 30)
        expected = {f"{a}-{b}" for a in range(1, 7) for b in range(1, 7) if a != b}
        self.assertEqual(set(exacta), expected)

    def test_the_column_is_the_first_boat_and_the_cell_is_the_second(self) -> None:
        exacta = self._by_type(EXACTA_BET_TYPE)

        # Column 1, first row: second boat 2 at 1.8 -> 1-2.
        self.assertEqual(exacta["1-2"], 1.8)
        # Column 2, first row: second boat 1 at 8.2 -> 2-1, not 1-2.
        self.assertEqual(exacta["2-1"], 8.2)
        # Last column, last row: first boat 6, second boat 5.
        self.assertEqual(exacta["6-5"], 322.6)

    def test_quinella_is_triangular_and_ascending(self) -> None:
        quinella = self._by_type(QUINELLA_BET_TYPE)

        self.assertEqual(len(quinella), 15)
        for combination in quinella:
            first, second = (int(part) for part in combination.split("-"))
            self.assertLess(first, second, combination)
        self.assertEqual(quinella["1-2"], 2.0)
        self.assertEqual(quinella["2-3"], 18.1)
        self.assertEqual(quinella["5-6"], 71.4)

    def test_blank_cells_produce_no_entry_rather_than_a_zero(self) -> None:
        quinella = self._by_type(QUINELLA_BET_TYPE)

        self.assertNotIn("2-1", quinella)
        self.assertNotIn("6-6", quinella)

    def test_no_combination_pairs_a_boat_with_itself(self) -> None:
        result = parse_exacta_odds(SAMPLE_EXACTA_HTML)

        for entry in result.entries:
            first, second = entry.combination.split("-")
            self.assertNotEqual(first, second, entry.combination)

    def test_a_header_missing_a_boat_shifts_nothing(self) -> None:
        """A 欠場 removes a column. Reading the first lane from the header
        rather than assuming `column + 1` is what keeps the remaining
        columns attributed to the right boats."""
        html = SAMPLE_EXACTA_HTML.replace(
            '<th class="is-boatColor3">3</th><th>選手3</th>',
            "<th>-</th><th>選手3</th>",
            1,
        )

        exacta = {
            e.combination: e.odds
            for e in parse_exacta_odds(html).entries
            if e.bet_type == EXACTA_BET_TYPE
        }

        self.assertNotIn("3-1", exacta)
        self.assertEqual(exacta["4-1"], 43.6)
        self.assertEqual(exacta["1-2"], 1.8)

    def test_a_page_shell_with_no_grid_yields_no_entries(self) -> None:
        result = parse_exacta_odds("<html><body>no odds here</body></html>")

        self.assertEqual(result.entries, ())

    def test_a_live_page_is_flagged_not_closing(self) -> None:
        self.assertFalse(parse_exacta_odds(SAMPLE_EXACTA_HTML).is_closing)
        self.assertTrue(
            parse_exacta_odds(SAMPLE_EXACTA_HTML + "締切時オッズ").is_closing
        )

    def test_the_implied_probabilities_carry_the_expected_overround(self) -> None:
        """A sanity check on the whole grid at once: 30 combinations of a
        pool with a ~25% takeout must sum to roughly 1.33 in 1/odds. A
        column misread would not land there."""
        exacta = self._by_type(EXACTA_BET_TYPE)

        overround = sum(1.0 / odds for odds in exacta.values())

        self.assertGreater(overround, 1.25)
        self.assertLess(overround, 1.45)


class ExactaUrlTest(unittest.TestCase):
    def test_exacta_url(self) -> None:
        self.assertEqual(
            exacta_odds_url(date(2026, 8, 3), "24", 12),
            "https://www.boatrace.jp/owpc/pc/race/odds2tf?rno=12&jcd=24&hd=20260803",
        )

    def test_rejects_unknown_venue(self) -> None:
        with self.assertRaises(OddsSourceError):
            exacta_odds_url(date(2026, 8, 3), "99", 1)


if __name__ == "__main__":
    unittest.main()
