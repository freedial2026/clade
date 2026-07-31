"""Tests for `db.meeting_resolution`.

The dated cases are real venue-days from the 2005-2026 B-file archive.
"""

import datetime as dt
import unittest

from boat_prediction.db.meeting_resolution import (
    MAX_POSTPONEMENT_GAP_DAYS,
    continues_meeting,
    estimated_meeting_start,
    resolve_new_meeting_start,
)


class ContinuesMeetingTest(unittest.TestCase):
    def test_day_one_always_opens_a_new_meeting(self) -> None:
        # Back-to-back 節 at one venue can be a single day apart (173 in
        # the archive), so 第1日 is what keeps them separate.
        self.assertFalse(
            continues_meeting(1, dt.date(2016, 3, 21), dt.date(2016, 3, 20))
        )

    def test_the_next_calendar_day_continues(self) -> None:
        self.assertTrue(continues_meeting(2, dt.date(2026, 6, 2), dt.date(2026, 6, 1)))

    def test_a_repeated_day_number_continues(self) -> None:
        # Venue 24, 2005-09: 第5日 ran on 09-05, 09-06 and 09-07.
        self.assertTrue(continues_meeting(5, dt.date(2005, 9, 6), dt.date(2005, 9, 5)))
        self.assertTrue(continues_meeting(5, dt.date(2005, 9, 7), dt.date(2005, 9, 6)))

    def test_a_backwards_day_number_continues(self) -> None:
        # Venue 03, 2007-07: 第6日 on 07-16 then 第5日 on 07-17.
        self.assertTrue(continues_meeting(5, dt.date(2007, 7, 17), dt.date(2007, 7, 16)))

    def test_a_gap_within_the_limit_continues(self) -> None:
        start = dt.date(2011, 5, 1)
        for gap in range(1, MAX_POSTPONEMENT_GAP_DAYS + 1):
            with self.subTest(gap=gap):
                self.assertTrue(
                    continues_meeting(3, start + dt.timedelta(days=gap), start)
                )

    def test_a_gap_beyond_the_limit_opens_a_new_meeting(self) -> None:
        start = dt.date(2011, 5, 1)
        self.assertFalse(
            continues_meeting(
                3, start + dt.timedelta(days=MAX_POSTPONEMENT_GAP_DAYS + 1), start
            )
        )

    def test_a_non_advancing_date_does_not_continue(self) -> None:
        day = dt.date(2026, 6, 1)
        self.assertFalse(continues_meeting(2, day, day))
        self.assertFalse(continues_meeting(2, day - dt.timedelta(days=1), day))


class MeetingStartTest(unittest.TestCase):
    def test_day_one_starts_on_its_own_date(self) -> None:
        self.assertEqual(
            estimated_meeting_start(dt.date(2026, 6, 1), 1), dt.date(2026, 6, 1)
        )

    def test_a_later_day_counts_back(self) -> None:
        self.assertEqual(
            estimated_meeting_start(dt.date(2026, 6, 5), 5), dt.date(2026, 6, 1)
        )

    def test_a_free_estimate_is_used_as_is(self) -> None:
        self.assertEqual(
            resolve_new_meeting_start(dt.date(2026, 6, 5), 5, {dt.date(2026, 5, 30)}),
            dt.date(2026, 6, 1),
        )

    def test_a_taken_estimate_moves_forward(self) -> None:
        # Reusing a taken start would merge this 節 into the meeting that
        # already owns that key -- the one failure the old rule avoided.
        taken = {dt.date(2026, 6, 1), dt.date(2026, 6, 2)}
        self.assertEqual(
            resolve_new_meeting_start(dt.date(2026, 6, 5), 5, taken), dt.date(2026, 6, 3)
        )


if __name__ == "__main__":
    unittest.main()
