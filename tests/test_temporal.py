import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from boat_prediction.temporal import (
    TemporalError,
    TemporalRecord,
    filter_available,
    for_display,
    is_available_for_prediction,
    is_valid_at,
    to_utc,
)

from _helpers import utc as _utc


class ToUtcTest(unittest.TestCase):
    def test_converts_aware_datetime_to_utc(self) -> None:
        jst = datetime(2026, 1, 1, 21, 0, tzinfo=ZoneInfo("Asia/Tokyo"))

        converted = to_utc(jst)

        self.assertEqual(converted, _utc(2026, 1, 1, 12, 0))
        self.assertEqual(converted.tzinfo, timezone.utc)

    def test_rejects_naive_datetime(self) -> None:
        with self.assertRaises(TemporalError):
            to_utc(datetime(2026, 1, 1, 12, 0))


class ForDisplayTest(unittest.TestCase):
    def test_converts_utc_storage_value_to_local_timezone(self) -> None:
        stored = _utc(2026, 1, 1, 12, 0)

        displayed = for_display(stored, ZoneInfo("Asia/Tokyo"))

        self.assertEqual(displayed.hour, 21)
        self.assertEqual(stored.tzinfo, timezone.utc)  # storage value untouched


class TemporalRecordTest(unittest.TestCase):
    def _record(self, **overrides: datetime | None) -> TemporalRecord:
        defaults = dict(
            event_time=_utc(2026, 1, 1, 10, 0),
            published_at=_utc(2026, 1, 1, 10, 5),
            collected_at=_utc(2026, 1, 1, 10, 10),
            available_at=_utc(2026, 1, 1, 10, 15),
            valid_from=_utc(2026, 1, 1, 0, 0),
            valid_to=None,
        )
        defaults.update(overrides)
        return TemporalRecord(**defaults)

    def test_accepts_well_ordered_utc_timestamps(self) -> None:
        record = self._record()
        self.assertIsNone(record.valid_to)

    def test_rejects_naive_datetime_field(self) -> None:
        with self.assertRaises(TemporalError):
            self._record(published_at=datetime(2026, 1, 1, 10, 5))

    def test_rejects_non_utc_offset(self) -> None:
        with self.assertRaises(TemporalError):
            self._record(published_at=datetime(2026, 1, 1, 19, 5, tzinfo=ZoneInfo("Asia/Tokyo")))

    def test_rejects_out_of_order_timestamps(self) -> None:
        with self.assertRaises(TemporalError):
            self._record(collected_at=_utc(2026, 1, 1, 10, 3))  # before published_at

    def test_rejects_valid_to_before_valid_from(self) -> None:
        with self.assertRaises(TemporalError):
            self._record(valid_from=_utc(2026, 1, 2), valid_to=_utc(2026, 1, 1))


class PointInTimeQueryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.record = TemporalRecord(
            event_time=_utc(2026, 1, 1, 10, 0),
            published_at=_utc(2026, 1, 1, 10, 5),
            collected_at=_utc(2026, 1, 1, 10, 10),
            available_at=_utc(2026, 1, 1, 10, 15),
            valid_from=_utc(2026, 1, 1, 0, 0),
            valid_to=_utc(2026, 2, 1, 0, 0),
        )

    def test_is_available_for_prediction_at_or_after_available_at(self) -> None:
        self.assertTrue(is_available_for_prediction(self.record, self.record.available_at))
        self.assertTrue(
            is_available_for_prediction(self.record, self.record.available_at + timedelta(minutes=1))
        )

    def test_rejects_prediction_before_available_at_future_leak(self) -> None:
        before = self.record.available_at - timedelta(minutes=1)
        self.assertFalse(is_available_for_prediction(self.record, before))

    def test_filter_available_excludes_records_not_yet_available(self) -> None:
        later_record = TemporalRecord(
            event_time=_utc(2026, 1, 2, 10, 0),
            published_at=_utc(2026, 1, 2, 10, 5),
            collected_at=_utc(2026, 1, 2, 10, 10),
            available_at=_utc(2026, 1, 2, 10, 15),
            valid_from=_utc(2026, 1, 2, 0, 0),
        )

        prediction_at = self.record.available_at + timedelta(minutes=1)
        result = filter_available([self.record, later_record], prediction_at)

        self.assertEqual(result, [self.record])

    def test_is_valid_at_within_window(self) -> None:
        self.assertTrue(is_valid_at(self.record, _utc(2026, 1, 15)))

    def test_is_valid_at_outside_window(self) -> None:
        self.assertFalse(is_valid_at(self.record, _utc(2026, 3, 1)))
        self.assertFalse(is_valid_at(self.record, _utc(2025, 12, 31)))


if __name__ == "__main__":
    unittest.main()
