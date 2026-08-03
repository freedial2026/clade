"""Capture live odds before the deadline, for races running today.

Why this exists
---------------

`load_odds_archive` loads the published 締切時オッズ, of which exactly one
observation per race is ever retained, stamped with the deadline itself.
That is enough to compare a model against the market's final judgement,
but not to *act* on the comparison: by the time those odds exist, betting
is closed. Nothing in the archive can fix this retroactively -- the time
series was never published -- so a pre-deadline series can only be built
going forward, one racing day at a time. This is that job.

How it decides what to fetch
----------------------------

Each race's deadline is already known: `races.scheduled_deadline_at`
comes from the B-file race card, published the day before. So a run
simply asks the database which races are approaching their deadline.

For every requested lead time (`--lead-minutes`, default 10 and 2), a
race is captured once when the current time falls inside
`deadline - lead ± tolerance`. Whether that round already happened is
decided by looking for an existing snapshot inside the same window, so
overlapping cron runs, retries and restarts cannot double-record, and no
extra state is kept outside the database.

Run it from cron every couple of minutes. A run that finds nothing due
exits 0 without making a single request.

Volume and site policy
----------------------

A racing day is ~13 venues x 12 races, so one lead time costs ~150
requests and the three defaults cost ~450 -- spread across the day, at
the package's usual 3s spacing, never parallel. That is the same order as
`odds_source.fetch_range`'s daily volume and far below the "large-volume
access" the site's policy prohibits. Adding lead times multiplies it;
keep the list short.

`--with-exacta` fetches a second page per race and so doubles that, to
~900 a day, or roughly 1.3 requests a minute averaged over the racing
window. Why it is worth the doubling: 単勝 and 2連単 are *separate pools*
on the same race, `P(1着 = boat i)` is readable from both, and where the
two disagree one of them is stale. The archive cannot answer that -- it
keeps one closing snapshot of 単勝 alone -- so, like the pre-deadline
series itself, it can only be built forward.

Prerequisite
------------

Today's B-file must already be loaded, or there are no deadlines to key
on and the run reports `races_considered=0`. Sequence the daily jobs so
the card load runs first.
"""

from __future__ import annotations

import argparse
import datetime as dt
from dataclasses import dataclass, field
from time import sleep

from sqlalchemy import select

from ..odds_source import (
    DEFAULT_REQUEST_DELAY_SECONDS,
    fetch_exacta_odds_page,
    fetch_odds_page,
    parse_exacta_odds,
    parse_win_place_odds,
)
from ..temporal import to_utc
from . import loader
from .loader import JST
from .models import OddsSnapshot, Race, Venue
from .session import create_db_engine, create_session_factory

DEFAULT_LEAD_MINUTES = (60, 10, 2)
"""When to read the odds, in minutes before the deadline.

The 60 exists for one reason and it is not redundancy. 直前情報 is
published well before the deadline -- measured on 2026-08-01, the live
capture found it complete 13-29 minutes out -- so a reading at 10 or 2
minutes is taken *after* the market has already seen it. With only those
two, the market's reaction to the exhibition data can never be observed,
because there is no "before" to compare against, and no amount of waiting
fixes it: the price at that moment is simply not recorded.

That is the shape of the one question this project has not been able to
ask. Everything measured so far scores a probability against an outcome;
whether the crowd *absorbs* new information, and how fast, needs a price
from each side of its arrival.

Adding lead times at 20/5/1 would not have helped -- all of them are also
after publication. Position matters here, not count. 60 is chosen to sit
safely before publication rather than tightly before it; the exact
publication time has not been measured, and until it is, a tighter lead
risks landing on the wrong side of the event it exists to bracket.

Cost: one extra reading per race, ~150 requests a day, taking the odds
capture from ~300 to ~450 and the whole daily total to ~600. Each request
is still paced 3 s apart and interleaved with the 直前情報 capture.
"""

DEFAULT_TOLERANCE_MINUTES = 2


class CaptureOddsError(ValueError):
    """Raised for invalid input to the capture run itself."""


@dataclass
class CaptureResult:
    races_considered: int = 0
    fetched: int = 0
    exacta_fetched: int = 0
    stats: loader.OddsLoadStats = field(default_factory=loader.OddsLoadStats)
    failed: list[tuple[str, str]] = field(default_factory=list)

    def __str__(self) -> str:
        return (
            f"races_considered={self.races_considered} fetched={self.fetched} "
            f"exacta_fetched={self.exacta_fetched} "
            f"snapshots={self.stats.snapshots} "
            f"skipped_missing_value={self.stats.skipped_missing_value} "
            f"skipped_already_observed={self.stats.skipped_already_observed} "
            f"failed={len(self.failed)}"
        )


