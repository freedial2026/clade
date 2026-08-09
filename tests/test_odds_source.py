import http.client
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from boat_prediction.odds_source import (
    EARLIEST_RETAINED_DATE,
    EXACTA_BET_TYPE,
    QUINELLA_BET_TYPE,
    SANRENPUKU_BET_TYPE,
    TRIFECTA_BET_TYPE,
    WIDE_BET_TYPE,
    KeepAliveSession,
    OddsSourceError,
    exacta_odds_url,
    fetch_racing_venues,
    fetch_range,
    fetch_trifecta_family_range,
    index_url,
    odds_url,
    parse_exacta_odds,
    parse_sanrenpuku_odds,
    parse_trifecta_odds,
    parse_wide_odds,
    parse_win_place_odds,
    sanrenpuku_odds_url,
    trifecta_odds_url,
    wide_odds_url,
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

# Same triangular header-by-lowest-lane grid as 2連複, with a low-high
# odds range in each cell instead of a single value -- structurally
# identical, so 拡連複 reuses the same builder as the exacta/quinella
# fixture above.
SAMPLE_WIDE_HTML = (
    "<html><body>"
    + _exacta_grid(
        "拡連複オッズ",
        [
            ["2", "2.0-2.6", "", "", "", "", "", "", "", "", "", ""],
            ["3", "4.8-6.0", "3", "9.9-12.4", "", "", "", "", "", "", "", ""],
            ["4", "6.2-7.7", "4", "12.6-15.8", "4", "18.4-23.1", "", "", "", "", "", ""],
            ["5", "5.9-7.4", "5", "12.0-15.1", "5", "17.5-22.0", "5", "24.9-31.3", "", "", "", ""],
            ["6", "19.7-24.7", "6", "40.1-50.4", "6", "58.6-73.6", "6", "83.2-100.0", "6", "97.8-100.0", "", ""],
        ],
    )
    + "</body></html>"
)


def _trifecta_html(n_lanes: int) -> str:
    """Build a synthetic 3連単 page for `n_lanes` boats (not 6, to keep the
    fixture small): one column-group per first-place lane, each holding
    `(n_lanes - 1) * (n_lanes - 2)` rows.

    Every group's blocks are the same size (`n_lanes - 2`, since 3rd
    place always excludes exactly {1st, 2nd} regardless of which 2nd was
    picked) so every group has the same number of blocks and they line
    up on the same physical rows -- the mechanism `_trifecta_grid`
    relies on. Verified against real fetched pages to produce the exact
    same combination set a real 4-boat sub-field would (tasks/CURRENT.md,
    2026-08-06); this generator is what proved that understanding, not
    just an assumption encoded into the fixture.
    """
    lanes = list(range(1, n_lanes + 1))
    block_size = n_lanes - 2
    num_blocks = n_lanes - 1
    rows: list[list[tuple[str, ...]]] = []
    for block_idx in range(num_blocks):
        for sub in range(block_size):
            row_cells = []
            for first in lanes:
                remaining_seconds = sorted(lane for lane in lanes if lane != first)
                second = remaining_seconds[block_idx]
                remaining_thirds = sorted(
                    lane for lane in lanes if lane not in (first, second)
                )
                third = remaining_thirds[sub]
                odds = f"{first}{second}{third}.5"
                row_cells.append(
                    (str(second), str(third), odds) if sub == 0 else (str(third), odds)
                )
            rows.append(row_cells)

    head = "".join(
        f'<th class="is-boatColor{lane}">{lane}</th><th>選手{lane}</th>' for lane in lanes
    )
    body = "".join(
        "<tr>" + "".join(f"<td>{v}</td>" for group in row for v in group) + "</tr>"
        for row in rows
    )
    return (
        '<div><span class="title7_mainLabel">3連単オッズ</span></div>'
        f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
    )


def _sanrenpuku_html(n_lanes: int) -> str:
    """Build a synthetic 3連複 page for `n_lanes` boats.

    Unlike 3連単, a block's size here depends on the *middle* value `m`
    alone (`n_lanes - m` candidates for the highest slot) -- the same for
    every group active at that `m` -- which is what keeps every active
    group's block boundaries aligned on the same rows even though
    different groups (different "lowest" lanes) have very different
    total combination counts. A group not yet active at a given `m`
    still emits blank cells matching that row's stride, exactly like the
    real page does for group 5/6 at small `m` (tasks/CURRENT.md).
    """
    lanes = list(range(1, n_lanes + 1))
    rows: list[tuple[dict[int, tuple[str, ...]], int]] = []
    for m in range(2, n_lanes):
        highest_candidates = [lane for lane in lanes if lane > m]
        active_groups = [g for g, low in enumerate(lanes) if low < m]
        for sub, highest in enumerate(highest_candidates):
            row: dict[int, tuple[str, ...]] = {}
            for g in active_groups:
                low = lanes[g]
                odds = f"{low}{m}{highest}.5"
                row[g] = (str(m), str(highest), odds) if sub == 0 else (str(highest), odds)
            rows.append((row, 3 if sub == 0 else 2))

    head = "".join(
        f'<th class="is-boatColor{lane}">{lane}</th><th>選手{lane}</th>' for lane in lanes
    )
    body_rows = []
    for row, stride in rows:
        cells = []
        for g in range(n_lanes):
            cells.extend(row[g] if g in row else [""] * stride)
        body_rows.append("<tr>" + "".join(f"<td>{v}</td>" for v in cells) + "</tr>")
    return (
        '<div><span class="title7_mainLabel">3連複オッズ</span></div>'
        f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>"
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


class FailingOnceOpener(FakeOpener):
    """Raises `OSError` for any request URL containing `fail_fragment`,
    every time -- `_fetch`'s own 3-attempt retry loop still runs and
    still exhausts, so this exercises the real end-to-end failure path
    (retries then `OddsSourceError`), not just the outer catch."""

    def __init__(self, fail_fragment: str) -> None:
        super().__init__()
        self.fail_fragment = fail_fragment

    def urlopen(self, request: str, timeout: float | None = None) -> FakeResponse:
        if self.fail_fragment in request:
            raise OSError("Temporary failure in name resolution")
        return super().urlopen(request, timeout=timeout)


class BulkFetchToleratesFailuresTest(unittest.TestCase):
    """A real unattended multi-day run died outright to one transient DNS
    failure before this existed (tasks/CURRENT.md, 2026-08-09) -- `_fetch`
    already retries 3 times internally, but once it gives up and raises
    `OddsSourceError`, the *caller* has to decide whether that kills the
    whole run. It must not.
    """

    def setUp(self) -> None:
        # _fetch's internal retry backoff (1s then 2s) uses the module's
        # own time.sleep, not the injectable `sleep` parameter -- patch it
        # so a real 3-day-run failure test doesn't itself take 3 seconds.
        patcher = patch("boat_prediction.odds_source.time.sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_failed_race_is_skipped_and_the_run_continues(self) -> None:
        opener = FailingOnceOpener(fail_fragment="rno=3&")
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)

            written = fetch_range(
                date(2026, 6, 1), date(2026, 6, 1), dest,
                opener=opener, sleep=lambda s: None, log=lambda m: None,
            )

            # 2 venues x 12 races, minus race 3 in *each* venue (2 failures).
            self.assertEqual(written, 22)
            self.assertFalse((dest / "20260601" / "01_03.html").exists())
            self.assertTrue((dest / "20260601" / "01_04.html").exists())
            self.assertFalse((dest / "20260601" / "24_03.html").exists())

    def test_a_failed_venue_discovery_day_is_skipped_and_the_next_day_still_runs(
        self,
    ) -> None:
        opener = FailingOnceOpener(fail_fragment="race/index?hd=20260601")
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)

            written = fetch_range(
                date(2026, 6, 1), date(2026, 6, 2), dest,
                opener=opener, sleep=lambda s: None, log=lambda m: None,
            )

            self.assertFalse((dest / "20260601").exists())
            # 06-02's own index request isn't in the failing fragment, so
            # that day proceeds normally: 2 venues x 12 races.
            self.assertEqual(written, 24)
            self.assertTrue((dest / "20260602" / "01_01.html").exists())

    def test_a_rerun_after_a_transient_failure_picks_up_the_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            failing = FailingOnceOpener(fail_fragment="rno=3&")

            fetch_range(
                date(2026, 6, 1), date(2026, 6, 1), dest,
                opener=failing, sleep=lambda s: None, log=lambda m: None,
            )
            self.assertFalse((dest / "20260601" / "01_03.html").exists())

            healthy = FakeOpener()
            written = fetch_range(
                date(2026, 6, 1), date(2026, 6, 1), dest,
                opener=healthy, sleep=lambda s: None, log=lambda m: None,
            )

            # Only the two files (one per venue) the first run couldn't write.
            self.assertEqual(written, 2)
            self.assertTrue((dest / "20260601" / "01_03.html").exists())
            self.assertTrue((dest / "20260601" / "24_03.html").exists())

    def test_run_does_not_raise_even_if_every_request_fails(self) -> None:
        """The degenerate case: a run started right as the network went
        down must still return cleanly (0 written) rather than crash an
        unattended multi-day process on its very first request."""
        opener = FailingOnceOpener(fail_fragment="boatrace.jp")
        with tempfile.TemporaryDirectory() as tmp:
            written = fetch_range(
                date(2026, 6, 1), date(2026, 6, 1), Path(tmp),
                opener=opener, sleep=lambda s: None, log=lambda m: None,
            )

        self.assertEqual(written, 0)


class FetchTrifectaFamilyRangeTest(unittest.TestCase):
    """Same shape as `FetchRangeTest`, but three files per race -- the
    thing actually specific to this function -- rather than one."""

    def test_refuses_a_start_date_before_the_retention_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(OddsSourceError):
            fetch_trifecta_family_range(
                date(2016, 1, 1), date(2016, 1, 2), Path(tmp), opener=FakeOpener()
            )

    def test_refuses_an_inverted_range(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(OddsSourceError):
            fetch_trifecta_family_range(
                date(2026, 6, 2), date(2026, 6, 1), Path(tmp), opener=FakeOpener()
            )

    def test_rejects_a_delay_under_one_second(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(OddsSourceError):
            fetch_trifecta_family_range(
                date(2026, 6, 1), date(2026, 6, 1), Path(tmp), delay_seconds=0.1,
                opener=FakeOpener(),
            )

    def test_writes_three_files_per_venue_race_and_is_idempotent(self) -> None:
        opener = FakeOpener()
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)

            written = fetch_trifecta_family_range(
                date(2026, 6, 1), date(2026, 6, 1), dest,
                opener=opener, sleep=lambda s: None, log=lambda m: None,
            )

            # 2 venues x 12 races x 3 pages
            self.assertEqual(written, 72)
            self.assertTrue((dest / "20260601" / "01_01_odds3t.html").exists())
            self.assertTrue((dest / "20260601" / "01_01_odds3f.html").exists())
            self.assertTrue((dest / "20260601" / "01_01_oddsk.html").exists())
            self.assertTrue((dest / "20260601" / "24_12_oddsk.html").exists())

            requests_before = len(opener.requests)
            rerun = fetch_trifecta_family_range(
                date(2026, 6, 1), date(2026, 6, 1), dest,
                opener=opener, sleep=lambda s: None, log=lambda m: None,
            )

            self.assertEqual(rerun, 0)
            self.assertEqual(len(opener.requests), requests_before)

    def test_shares_the_venues_marker_with_a_prior_win_place_fetch(self) -> None:
        """Pointed at a directory `fetch_range` already populated, the
        venue-discovery request must not be repeated -- one index request
        serves both fetchers."""
        opener = FakeOpener()
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            fetch_range(
                date(2026, 6, 1), date(2026, 6, 1), dest,
                opener=opener, sleep=lambda s: None, log=lambda m: None,
            )
            index_requests_before = sum(1 for r in opener.requests if "race/index" in r)

            fetch_trifecta_family_range(
                date(2026, 6, 1), date(2026, 6, 1), dest,
                opener=opener, sleep=lambda s: None, log=lambda m: None,
            )
            index_requests_after = sum(1 for r in opener.requests if "race/index" in r)

            self.assertEqual(index_requests_after, index_requests_before)

    def test_a_failed_page_is_skipped_and_the_run_continues(self) -> None:
        """Same resilience as `fetch_range` (BulkFetchToleratesFailuresTest),
        pinned separately here because this function's inner loop has an
        extra level of nesting (one more `for` over the three pages) that
        a copy-paste fix could easily miss wrapping."""
        with patch("boat_prediction.odds_source.time.sleep"):
            opener = FailingOnceOpener(fail_fragment="odds3f")
            with tempfile.TemporaryDirectory() as tmp:
                dest = Path(tmp)

                written = fetch_trifecta_family_range(
                    date(2026, 6, 1), date(2026, 6, 1), dest,
                    opener=opener, sleep=lambda s: None, log=lambda m: None,
                )

                # 2 venues x 12 races x 3 pages, minus every odds3f page.
                self.assertEqual(written, 48)
                self.assertTrue((dest / "20260601" / "01_01_odds3t.html").exists())
                self.assertFalse((dest / "20260601" / "01_01_odds3f.html").exists())
                self.assertTrue((dest / "20260601" / "01_01_oddsk.html").exists())


class KeepAliveSessionTest(unittest.TestCase):
    """No real network: `http.client.HTTPSConnection` is mocked, so this
    tests the reuse/reconnect *policy*, not connectivity.

    Measured live against boatrace.jp (tasks/CURRENT.md, 2026-08-06): a
    fresh connection per request -- what `urllib.request.urlopen` does by
    default -- cost ~10-13s a request, on `.21` and two separate VPS
    hosts alike. This class exists to amortize that handshake across many
    requests to the same host, so the property that actually matters is
    "one connection object, reused" -- not any particular HTTP detail.
    """

    @staticmethod
    def _mock_connection(body: bytes = b"<html>ok</html>") -> MagicMock:
        connection = MagicMock()
        response = MagicMock()
        response.read.return_value = body
        connection.getresponse.return_value = response
        return connection

    def test_reuses_the_same_connection_for_the_same_host(self) -> None:
        connection = self._mock_connection()
        with patch(
            "boat_prediction.odds_source.http.client.HTTPSConnection",
            return_value=connection,
        ) as ctor:
            session = KeepAliveSession()
            session.urlopen(session.Request("https://www.boatrace.jp/a"))
            session.urlopen(session.Request("https://www.boatrace.jp/b"))
            session.close()

        ctor.assert_called_once()
        self.assertEqual(connection.request.call_count, 2)

    def test_opens_a_separate_connection_per_host(self) -> None:
        with patch(
            "boat_prediction.odds_source.http.client.HTTPSConnection",
            side_effect=lambda host, timeout: self._mock_connection(),
        ) as ctor:
            session = KeepAliveSession()
            session.urlopen(session.Request("https://www.boatrace.jp/a"))
            session.urlopen(session.Request("https://other.example.com/b"))
            session.close()

        self.assertEqual(ctor.call_count, 2)

    def test_a_stale_connection_reconnects_once_rather_than_raising(self) -> None:
        broken = MagicMock()
        broken.request.side_effect = http.client.RemoteDisconnected("closed")
        healthy = self._mock_connection()
        with patch(
            "boat_prediction.odds_source.http.client.HTTPSConnection",
            side_effect=[broken, healthy],
        ):
            session = KeepAliveSession()
            html = session.urlopen(session.Request("https://www.boatrace.jp/a")).read()

        self.assertEqual(html, b"<html>ok</html>")
        healthy.request.assert_called_once()
        broken.close.assert_called_once()

    def test_close_closes_every_held_connection(self) -> None:
        a = self._mock_connection()
        b = self._mock_connection()
        with patch(
            "boat_prediction.odds_source.http.client.HTTPSConnection", side_effect=[a, b]
        ):
            session = KeepAliveSession()
            session.urlopen(session.Request("https://www.boatrace.jp/x"))
            session.urlopen(session.Request("https://other.example.com/y"))
            session.close()

        a.close.assert_called_once()
        b.close.assert_called_once()

    def test_is_a_context_manager_that_closes_on_exit(self) -> None:
        connection = self._mock_connection()
        with patch(
            "boat_prediction.odds_source.http.client.HTTPSConnection",
            return_value=connection,
        ):
            with KeepAliveSession() as session:
                session.urlopen(session.Request("https://www.boatrace.jp/x"))

        connection.close.assert_called_once()


class BulkFetchDefaultsToKeepAliveTest(unittest.TestCase):
    """The actual point of adding `KeepAliveSession`: a bulk fetch called
    with no `opener` must not silently fall back to a fresh connection
    per request the way it did before."""

    @staticmethod
    def _mock_connection() -> MagicMock:
        connection = MagicMock()
        response = MagicMock()
        response.read.return_value = SAMPLE_INDEX_HTML.encode("utf-8")
        connection.getresponse.return_value = response
        return connection

    def test_fetch_range_reuses_one_connection_for_the_whole_run(self) -> None:
        connection = self._mock_connection()
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch(
                "boat_prediction.odds_source.http.client.HTTPSConnection",
                return_value=connection,
            ) as ctor,
        ):
            fetch_range(
                date(2026, 6, 1), date(2026, 6, 1), Path(tmp),
                sleep=lambda s: None, log=lambda m: None,
            )

        # One connection object for the index request plus every venue's
        # 12 races -- never recreated per request.
        ctor.assert_called_once()
        connection.close.assert_called_once()

    def test_fetch_trifecta_family_range_reuses_one_connection(self) -> None:
        connection = self._mock_connection()
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch(
                "boat_prediction.odds_source.http.client.HTTPSConnection",
                return_value=connection,
            ) as ctor,
        ):
            fetch_trifecta_family_range(
                date(2026, 6, 1), date(2026, 6, 1), Path(tmp),
                sleep=lambda s: None, log=lambda m: None,
            )

        ctor.assert_called_once()
        connection.close.assert_called_once()

    def test_an_explicit_opener_bypasses_keep_alive_entirely(self) -> None:
        """Existing test-double `opener`s (FakeOpener, throughout this
        file) must keep working exactly as before -- this is what proves
        it: HTTPSConnection is never touched when an opener is supplied."""
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("boat_prediction.odds_source.http.client.HTTPSConnection") as ctor,
        ):
            fetch_range(
                date(2026, 6, 1), date(2026, 6, 1), Path(tmp),
                opener=FakeOpener(), sleep=lambda s: None, log=lambda m: None,
            )

        ctor.assert_not_called()


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


