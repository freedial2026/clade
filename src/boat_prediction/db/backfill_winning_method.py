"""Fill `race_results.winning_method` from the K-file archive.

The loader now reads 決まり手 (`kfile_parser._RESULT_HEADER_RE`), but the
1.15 M results already in the database were loaded before it did and hold
NULL. Re-running `load_archive` would fix them and would also delete and
reinsert roughly 19 M rows to change one column — so this walks the same
archive and issues targeted UPDATEs instead.

Safe by construction: it only ever sets `winning_method`, only where it is
currently NULL, and only for a race the archive actually names. Nothing is
deleted, no other column is touched, and a race the file does not give a
technique for is left NULL rather than guessed.

Dry-run by default, in the manner of `rebuild_meetings.py`; `--apply`
commits. Resumable through the same ledger idea as `load_archive`, keyed
by month, so an interrupted run does not re-parse what it already did.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select, update

from ..kfile_parser import parse_k_file_text
from ..official_source import extract_k_file_text
from .models import Race, RaceResult, Venue
from .session import create_db_engine, create_session_factory

DEFAULT_ARCHIVE = Path("data/raw/boatrace/K")
DEFAULT_LEDGER = Path("data/manifests/winning_method_backfill_ledger.json")


@dataclass
class BackfillStats:
    files: int = 0
    files_skipped: int = 0
    races_in_files: int = 0
    matched: int = 0
    updated: int = 0
    no_method_in_file: int = 0
    race_not_found: int = 0
    already_set: int = 0
    failures: list[str] = None

    def __post_init__(self) -> None:
        if self.failures is None:
            self.failures = []

    def __str__(self) -> str:
        return (
            f"files={self.files} skipped={self.files_skipped} "
            f"races_in_files={self.races_in_files} matched={self.matched} "
            f"updated={self.updated} no_method_in_file={self.no_method_in_file} "
            f"race_not_found={self.race_not_found} already_set={self.already_set} "
            f"failures={len(self.failures)}"
        )


def _date_from_name(path: Path) -> dt.date | None:
    stem = path.stem
    if len(stem) != 7 or not stem[1:].isdigit():
        return None
    try:
        return dt.date(2000 + int(stem[1:3]), int(stem[3:5]), int(stem[5:7]))
    except ValueError:
        return None


def backfill_file(session, path: Path, *, apply: bool) -> BackfillStats:
    stats = BackfillStats(files=1)
    race_date = _date_from_name(path)
    if race_date is None:
        stats.failures.append(f"{path.name}: unparsable filename")
        return stats

    venues = parse_k_file_text(extract_k_file_text(path))
    venue_ids = dict(session.execute(select(Venue.code, Venue.id)).all())

    for venue_day in venues:
        venue_id = venue_ids.get(venue_day.venue_code)
        if venue_id is None:
            continue
        for parsed in venue_day.races:
            stats.races_in_files += 1
            if not parsed.winning_method:
                stats.no_method_in_file += 1
                continue
            row = session.execute(
                select(RaceResult.id, RaceResult.winning_method)
                .join(Race, Race.id == RaceResult.race_id)
                .where(
                    Race.venue_id == venue_id,
                    Race.race_date == race_date,
                    Race.race_number == parsed.race_number,
                )
            ).first()
            if row is None:
                stats.race_not_found += 1
                continue
            stats.matched += 1
            if row[1] is not None:
                stats.already_set += 1
                continue
            if apply:
                session.execute(
                    update(RaceResult)
                    .where(RaceResult.id == row[0])
                    .values(winning_method=parsed.winning_method)
                )
            stats.updated += 1
    return stats


def _load_ledger(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    return set(json.loads(path.read_text(encoding="utf-8")).get("files", []))


def _save_ledger(path: Path, done: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"files": sorted(done)}, indent=2) + "\n", encoding="utf-8")


def backfill_archive(
    session_factory,
    *,
    archive_dir: Path,
    apply: bool,
    ledger_path: Path,
    log=print,
) -> BackfillStats:
    total = BackfillStats()
    done = _load_ledger(ledger_path) if apply else set()

    for path in sorted(archive_dir.rglob("k*.lzh")):
        if path.name in done:
            total.files_skipped += 1
            continue
        with session_factory() as session:
            try:
                one = backfill_file(session, path, apply=apply)
            except Exception as exc:  # noqa: BLE001 -- one bad file must not end the run
                session.rollback()
                total.failures.append(f"{path.name}: {exc}")
                continue
            if apply:
                session.commit()

        total.files += one.files
        total.races_in_files += one.races_in_files
        total.matched += one.matched
        total.updated += one.updated
        total.no_method_in_file += one.no_method_in_file
        total.race_not_found += one.race_not_found
        total.already_set += one.already_set
        total.failures.extend(one.failures)

        if apply:
            done.add(path.name)
            if total.files % 200 == 0:
                _save_ledger(ledger_path, done)
                log(f"{path.name}: {total}")

    if apply:
        _save_ledger(ledger_path, done)
    return total


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--archive-dir", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument(
        "--apply", action="store_true", help="commit; without it nothing is written"
    )
    args = parser.parse_args(argv)

    engine = create_db_engine(args.database_url)
    try:
        stats = backfill_archive(
            create_session_factory(engine),
            archive_dir=args.archive_dir,
            apply=args.apply,
            ledger_path=args.ledger,
        )
    finally:
        engine.dispose()

    print(("applied: " if args.apply else "dry-run (nothing written): ") + str(stats))
    for message in stats.failures[:10]:
        print(f"  {message}")
    return 1 if stats.failures else 0


if __name__ == "__main__":
    raise SystemExit(_main())