@dataclass(frozen=True)
class DueRace:
    venue_code: str
    race_date: dt.date
    race_number: int
    deadline_at: dt.datetime
    lead_minutes: int


def _as_aware_utc(value: dt.datetime) -> dt.datetime:
    """SQLite hands back naive datetimes where PostgreSQL gives aware
    ones; the comparisons below are all in UTC either way."""
    return value if value.tzinfo is not None else value.replace(tzinfo=dt.UTC)


def find_due_races(
    session,
    now: dt.datetime,
    *,
    race_date: dt.date,
    lead_minutes: tuple[int, ...] = DEFAULT_LEAD_MINUTES,
    tolerance_minutes: int = DEFAULT_TOLERANCE_MINUTES,
) -> tuple[list[DueRace], int]:
    """Races whose deadline puts them inside a capture window right now
    and that have no snapshot in that window yet.

    Returns the due races and how many races were considered, so a run
    that fetches nothing can still say whether it saw a race card at all.
    """
    if not lead_minutes:
        raise CaptureOddsError("at least one lead time is required")
    if tolerance_minutes <= 0:
        raise CaptureOddsError(f"tolerance_minutes must be positive, got {tolerance_minutes}")

    now = _as_aware_utc(now)
    tolerance = dt.timedelta(minutes=tolerance_minutes)
    rows = session.execute(
        select(Venue.code, Race.race_number, Race.id, Race.scheduled_deadline_at)
        .join(Venue, Venue.id == Race.venue_id)
        .where(Race.race_date == race_date, Race.scheduled_deadline_at.is_not(None))
        .order_by(Race.scheduled_deadline_at)
    ).all()

    due: list[DueRace] = []
    for venue_code, race_number, race_id, deadline_at in rows:
        deadline_at = _as_aware_utc(deadline_at)
        for lead in sorted(lead_minutes, reverse=True):
            target = deadline_at - dt.timedelta(minutes=lead)
            if not (target - tolerance <= now <= target + tolerance):
                continue
            # Any snapshot at or after this round's window opens means the
            # round happened. Deliberately unbounded above: a run pacing
            # ~150 races at 3s takes minutes, so the reading for a late
            # race is stamped well after its own window closed, and a
            # bounded check would call that round undone and refetch it.
            # An earlier round cannot satisfy this test, because its
            # window opens further from the deadline.
            already = session.scalar(
                select(OddsSnapshot.id).where(
                    OddsSnapshot.race_id == race_id,
                    OddsSnapshot.observed_at >= target - tolerance,
                )
            )
            if already is not None:
                continue
            due.append(
                DueRace(
                    venue_code=venue_code,
                    race_date=race_date,
                    race_number=race_number,
                    deadline_at=deadline_at,
                    lead_minutes=lead,
                )
            )
            break  # one capture per race per run, even if windows overlap
    return due, len(rows)