class ParseWideOddsTest(unittest.TestCase):
    """拡連複 reuses `_combination_grid` unmodified, so this mostly
    confirms the reuse actually wires up rather than re-testing the grid
    logic `ParseExactaOddsTest` already covers."""

    def test_reads_all_fifteen_pairs_ascending(self) -> None:
        wide = {e.combination: e.odds for e in parse_wide_odds(SAMPLE_WIDE_HTML).entries}

        self.assertEqual(len(wide), 15)
        for combination in wide:
            first, second = (int(part) for part in combination.split("-"))
            self.assertLess(first, second, combination)

    def test_takes_the_low_end_of_the_range(self) -> None:
        """`odds_snapshots.odds` holds one number; `_parse_odds_cell`
        already picks the low end for a range, and this pins that 拡連複
        inherits the same (conservative) choice rather than silently
        switching to the high end."""
        wide = {e.combination: e.odds for e in parse_wide_odds(SAMPLE_WIDE_HTML).entries}

        self.assertEqual(wide["1-2"], 2.0)
        self.assertEqual(wide["5-6"], 97.8)

    def test_a_page_shell_with_no_grid_yields_no_entries(self) -> None:
        result = parse_wide_odds("<html><body>no odds here</body></html>")

        self.assertEqual(result.entries, ())


