"""Backfill 直前情報 from the Boatrace Open API mirror.

Fills the gap the live capture cannot: the mirror serves 2023-05-01
onward, about 170,000 races, while `capture_beforeinfo` only starts
accumulating from the day it was switched on. One HTTP request per day
rather than per race, against GitHub Pages rather than boatrace.jp.

**`available_at` is the race's own deadline, not the fetch.** For a live
capture the fetch time is the evidence; for a backfill it proves nothing
— the values are being read months later. The exhibition run demonstrably
happens before the race, so the deadline is a bound that is certainly
true and errs late, which is the safe direction. The live capture shows
the real margin is 5-30 minutes, so this bound understates availability
and never overstates it.

Rows carry `SOURCE_BOATRACE_OPENAPI`, so a query can always separate
backfilled rows from ones read off the official page — which matters,
because the mirror omits parts replacement (written as NULL, not False)
and the weather observation label (so `is_safe_for_race` refuses its
weather).

Idempotent per race via `load_before_info`'s own existence check, and
ledger-driven per day in the manner of `load_archive.py`, so an
interrupted run resumes instead of refetching.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select

from ..boatrace_openapi_source import EARLIEST_DATE, BoatraceOpenApiError, fetch_day
from .loader import SOURCE_BOATRACE_OPENAPI, ensure_reference_data, load_before_info
from .models import Race, Venue
from .session import create_db_engine, create_session_factory

DEFAULT_LEDGER = Path("data/manifests/beforeinfo_backfill_ledger.json")
DEFAULT_DELAY_SECONDS = 1.0
"""One request per day of data, against GitHub Pages. A second between
them is courtesy rather than a rate limit."""


@dataclass
class BackfillStats:
    days: int = 0
    days_skipped: int = 0
    races_seen: int = 0
    races_loaded: int = 0
    races_not_in_db: int = 0
    races_already_loaded: int = 0
    races_no_exhibition: int = 0
    failures: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return (
            f"days={self.days} days_skipped={self.days_skipped} "
            f"races_seen={self.races_seen} races_loaded={self.races_loaded} "
            f"races_not_in_db={self.races_not_in_db} "
            f"races_already_loaded={self.races_already_loaded} "
            f"races_no_exhibition={self.races_no_exhibition} "
            f"failures={len(self.failures)}"
        )


def _load_ledger(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    return set(json.loads(path.read_text(encoding="utf-8")).get("days", []))


def _save_ledger(path: Path, days: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"days": sorted(days)}, indent=2) + "\n", encoding="utf-8"
    )


def _race_index(session, race_date: dt.date) -> dict[tuple[str, int], tuple]:
    rows = session.execute(
        select(Venue.code, Race.race_number, Race.id, Race.scheduled_deadline_at)
        .join(Venue, Venue.id == Race.venue_id)
        .where(Race.race_date == race_date)
    ).all()
    return {(code, int(number)): (race_id, deadline) for code, number, race_id, deadline in rows}


def backfill_day(session, race_date: dt.date, *, opener=None) -> BackfillStats:
    stats = BackfillStats(days=1)
    races = fetch_day(race_date, opener=opener)
    index = _race_index(session, race_date)

    for preview in races:
        stats.races_seen += 1
        key = (preview.venue_code, preview.race_number)
        if key not in index:
            # The card was never loaded for this race; inventing a bare
            # Race row here would be the "don't invent data" rule broken.
            stats.races_not_in_db += 1
            continue
        race_id, deadline = index[key]
        if deadline is None:
            stats.races_not_in_db += 1
            continue
        result = load_before_info(
            session,
            race_id=race_id,
            info=preview.info,
            observed_at=deadline,
            available_at=deadline,
            source_code=SOURCE_BOATRACE_OPENAPI,
            parts_known=False,
        )
        if result.skipped_no_exhibition:
            stats.races_no_exhibition += 1
        elif result.skipped_already_captured:
            stats.races_already_loaded += 1
        elif result.boat_rows:
            stats.races_loaded += 1
    return stats


def backfill_range(
    session_factory,
    *,
    start_date: dt.date,
    end_date: dt.date,
    ledger_path: Path = DEFAULT_LEDGER,
    delay_seconds: float = DEFAULT_DELAY_SECONDS,
    opener=None,
    sleep=time.sleep,
    log=print,
) -> BackfillStats:
    if start_date < EARLIEST_DATE:
        raise BoatraceOpenApiError(
            f"start_date {start_date} precedes the feed's earliest date {EARLIEST_DATE}"
        )
    if end_date < start_date:
        raise BoatraceOpenApiError(f"end_date {end_date} precedes start_date {start_date}")

    done = _load_ledger(ledger_path)
    total = BackfillStats()
    current = start_date
    first = True

    while current <= end_date:
        stamp = current.isoformat()
        if stamp in done:
            total.days_skipped += 1
            current += dt.timedelta(days=1)
            continue
        if not first:
            sleep(delay_seconds)
        first = False

        with session_factory() as session:
            ensure_reference_data(session)
            try:
                day = backfill_day(session, current, opener=opener)
            except Exception as exc:  # noqa: BLE001 -- one bad day must not end the run
                session.rollback()
                total.failures.append(f"{stamp}: {exc}")
                log(f"{stamp}: FAILED {exc}")
                current += dt.timedelta(days=1)
                continue
            session.commit()

        total.days += day.days
        total.races_seen += day.races_seen
        total.races_loaded += day.races_loaded
        total.races_not_in_db += day.races_not_in_db
        total.races_already_loaded += day.races_already_loaded
        total.races_no_exhibition += day.races_no_exhibition
        done.add(stamp)
        _save_ledger(ledger_path, done)
        if current.day == 1 or day.races_loaded == 0:
            log(f"{stamp}: {day}")
        current += dt.timedelta(days=1)

    return total


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--start-date", type=dt.date.fromisoformat, default=EARLIEST_DATE)
    parser.add_argument("--end-date", type=dt.date.fromisoformat, required=True)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--delay-seconds", type=float, default=DEFAULT_DELAY_SECONDS)
    args = parser.parse_args(argv)

    engine = create_db_engine(args.database_url)
    try:
        stats = backfill_range(
            create_session_factory(engine),
            start_date=args.start_date,
            end_date=args.end_date,
            ledger_path=args.ledger,
            delay_seconds=args.delay_seconds,
        )
    finally:
        engine.dispose()

    print(f"done: {stats}")
    for message in stats.failures[:10]:
        print(f"  {message}")
    return 1 if stats.failures else 0


if __name__ == "__main__":
    raise SystemExit(_main())
