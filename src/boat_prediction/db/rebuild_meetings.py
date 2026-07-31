"""Re-derive `races.meeting_id` for races already in the database.

Repairs data loaded before `loader._get_or_create_meeting` switched from
`race_date - (series_day - 1)` to the lookup rule in
`meeting_resolution`. That arithmetic fragments a 節 whenever
`series_day` repeats, skips or goes backwards, which the B-file does on
every postponed day: measured over the 2005-2026 archive, 541 節 (3.03%)
split across 2-4 `RaceMeeting` rows.

No file is re-parsed and no schema changes. Everything needed is already
on `races` (`venue_id`, `race_date`, `series_day`), and `race_meetings`
is referenced only by `races.meeting_id`, so the repair touches exactly
two tables.

Safety
------

- Dry-run by default. `--apply` is required to write anything.
- `--apply` first copies `(races.id, races.meeting_id)` into
  `races_meeting_id_backup`, then does all its work in one transaction.
- Only meetings left with no races are deleted.
- Rollback: restore `race_meetings` from a `pg_dump -t race_meetings`
  taken beforehand, then
  `UPDATE races r SET meeting_id = b.meeting_id
     FROM races_meeting_id_backup b WHERE b.id = r.id;`

Applying this to a database that a load is still writing to would race
with the loader; run it only when no load is in flight.
"""

from __future__ import annotations

import argparse
import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from .meeting_resolution import continues_meeting
from .models import Race, RaceMeeting, Venue
from .session import create_db_engine, create_session_factory

BACKUP_TABLE = "races_meeting_id_backup"


class RebuildMeetingsError(RuntimeError):
    """Raised when the rebuild cannot run safely."""


@dataclass
class RebuildStats:
    venue_days: int = 0
    meetings_before: int = 0
    meetings_after: int = 0
    meetings_deleted: int = 0
    races_repointed: int = 0
    series_split_before: int = 0
    examples: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return (
            f"venue_days={self.venue_days} "
            f"meetings_before={self.meetings_before} "
            f"meetings_after={self.meetings_after} "
            f"meetings_deleted={self.meetings_deleted} "
            f"races_repointed={self.races_repointed} "
            f"series_split_before={self.series_split_before}"
        )


def _venue_days(session: Session) -> dict[tuple[object, dt.date], dict]:
    """One row per (venue, race_date): its series_day and the meetings its
    races currently point at."""
    days: dict[tuple[object, dt.date], dict] = {}
    for venue_id, race_date, series_day, meeting_id, race_id in session.execute(
        select(Race.venue_id, Race.race_date, Race.series_day, Race.meeting_id, Race.id).order_by(
            Race.venue_id, Race.race_date
        )
    ):
        key = (venue_id, race_date)
        day = days.setdefault(
            key,
            {"series_day": series_day, "meeting_ids": set(), "race_ids": []},
        )
        if day["series_day"] is None:
            day["series_day"] = series_day
        day["race_ids"].append(race_id)
        if meeting_id is not None:
            day["meeting_ids"].add(meeting_id)
    return days


def plan_rebuild(
    session: Session, days: dict[tuple[object, dt.date], dict] | None = None
) -> tuple[list[list[tuple]], RebuildStats]:
    """Group loaded venue-days into 節 without writing anything.

    Returns the groups (lists of `(venue_id, race_date)` keys, in date
    order) and the statistics describing the change.
    """
    if days is None:
        days = _venue_days(session)
    stats = RebuildStats(venue_days=len(days))
    stats.meetings_before = session.scalar(select(func.count()).select_from(RaceMeeting))

    by_venue: dict[object, list[tuple[object, dt.date]]] = defaultdict(list)
    for key in days:
        by_venue[key[0]].append(key)

    groups: list[list[tuple]] = []
    for keys in by_venue.values():
        keys.sort(key=lambda k: k[1])
        current: list[tuple] = []
        previous_date: dt.date | None = None
        for key in keys:
            series_day = days[key]["series_day"]
            if series_day is None:
                # A day loaded from the K-file with no B-file card: it has
                # no meeting and gains none here. It must not break the
                # chain either, or the 節 around it fragments -- 12 of the
                # 18 such days in the .21 database sit mid-節, with the day
                # numbers either side running straight through the hole
                # (第1日 then 第3日). Leaving `previous_date` alone makes
                # the next carded day two days from the last one, which is
                # inside the postponement window. Merging two different 節
                # this way is not possible: a 第1日 still opens a new one.
                continue
            starts_new = previous_date is None or not continues_meeting(
                series_day, key[1], previous_date
            )
            if starts_new and current:
                groups.append(current)
                current = []
            current.append(key)
            previous_date = key[1]
        if current:
            groups.append(current)

    venue_codes = dict(session.execute(select(Venue.id, Venue.code)).all())
    groups_with_a_meeting = 0
    for group in groups:
        meeting_ids = set()
        for key in group:
            meeting_ids |= days[key]["meeting_ids"]
        if meeting_ids:
            groups_with_a_meeting += 1
        if len(meeting_ids) > 1:
            stats.series_split_before += 1
            if len(stats.examples) < 10:
                stats.examples.append(
                    f"venue={venue_codes.get(group[0][0], group[0][0])} "
                    f"{group[0][1]}..{group[-1][1]} "
                    f"({len(group)}日) -> {len(meeting_ids)} meetings"
                )
    # Groups whose days all have `meeting_id IS NULL` -- races created by a
    # K-file load with no B-file card -- gain no meeting here, so counting
    # every group would make the dry run predict one row more than `--apply`
    # actually leaves behind.
    stats.meetings_after = groups_with_a_meeting
    stats.meetings_deleted = max(stats.meetings_before - groups_with_a_meeting, 0)
    return groups, stats


