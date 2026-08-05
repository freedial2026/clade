"""Capture each race's result minutes after it runs, not the next morning.

`ingest_daily results` loads the K-file at 02:00, so a prediction made at
06:45 cannot be checked against its own race until the following day.
This closes that gap for the day in progress; the K-file remains the
authoritative record and this never replaces it.

**Timing.** A race runs shortly after its deadline (1800 m takes about two
minutes) and is confirmed within a few more. The capture waits
`DEFAULT_SETTLE_MINUTES` after the deadline before asking. It does not
matter if that is sometimes early — a page whose race has not been
confirmed comes back without a finishing order, nothing is written, and
the next run picks it up. That self-healing is why the wait can be short
rather than defensively long.

**Once per race.** Unlike odds, a result does not change, so a race with a
row is never fetched again. Volume is therefore about one request per
race, ~150 on a racing day.

**Checkpointed.** The caller passes a `checkpoint` that is called after
each race is stored, so an interrupted run keeps what it captured. A
single commit at the end cost a whole catch-up run of 130 races the first
time this was used: the process was killed nine minutes in and every
fetched result went with it. Results can be re-fetched, but a capture
that silently discards its work is the wrong shape for collection.

**A page with no result is not an empty result.** A venue not racing that
day, and a race not yet confirmed, both return 200 with a shell. Writing
"no winner" from either would be worse than writing nothing, so
`has_result` gates every insert.
"""

from __future__ import annotations

import argparse
import datetime as dt
import time
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from sqlalchemy import select

from ..odds_source import DEFAULT_REQUEST_DELAY_SECONDS
from ..raceresult_source import (
    RaceResultSourceError,
    fetch_raceresult_html,
    parse_raceresult,
)
from .loader import SOURCE_RACERESULT, _source_id
from .models import LiveRaceResult, Race, Venue
from .session import create_db_engine, create_session_factory

JST = ZoneInfo("Asia/Tokyo")
DEFAULT_SETTLE_MINUTES = 8
"""How long after the deadline to first ask for a result.

The race itself takes about two minutes and confirmation a few more. Too
early simply returns a page without a finishing order, which writes
nothing and is retried, so this is set to catch the common case quickly
rather than to be safe against the slowest one.
"""


class CaptureResultsError(ValueError):
    """Raised for invalid capture parameters."""


@dataclass
class CaptureResultsResult:
    considered: int = 0
    due: int = 0
    fetched: int = 0
    stored: int = 0
    lane_rows: int = 0
    not_confirmed_yet: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return (
            f"considered={self.considered} due={self.due} fetched={self.fetched} "
            f"stored={self.stored} lane_rows={self.lane_rows} "
            f"not_confirmed_yet={self.not_confirmed_yet} failed={self.failed}"
        )


@dataclass(frozen=True)
class DueRace:
    race_id: object
    venue_code: str
    race_number: int


def _as_aware_utc(value: dt.datetime) -> dt.datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=dt.UTC)


def find_due_races(
    session,
    now: dt.datetime,
    *,
    race_date: dt.date,
    settle_minutes: int = DEFAULT_SETTLE_MINUTES,
) -> tuple[list[DueRace], int]:
    """Races whose deadline is far enough past and that have no row yet."""
    if settle_minutes < 0:
        raise CaptureResultsError(f"settle_minutes must not be negative: {settle_minutes}")

    now = _as_aware_utc(now)
    rows = session.execute(
        select(Race.id, Venue.code, Race.race_number, Race.scheduled_deadline_at)
        .join(Venue, Venue.id == Race.venue_id)
        .where(
            Race.race_date == race_date,
            Race.scheduled_deadline_at.is_not(None),
            Race.status != "cancelled",
        )
        .order_by(Race.scheduled_deadline_at)
    ).all()

    due: list[DueRace] = []
    for race_id, venue_code, race_number, deadline in rows:
        if _as_aware_utc(deadline) + dt.timedelta(minutes=settle_minutes) > now:
            continue
        already = session.scalar(
            select(LiveRaceResult.id).where(LiveRaceResult.race_id == race_id)
        )
        if already is not None:
            continue
        due.append(DueRace(race_id=race_id, venue_code=venue_code, race_number=race_number))
    return due, len(rows)


def store_result(session, *, race_id, page, observed_at: dt.datetime) -> int:
    """Write one race's lanes. Returns the number of rows written, 0 when
    the page carries no finishing order."""
    if not page.has_result:
        return 0

    win_payout = next(
        (p.amount_yen for p in page.payouts if p.bet_type == "単勝"), None
    )
    source_id = _source_id(session, SOURCE_RACERESULT)
    written = 0
    for lane in page.lanes:
        session.add(
            LiveRaceResult(
                race_id=race_id,
                lane_number=lane.lane_number,
                finish_position=lane.finish_position,
                status=lane.status,
                start_timing_sec=lane.start_timing_sec,
                win_payout_yen=win_payout if lane.finish_position == 1 else None,
                winning_method=page.winning_method if lane.finish_position == 1 else None,
                observed_at=observed_at,
                available_at=observed_at,
                source_id=source_id,
            )
        )
        written += 1
    session.flush()
    return written


def capture_due_results(
    session,
    *,
    race_date: dt.date,
    now: dt.datetime | None = None,
    settle_minutes: int = DEFAULT_SETTLE_MINUTES,
    opener: object | None = None,
    delay_seconds: float = DEFAULT_REQUEST_DELAY_SECONDS,
    sleep=time.sleep,
    checkpoint=None,
) -> CaptureResultsResult:
    now = _as_aware_utc(now or dt.datetime.now(dt.UTC))
    result = CaptureResultsResult()

    due, considered = find_due_races(
        session, now, race_date=race_date, settle_minutes=settle_minutes
    )
    result.considered = considered
    result.due = len(due)

    for index, race in enumerate(due):
        if index:
            sleep(delay_seconds)
        observed_at = _as_aware_utc(dt.datetime.now(dt.UTC))
        try:
            html = fetch_raceresult_html(
                race_date, race.venue_code, race.race_number, opener=opener
            )
            page = parse_raceresult(html)
        except (RaceResultSourceError, Exception) as exc:  # noqa: BLE001
            result.failed += 1
            result.errors.append(f"{race.venue_code} {race.race_number}R: {exc}")
            continue

        result.fetched += 1
        written = store_result(
            session, race_id=race.race_id, page=page, observed_at=observed_at
        )
        if written:
            result.stored += 1
            result.lane_rows += written
            if checkpoint is not None:
                checkpoint()
        else:
            result.not_confirmed_yet += 1

    return result


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--date", type=dt.date.fromisoformat, default=None)
    parser.add_argument("--settle-minutes", type=int, default=DEFAULT_SETTLE_MINUTES)
    parser.add_argument("--delay-seconds", type=float, default=DEFAULT_REQUEST_DELAY_SECONDS)
    args = parser.parse_args(argv)

    race_date = args.date or dt.datetime.now(JST).date()
    engine = create_db_engine(args.database_url)
    try:
        with create_session_factory(engine)() as session:
            result = capture_due_results(
                session,
                race_date=race_date,
                settle_minutes=args.settle_minutes,
                delay_seconds=args.delay_seconds,
                checkpoint=session.commit,
            )
            session.commit()
    finally:
        engine.dispose()

    print(f"{race_date} {result}")
    for message in result.errors[:10]:
        print(f"  error: {message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
