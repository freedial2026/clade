import unittest
from datetime import datetime

from boat_prediction.odds import (
    OddsError,
    OddsSnapshot,
    get_closing_odds,
    get_prediction_time_odds,
)

from _helpers import make_temporal_record
from _helpers import utc as _utc


def _snapshot(
    odds: float,
    available_at: datetime,
    *,
    race_key: str = "2026-07-29-01-01",
    lane_number: int = 1,
    is_closing: bool = False,
) -> OddsSnapshot:
    return OddsSnapshot(
        race_key=race_key,
        lane_number=lane_number,
        odds=odds,
        source="official",
        temporal=make_temporal_record(available_at),
        is_closing=is_closing,
    )


class OddsSnapshotTest(unittest.TestCase):
    def test_rejects_non_positive_odds(self) -> None:
        with self.assertRaises(OddsError):
            _snapshot(0.0, _utc(2026, 7, 29, 9, 0))

    def test_rejects_invalid_lane_number(self) -> None:
        with self.assertRaises(OddsError):
            _snapshot(2.5, _utc(2026, 7, 29, 9, 0), lane_number=7)


class GetPredictionTimeOddsTest(unittest.TestCase):
    def test_missing_snapshot_is_explicit(self) -> None:
        result = get_prediction_time_odds([], "race-1", 1, _utc(2026, 7, 29, 9, 0))

        self.assertFalse(result.found)
        self.assertIsNone(result.snapshot)
        self.assertIsNotNone(result.reason)

    def test_picks_the_latest_available_snapshot(self) -> None:
        early = _snapshot(3.0, _utc(2026, 7, 29, 8, 0))
        later = _snapshot(2.8, _utc(2026, 7, 29, 9, 0))

        result = get_prediction_time_odds([early, later], "2026-07-29-01-01", 1, _utc(2026, 7, 29, 9, 30))

        self.assertTrue(result.found)
        self.assertEqual(result.snapshot.odds, 2.8)

    def test_never_selects_a_snapshot_published_after_prediction_at(self) -> None:
        early = _snapshot(3.0, _utc(2026, 7, 29, 8, 0))
        future = _snapshot(1.5, _utc(2026, 7, 29, 10, 0))  # not yet available

        result = get_prediction_time_odds(
            [early, future], "2026-07-29-01-01", 1, _utc(2026, 7, 29, 9, 0)
        )

        self.assertTrue(result.found)
        self.assertEqual(result.snapshot.odds, 3.0)  # the future one must never win

    def test_prediction_before_any_availability_is_missing(self) -> None:
        only = _snapshot(3.0, _utc(2026, 7, 29, 9, 0))

        result = get_prediction_time_odds(
            [only], "2026-07-29-01-01", 1, _utc(2026, 7, 29, 8, 0)
        )

        self.assertFalse(result.found)


class GetClosingOddsTest(unittest.TestCase):
    def test_missing_closing_snapshot_is_explicit(self) -> None:
        live_only = _snapshot(3.0, _utc(2026, 7, 29, 8, 0))

        result = get_closing_odds([live_only], "2026-07-29-01-01", 1)

        self.assertFalse(result.found)
        self.assertIsNone(result.snapshot)

    def test_returns_the_snapshot_marked_as_closing(self) -> None:
        closing = _snapshot(2.5, _utc(2026, 7, 29, 10, 55), is_closing=True)

        result = get_closing_odds([closing], "2026-07-29-01-01", 1)

        self.assertTrue(result.found)
        self.assertEqual(result.snapshot.odds, 2.5)

    def test_multiple_closing_snapshots_raises(self) -> None:
        first = _snapshot(2.5, _utc(2026, 7, 29, 10, 55), is_closing=True)
        second = _snapshot(2.6, _utc(2026, 7, 29, 10, 56), is_closing=True)

        with self.assertRaises(OddsError):
            get_closing_odds([first, second], "2026-07-29-01-01", 1)


class PredictionTimeVsClosingOddsAreDistinctTest(unittest.TestCase):
    def test_prediction_time_and_closing_odds_can_differ(self) -> None:
        race_key = "2026-07-29-01-01"
        live = _snapshot(3.0, _utc(2026, 7, 29, 8, 0), race_key=race_key)
        closing = _snapshot(2.2, _utc(2026, 7, 29, 10, 55), race_key=race_key, is_closing=True)
        snapshots = [live, closing]

        prediction_time = get_prediction_time_odds(
            snapshots, race_key, 1, _utc(2026, 7, 29, 8, 30)
        )
        closing_result = get_closing_odds(snapshots, race_key, 1)

        self.assertTrue(prediction_time.found)
        self.assertTrue(closing_result.found)
        self.assertNotEqual(prediction_time.snapshot.odds, closing_result.snapshot.odds)
        self.assertFalse(prediction_time.snapshot.is_closing)
        self.assertTrue(closing_result.snapshot.is_closing)


if __name__ == "__main__":
    unittest.main()