class ParseTrifectaOddsTest(unittest.TestCase):
    """3連単's grid nests a second dimension inside each first-place
    column via HTML rowspan, which shows up in the parsed tree as a
    shorter `<tr>` -- the thing this class actually needs to guard,
    since a bug there does not raise, it silently drops or
    misattributes a combination.
    """

    def test_reads_every_ordered_combination_for_a_four_boat_field(self) -> None:
        html = "<html><body>" + _trifecta_html(4) + "</body></html>"
        result = parse_trifecta_odds(html)

        combos = {e.combination for e in result.entries}
        expected = {
            f"{a}-{b}-{c}"
            for a in range(1, 5)
            for b in range(1, 5)
            for c in range(1, 5)
            if len({a, b, c}) == 3
        }
        self.assertEqual(len(result.entries), 24)
        self.assertEqual(combos, expected)

    def test_no_combination_repeats_a_lane(self) -> None:
        html = "<html><body>" + _trifecta_html(4) + "</body></html>"

        for entry in parse_trifecta_odds(html).entries:
            lanes = entry.combination.split("-")
            self.assertEqual(len(set(lanes)), 3, entry.combination)

    def test_continuation_rows_carry_forward_the_leading_value(self) -> None:
        """The actual mechanism under test: a row with 2 cells for a group
        must reuse that group's most recently seen leading (second-place)
        value, not silently drop the combination or attribute it to the
        wrong second place."""
        html = "<html><body>" + _trifecta_html(4) + "</body></html>"
        odds = {e.combination: e.odds for e in parse_trifecta_odds(html).entries}

        # First block for lane 1 (second=2): third cycles 3, then 4 on
        # the very next (2-cell) row.
        self.assertEqual(odds["1-2-3"], 123.5)
        self.assertEqual(odds["1-2-4"], 124.5)
        # Second block (second=3): a fresh 3-cell row restates the leading
        # value rather than continuing to reuse 2.
        self.assertEqual(odds["1-3-4"], 134.5)

    def test_a_page_shell_with_no_grid_yields_no_entries(self) -> None:
        result = parse_trifecta_odds("<html><body>no odds here</body></html>")

        self.assertEqual(result.entries, ())

    def test_a_live_page_is_flagged_not_closing(self) -> None:
        html = "<html><body>" + _trifecta_html(4) + "</body></html>"

        self.assertFalse(parse_trifecta_odds(html).is_closing)
        self.assertTrue(parse_trifecta_odds(html + "締切時オッズ").is_closing)