def rebuild_meetings(session: Session, *, apply: bool = False) -> RebuildStats:
    """Re-point every race at the meeting its 節 resolves to.

    With `apply=False` (the default) nothing is written and the returned
    statistics describe what would change.
    """
    days = _venue_days(session)
    groups, stats = plan_rebuild(session, days)

    survivors: dict[int, object] = {}
    repointed = 0
    for index, group in enumerate(groups):
        # The meeting of the earliest day survives: for a 節 that starts
        # at 第1日 its meeting_start_date is already the true start, so
        # keeping it leaves the surviving row's key meaningful.
        survivor_id = None
        for key in group:
            ids = days[key]["meeting_ids"]
            if ids:
                survivor_id = min(ids, key=str)
                break
        if survivor_id is None:
            continue
        survivors[index] = survivor_id
        for key in group:
            if days[key]["meeting_ids"] - {survivor_id}:
                repointed += len(days[key]["race_ids"])
    stats.races_repointed = repointed

    if not apply:
        return stats

    _create_backup_table(session)
    for index, group in enumerate(groups):
        survivor_id = survivors.get(index)
        if survivor_id is None:
            continue
        merged_ids = {mid for key in group for mid in days[key]["meeting_ids"]}
        for key in group:
            if not days[key]["meeting_ids"] - {survivor_id}:
                continue
            session.execute(
                Race.__table__.update()
                .where(Race.venue_id == key[0], Race.race_date == key[1])
                .values(meeting_id=survivor_id)
            )
        survivor = session.get(RaceMeeting, survivor_id)
        if survivor is not None and survivor.meeting_title is None:
            # A merged-away duplicate may carry the title the survivor
            # lacks: 第1日 often has none while later days do.
            for merged_id in sorted(merged_ids - {survivor_id}, key=str):
                merged = session.get(RaceMeeting, merged_id)
                if merged is not None and merged.meeting_title is not None:
                    survivor.meeting_title = merged.meeting_title
                    break
    session.flush()

    orphan_ids = [
        row[0]
        for row in session.execute(
            select(RaceMeeting.id).where(
                ~select(Race.id).where(Race.meeting_id == RaceMeeting.id).exists()
            )
        )
    ]
    if orphan_ids:
        session.execute(delete(RaceMeeting).where(RaceMeeting.id.in_(orphan_ids)))
    stats.meetings_deleted = len(orphan_ids)
    session.flush()
    return stats


def _create_backup_table(session: Session) -> None:
    exists = session.bind.dialect.has_table(session.connection(), BACKUP_TABLE)
    if exists:
        raise RebuildMeetingsError(
            f"{BACKUP_TABLE} already exists -- a previous run's backup would be "
            "overwritten. Drop or rename it once its rollback is no longer needed."
        )
    session.execute(
        text(f"CREATE TABLE {BACKUP_TABLE} AS SELECT id, meeting_id FROM races")
    )


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=None)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the change (default is a dry run that writes nothing)",
    )
    args = parser.parse_args(argv)

    engine = create_db_engine(args.database_url)
    try:
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            stats = rebuild_meetings(session, apply=args.apply)
            if args.apply:
                session.commit()
            else:
                session.rollback()
    finally:
        engine.dispose()

    print(("applied: " if args.apply else "dry-run: ") + str(stats))
    for example in stats.examples:
        print(f"  split: {example}")
    if not args.apply:
        print("nothing was written; re-run with --apply to make the change")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
