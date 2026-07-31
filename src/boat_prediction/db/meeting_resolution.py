"""How a race day is attached to its 節 (`RaceMeeting`).

Shared by `loader._get_or_create_meeting` (incremental, one day at a
time) and `rebuild_meetings` (bulk, over days already in the database)
so the two cannot drift apart.

Why this is not arithmetic
--------------------------

The obvious key, `race_date - (series_day - 1)`, assumes `series_day`
advances by exactly one per calendar day. Measured across the whole
2005-2026 B-file archive (7,862 files, 97,116 venue-days, 17,852 節),
it does not:

- The B-file is a race card published *before* the day, so a day
  postponed for weather still ships a full card and the counter
  repeats. Venue 24 in 2005-09 ran 第5日 on three consecutive dates.
- It also skips (venue 09, 2005-09: 第1日, 第1日, 第3日, 第3日, 第4日)
  and even goes backwards (venue 03, 2007-07: 第1,2,2,3,4,6,5,6日).

Under the arithmetic key those days derive different start dates, so
**541 節 (3.03%) fragment into 2-4 `RaceMeeting` rows** — 18,450 rows
for 17,852 節. Any "earlier in this 節" feature (the motor and boat are
drawn for the series, and racer form drifts across it) silently loses
its history at the postponement boundary.

The rule used instead
---------------------

A day continues the venue's most recent meeting when it is not 第1日 and
falls within `MAX_POSTPONEMENT_GAP_DAYS` of that meeting's last loaded
race day; otherwise it opens a new one. Replaying the archive with this
rule reproduced all 17,852 節 exactly, with no meeting spanning two of
them.

Only days already loaded are consulted, so no future knowledge enters —
unlike "last day of the 節", which is why
`models.RaceMeeting.meeting_end_date` stays NULL.

Two limitations, both accepted deliberately:

- It is order-dependent. Loading a month in isolation re-opens a
  meeting mid-節, exactly as the arithmetic key does today.
- 38 節 (0.21%) have no 第1日 in the archive at all; those attach to the
  preceding meeting if it is within the gap.
"""

from __future__ import annotations

import datetime as dt

MAX_POSTPONEMENT_GAP_DAYS = 3
"""How far a 節 may jump and still be the same 節.

Consecutive 節 at one venue can start as little as one day apart (173
cases in the archive, 742 at two days), so a wider window would swallow
the next 節. Three days covers the observed postponement runs while
staying inside that separation.
"""


def continues_meeting(
    series_day: int, race_date: dt.date, previous_race_date: dt.date
) -> bool:
    """True when this day belongs to the meeting that `previous_race_date`
    belongs to.

    第1日 always opens a new 節: it is the one part of `series_day` that
    is reliable, and it is what keeps back-to-back 節 apart.
    """
    if series_day == 1:
        return False
    gap = (race_date - previous_race_date).days
    return 0 < gap <= MAX_POSTPONEMENT_GAP_DAYS


def estimated_meeting_start(race_date: dt.date, series_day: int) -> dt.date:
    """Best available start date when opening a new meeting.

    Exact for a 第1日; for any other day it is only an estimate, because
    the counter that produced it is the unreliable one. It is used as a
    key, not as a claim about when the 節 began -- which is why
    `resolve_new_meeting_start` may move it to avoid a collision.
    """
    return race_date - dt.timedelta(days=series_day - 1)


def resolve_new_meeting_start(
    race_date: dt.date, series_day: int, taken: object
) -> dt.date:
    """`estimated_meeting_start`, moved forward past any date already
    used as a start at this venue.

    `(venue_id, meeting_start_date)` is unique, so an estimate that
    collides with an existing meeting would silently merge this 節 into
    that one -- the one failure mode the arithmetic key never had and
    that this module must not introduce. `taken` is any container
    supporting `in` (a set of dates, or a DB-backed lookup).
    """
    start = estimated_meeting_start(race_date, series_day)
    while start in taken:
        start += dt.timedelta(days=1)
    return start