class ParseSanrenpukuOddsTest(unittest.TestCase):
    """3連複's block sizes shrink as the middle value rises (fewer lanes
    remain above it), and different groups (lowest lanes) become active
    at different points -- unlike 3連単, where every block is the same
    size. This is the shape most likely to hide an off-by-one.
    """

    def test_reads_every_unordered_triple_for_a_four_boat_field(self) -> None:
        html = "<html><body>" + _sanrenpuku_html(4) + "</body></html>"
        result = parse_sanrenpuku_odds(html)

        combos = {e.combination for e in result.entries}
        self.assertEqual(len(result.entries), 4)  # C(4,3)
        self.assertEqual(combos, {"1-2-3", "1-2-4", "1-3-4", "2-3-4"})

    def test_keys_are_ascending(self) -> None:
        html = "<html><body>" + _sanrenpuku_html(4) + "</body></html>"

        for entry in parse_sanrenpuku_odds(html).entries:
            low, mid, high = (int(part) for part in entry.combination.split("-"))
            self.assertLess(low, mid, entry.combination)
            self.assertLess(mid, high, entry.combination)

    def test_a_group_not_yet_active_contributes_nothing_early(self) -> None:
        """Lane 4 can never be the *lowest* member of a triple in a
        4-boat field (there is nothing above it to pair with), so it must
        never appear as the first element of a combination."""
        html = "<html><body>" + _sanrenpuku_html(4) + "</body></html>"

        for entry in parse_sanrenpuku_odds(html).entries:
            lowest = int(entry.combination.split("-")[0])
            self.assertLess(lowest, 4, entry.combination)

    def test_a_later_group_starting_mid_table_is_not_misattributed(self) -> None:
        """Group 2 (lowest=2) has no rows at all until m=3, several rows
        into the table -- the exact case that would misattribute a
        blank-padded row's data to the wrong group if the blank padding
        were handled by position instead of by group index."""
        html = "<html><body>" + _sanrenpuku_html(4) + "</body></html>"
        odds = {e.combination: e.odds for e in parse_sanrenpuku_odds(html).entries}

        self.assertEqual(odds["2-3-4"], 234.5)
        self.assertNotIn("2-2-4", odds)

    def test_a_page_shell_with_no_grid_yields_no_entries(self) -> None:
        result = parse_sanrenpuku_odds("<html><body>no odds here</body></html>")

        self.assertEqual(result.entries, ())