def capture_due_odds(
    session,
    *,
    now: dt.datetime | None = None,
    race_date: dt.date | None = None,
    lead_minutes: tuple[int, ...] = DEFAULT_LEAD_MINUTES,
    tolerance_minutes: int = DEFAULT_TOLERANCE_MINUTES,
    delay_seconds: float = DEFAULT_REQUEST_DELAY_SECONDS,
    opener: object | None = None,
    sleeper=sleep,
    clock=None,
    with_exacta: bool = False,
) -> CaptureResult:
    """Fetch and store every race that is due for capture right now.

    One race's failure does not stop the run -- a single unreachable page
    must not cost the rest of the day's captures, which cannot be
    retaken later.

    `with_exacta` adds the 2連単/2連複 page, doubling the request count.
    Both pages for one race are stamped with **one** `observed_at`, taken
    before either fetch: the point of the second pool is to compare it
    with the first, and two stamps 3 s apart would leave every comparison
    with a 3 s window in which the market could have moved. The stamp is
    therefore slightly early for the second page -- conservative in the
    direction that matters, since `available_at` is what a leakage check
    reads.

    A round counts as done once *any* snapshot exists for it, so a race
    whose win page succeeds and whose exacta page fails is not retried;
    it is recorded in `failed` instead. That is deliberate -- the
    alternative is per-bet-type round state, which is a lot of machinery
    for a page that can simply be missing from a comparison.
    """
    if delay_seconds < 1.0:
        raise CaptureOddsError(f"delay_seconds must be >= 1.0, got {delay_seconds!r}")
    clock = clock or (lambda: dt.datetime.now(dt.UTC))
    now = _as_aware_utc(now or clock())
    race_date = race_date or now.astimezone(JST).date()

    due, considered = find_due_races(
        session,
        now,
        race_date=race_date,
        lead_minutes=lead_minutes,
        tolerance_minutes=tolerance_minutes,
    )
    result = CaptureResult(races_considered=considered)

    for index, race in enumerate(due):
        label = f"{race.venue_code} {race.race_date} {race.race_number}R"
        # Read the clock per race, not once per run: with 3s spacing a
        # run's last race is fetched minutes after its first, and stamping
        # them alike would misstate when each was available.
        observed_at = to_utc(_as_aware_utc(clock()))
        try:
            html = fetch_odds_page(
                race.race_date, race.venue_code, race.race_number, opener=opener
            )
            parsed = parse_win_place_odds(html)
            result.stats = result.stats.merge(
                loader.load_odds_observation(
                    session,
                    race.venue_code,
                    race.race_date,
                    race.race_number,
                    parsed,
                    observed_at,
                )
            )
            result.fetched += 1
        except Exception as exc:  # noqa: BLE001 - record and keep capturing
            result.failed.append((label, str(exc)))

        if with_exacta:
            sleeper(delay_seconds)
            try:
                html = fetch_exacta_odds_page(
                    race.race_date, race.venue_code, race.race_number, opener=opener
                )
                result.stats = result.stats.merge(
                    loader.load_combination_odds_observation(
                        session,
                        race.venue_code,
                        race.race_date,
                        race.race_number,
                        parse_exacta_odds(html),
                        observed_at,
                    )
                )
                result.exacta_fetched += 1
            except Exception as exc:  # noqa: BLE001 - record and keep capturing
                result.failed.append((f"{label} 2連単", str(exc)))

        if index < len(due) - 1:
            sleeper(delay_seconds)

    return result


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=None)
    parser.add_argument(
        "--race-date",
        type=dt.date.fromisoformat,
        default=None,
        help="racing day to capture (default: today in JST)",
    )
    parser.add_argument(
        "--lead-minutes",
        type=int,
        nargs="+",
        default=list(DEFAULT_LEAD_MINUTES),
        help="capture this many minutes before each deadline (default: 10 2)",
    )
    parser.add_argument(
        "--tolerance-minutes",
        type=int,
        default=DEFAULT_TOLERANCE_MINUTES,
        help="how far from the target time a run still counts as that round",
    )
    parser.add_argument("--delay-seconds", type=float, default=DEFAULT_REQUEST_DELAY_SECONDS)
    parser.add_argument(
        "--with-exacta",
        action="store_true",
        help="also capture the 2連単/2連複 pool (one more page per race, doubling requests)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what is due without fetching or writing anything",
    )
    args = parser.parse_args(argv)

    engine = create_db_engine(args.database_url)
    try:
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            if args.dry_run:
                now = dt.datetime.now(dt.UTC)
                race_date = args.race_date or now.astimezone(JST).date()
                due, considered = find_due_races(
                    session,
                    now,
                    race_date=race_date,
                    lead_minutes=tuple(args.lead_minutes),
                    tolerance_minutes=args.tolerance_minutes,
                )
                print(f"dry-run: races_considered={considered} due={len(due)}")
                for race in due[:20]:
                    print(
                        f"  {race.venue_code} {race.race_number}R "
                        f"deadline={race.deadline_at.astimezone(JST):%H:%M} "
                        f"lead={race.lead_minutes}m"
                    )
                return 0

            result = capture_due_odds(
                session,
                race_date=args.race_date,
                lead_minutes=tuple(args.lead_minutes),
                tolerance_minutes=args.tolerance_minutes,
                delay_seconds=args.delay_seconds,
                with_exacta=args.with_exacta,
            )
            session.commit()
    finally:
        engine.dispose()

    print(f"done: {result}")
    for label, message in result.failed[:20]:
        print(f"  failed: {label}: {message}")
    return 1 if result.failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
