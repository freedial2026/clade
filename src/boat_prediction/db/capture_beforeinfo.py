"""Capture 直前情報 for each race shortly before its deadline.

The one genuinely pre-race source this project has never collected. Three
separate measurements on 2026-08-01 pointed at it independently
(tasks/HANDOFF.md):

* 展示タイム is the only *absolute* measure of boat speed available.
  Everything else derives from finishing position, which is relative to
  the field and therefore blind to any change common to all six crews.
  The database holds 5,829 exhibition times against 6,984,306 entries.
* Per-course racer ability is real and persistent (0.49 against a 0.78
  control), but `racer_period_course_stats` is keyed by **course** while
  the card only gives the **lane**. The start exhibition is the only
  pre-race observation of the course actually taken.
* Surface weather here is measured at the venue at race time. The JMA
  data already loaded is a *daily* average from the nearest *land*
  station, and it showed no effect at all on venue aptitude -- plausibly
  because it is too coarse rather than because there is nothing there.

**Window, not target times.** `capture_odds` fires at fixed leads because
odds move and each reading is a distinct fact. 直前情報 does not move once
published, so one capture per race is enough, and this instead takes any
race whose deadline is inside a window and which has no row yet. The
every-two-minutes schedule then self-heals: a page fetched before the
exhibition run has the boat list but no times, nothing is written, and
the next run retries it.

Volume is about 150 requests on a racing day, 3 s apart, never parallel --
the same order as the odds capture already running, and far below the
site's large-volume threshold.
"""

from __future__ import annotations

import argparse
import datetime as dt
import time
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from sqlalchemy import select

from ..beforeinfo_source import (
    BeforeInfoSourceError,
    fetch_beforeinfo_html,
    parse_beforeinfo,
)
from ..odds_source import DEFAULT_REQUEST_DELAY_SECONDS
from .loader import load_before_info
from .models import BeforeInfoEntry, Race, Venue
from .session import create_db_engine, create_session_factory

JST = ZoneInfo("Asia/Tokyo")

DEFAULT_WINDOW_MINUTES = (5, 30)
"""Capture a race when its deadline is between 5 and 30 minutes away.

The upper bound sits inside the period when 直前情報 is normally published
(after the exhibition run, roughly 40-50 minutes before the race), and
the lower bound leaves room for a run that is pacing a long queue at 3 s
per request to still finish before the deadline it is working toward.
"""


class CaptureBeforeInfoError(ValueError):
    """Raised for invalid capture parameters."""


@dataclass
class CaptureBeforeInfoResult:
    considered: int = 0
    due: int = 0
    fetched: int = 0
    stored: int = 0
    boat_rows: int = 0
    weather_rows: int = 0
    no_exhibition_yet: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return (
            f"considered={self.considered} due={self.due} fetched={self.fetched} "
            f"stored={self.stored} boat_rows={self.boat_rows} "
            f"weather_rows={self.weather_rows} "
            f"no_exhibition_yet={self.no_exhibition_yet} failed={self.failed}"
        )


@dataclass(frozen=True)
class DueRace:
    race_id: object
    venue_code: str
    race_number: int
    deadline_at: dt.datetime


def _as_aware_utc(value: dt.datetime) -> dt.datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=dt.UTC)


def find_due_races(
    session,
    now: dt.datetime,
    *,
    race_date: dt.date,
    window_minutes: tuple[int, int] = DEFAULT_WINDOW_MINUTES,
) -> tuple[list[DueRace], int]:
    """Races inside the capture window that have no 直前情報 row yet."""
    low, high = window_minutes
    if low <= 0 or high <= low:
        raise CaptureBeforeInfoError(
            f"window_minutes must be an increasing positive pair, got {window_minutes!r}"
        )

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
    for race_id, venue_code, race_number, deadline_at in rows:
        deadline_at = _as_aware_utc(deadline_at)
        minutes_away = (deadline_at - now).total_seconds() / 60
        if not (low <= minutes_away <= high):
            continue
        already = session.scalar(
            select(BeforeInfoEntry.id).where(BeforeInfoEntry.race_id == race_id)
        )
        if already is not None:
            continue
        due.append(
            DueRace(
                race_id=race_id,
                venue_code=venue_code,
                race_number=race_number,
                deadline_at=deadline_at,
            )
        )
    return due, len(rows)


def capture_due_beforeinfo(
    session,
    *,
    race_date: dt.date,
    now: dt.datetime | None = None,
    window_minutes: tuple[int, int] = DEFAULT_WINDOW_MINUTES,
    opener: object | None = None,
    delay_seconds: float = DEFAULT_REQUEST_DELAY_SECONDS,
    sleep=time.sleep,
) -> CaptureBeforeInfoResult:
    """Fetch and store every due race. A per-race failure is recorded and
    does not stop the run: one unreachable page must not cost the rest of
    the day's captures, which cannot be retaken later in the same form."""
    now = _as_aware_utc(now or dt.datetime.now(dt.UTC))
    result = CaptureBeforeInfoResult()

    due, considered = find_due_races(
        session, now, race_date=race_date, window_minutes=window_minutes
    )
    result.considered = considered
    result.due = len(due)

    for index, race in enumerate(due):
        if index:
            sleep(delay_seconds)
        observed_at = _as_aware_utc(dt.datetime.now(dt.UTC))
        try:
            html = fetch_beforeinfo_html(
                race_date, race.venue_code, race.race_number, opener=opener
            )
            info = parse_beforeinfo(html)
        except (BeforeInfoSourceError, Exception) as exc:  # noqa: BLE001
            result.failed += 1
            result.errors.append(f"{race.venue_code} {race.race_number}R: {exc}")
            continue

        result.fetched += 1
        stats = load_before_info(
            session, race_id=race.race_id, info=info, observed_at=observed_at
        )
        if stats.skipped_no_exhibition:
            result.no_exhibition_yet += 1
            continue
        if stats.boat_rows:
            result.stored += 1
            result.boat_rows += stats.boat_rows
            result.weather_rows += stats.weather_rows

    return result


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--date", type=dt.date.fromisoformat, default=None)
    parser.add_argument("--delay-seconds", type=float, default=DEFAULT_REQUEST_DELAY_SECONDS)
    parser.add_argument("--window-minutes", type=int, nargs=2, default=list(DEFAULT_WINDOW_MINUTES))
    args = parser.parse_args(argv)

    race_date = args.date or dt.datetime.now(JST).date()
    engine = create_db_engine(args.database_url)
    try:
        with create_session_factory(engine)() as session:
            result = capture_due_beforeinfo(
                session,
                race_date=race_date,
                window_minutes=tuple(args.window_minutes),
                delay_seconds=args.delay_seconds,
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