class TrifectaFamilyUrlTest(unittest.TestCase):
    def test_trifecta_url(self) -> None:
        self.assertEqual(
            trifecta_odds_url(date(2026, 8, 3), "24", 12),
            "https://www.boatrace.jp/owpc/pc/race/odds3t?rno=12&jcd=24&hd=20260803",
        )

    def test_sanrenpuku_url(self) -> None:
        self.assertEqual(
            sanrenpuku_odds_url(date(2026, 8, 3), "24", 12),
            "https://www.boatrace.jp/owpc/pc/race/odds3f?rno=12&jcd=24&hd=20260803",
        )

    def test_wide_url(self) -> None:
        self.assertEqual(
            wide_odds_url(date(2026, 8, 3), "24", 12),
            "https://www.boatrace.jp/owpc/pc/race/oddsk?rno=12&jcd=24&hd=20260803",
        )

    def test_rejects_unknown_venue(self) -> None:
        for url_fn in (trifecta_odds_url, sanrenpuku_odds_url, wide_odds_url):
            with self.assertRaises(OddsSourceError):
                url_fn(date(2026, 8, 3), "99", 1)


class BetTypeConstantsTest(unittest.TestCase):
    """These must equal `combination_model`/`evaluate_bet_types`'s
    `BetTypeSpec.key` values -- the whole point of using English
    constants here rather than the K-file's Japanese labels."""

    def test_bet_type_constants_match_the_spec_keys(self) -> None:
        self.assertEqual(TRIFECTA_BET_TYPE, "trifecta")
        self.assertEqual(SANRENPUKU_BET_TYPE, "sanrenpuku")
        self.assertEqual(WIDE_BET_TYPE, "wide")


if __name__ == "__main__":
    unittest.main()
