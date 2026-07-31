"""Walk the downloaded closing-odds archive and load it into `odds_snapshots`.

Mirrors `load_archive.py`'s ledger/dry-run/injectable-session-factory
pattern, but the on-disk layout `odds_source.fetch_range` actually
produced is per day+race, not per month
(`data/raw/boatrace/odds/{YYYYMMDD}/{venue}_{race}.html`), so a day's
files are discovered by globbing that day's directory rather than
computing one fixed filename per date.

Idempotent the same two ways as `load_archive.py`:

- `loader.load_odds_day` fully replaces a race's existing snapshots, so
  re-running any date range is always safe.
- A JSON ledger keyed by `("odds", source_file_hash)`
  (`data/manifests/odds_load_ledger.json` by default) lets a re-run skip
  files it already loaded.

One file's failure does not stop the run: it is recorded in
`OddsArchiveLoadResult.failed` and the walk continues, matching
`load_archive.py`'s partial-failure recovery.

`--dry-run` parses and would-load every file without committing or
updating the ledger.
"""

from __future__ import annotations

import argparse
import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

from ..checksums import sha256_file
from ..json_store import read_json_or_default, write_json
from ..odds_source import EARLIEST_RETAINED_DATE, OddsSourceError, parse_win_place_odds
from ..race_id import VALID_VENUE_CODES
from . import loader
from .loader import JST
from .session import create_db_engine, create_session_factory

DEFAULT_RAW_ROOT = Path("data/raw/boatrace/odds")
DEFAULT_LEDGER_PATH = Path("data/manifests/odds_load_ledger.json")
DEFAULT_START_DATE = EARLIEST_RETAINED_DATE

_KIND_ODDS = "odds"


class OddsArchiveLoadError(ValueError):
    """Raised for invalid input to the archive walk itself (not a
    per-file failure, which is recorded rather than raised -- see the
    module docstring)."""


@dataclass
class OddsArchiveLoadResult:
    stats: loader.OddsLoadStats = field(default_factory=loader.OddsLoadStats)
    loaded_files: int = 0
    skipped_already_loaded: int = 0
    skipped_missing: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)


def _day_dir(raw_root: Path, target_date: dt.date) -> Path:
    return raw_root / target_date.strftime("%Y%m%d")


def _iter_dates(start_date: dt.date, end_date: dt.date):
    if end_date < start_date:
        raise OddsArchiveLoadError(f"end_date {end_date} is before start_date {start_date}")
    current = start_date
    one_day = dt.timedelta(days=1)
    while current <= end_date:
        yield current
        current += one_day


def _ledger_key(file_hash: str) -> str:
    return f"{_KIND_ODDS}:{file_hash}"


def _race_files(day_dir: Path) -> list[tuple[str, int, Path]]:
    """Discover `(venue_code, race_number, path)` triples in a day's
    directory, skipping the `_venues.txt` marker and anything that
    doesn't match the `{venue}_{race}.html` shape `fetch_range` writes."""
    found = []
    for path in sorted(day_dir.glob("*_*.html")):
        venue_code, _, race_part = path.stem.partition("_")
        if venue_code in VALID_VENUE_CODES and race_part.isdigit():
            found.append((venue_code, int(race_part), path))
    return found


def load_odds_archive(
    raw_root: Path,
    session_factory,
    *,
    start_date: dt.date = DEFAULT_START_DATE,
    end_date: dt.date | None = None,
    ledger_path: Path = DEFAULT_LEDGER_PATH,
    dry_run: bool = False,
    force: bool = False,
    progress: object | None = print,
    parse: object = parse_win_place_odds,
) -> OddsArchiveLoadResult:
    """Load every archived closing-odds page in `[start_date, end_date]`.

    `session_factory` is a `sqlalchemy.orm.sessionmaker` (injectable so
    tests can point this at an in-memory database). `parse` defaults to
    `odds_source.parse_win_place_odds` and is injectable for the same
    reason: tests should not need real fetched HTML on disk.
    """
    if not raw_root.is_dir():
        raise OddsArchiveLoadError(f"raw_root is not an existing directory: {raw_root}")
    # JST, not the host's local date: race days are defined in JST
    # everywhere else in this loader.
    end_date = end_date or dt.datetime.now(JST).date()

    ledger = (
        {} if dry_run else dict(read_json_or_default(ledger_path, {}, error_type=OddsArchiveLoadError))
    )
    result = OddsArchiveLoadResult()

    session = session_factory()
    try:
        # Committed unconditionally, same reasoning as load_archive.py:
        # idempotent/non-destructive, so the per-file rollback below never
        # has to worry about undoing it.
        loader.ensure_reference_data(session)
        session.commit()

        for target_date in _iter_dates(start_date, end_date):
            day_dir = _day_dir(raw_root, target_date)
            if not day_dir.is_dir():
                result.skipped_missing += 1
                continue

            for venue_code, race_number, path in _race_files(day_dir):
                file_hash = sha256_file(path)
                key = _ledger_key(file_hash)
                if not force and key in ledger:
                    result.skipped_already_loaded += 1
                    continue

                relative = path.relative_to(raw_root).as_posix()
                try:
                    html = path.read_text(encoding="utf-8")
                    race_odds = parse(html)
                    day_stats = loader.load_odds_day(
                        session, venue_code, target_date, race_number, race_odds
                    )
                except (OddsSourceError, loader.LoaderError) as exc:
                    session.rollback()
                    result.failed.append((relative, str(exc)))
                    continue

                if dry_run:
                    session.rollback()
                else:
                    session.commit()
                    ledger[key] = {
                        "loaded_at": dt.datetime.now(dt.UTC).isoformat(),
                        "race_date": target_date.isoformat(),
                    }
                # See load_archive.py's identical call for why: a single
                # Session runs for the whole archive, so committed objects
                # must be detached to avoid unbounded identity-map growth.
                session.expunge_all()
                result.stats = result.stats.merge(day_stats)
                result.loaded_files += 1

            if progress is not None and target_date.day == 1:
                progress(
                    f"{target_date:%Y-%m}: loaded={result.loaded_files} "
                    f"skipped_missing={result.skipped_missing} "
                    f"skipped_loaded={result.skipped_already_loaded} "
                    f"failed={len(result.failed)}"
                )
    finally:
        session.close()

    if not dry_run:
        write_json(ledger_path, ledger)

    return result


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    parser.add_argument(
        "--start-date", type=dt.date.fromisoformat, default=DEFAULT_START_DATE
    )
    parser.add_argument("--end-date", type=dt.date.fromisoformat, default=None)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER_PATH)
    parser.add_argument("--database-url", default=None)
    parser.add_argument(
        "--dry-run", action="store_true", help="parse and validate without writing to the database"
    )
    parser.add_argument(
        "--force", action="store_true", help="reload files already recorded in the ledger"
    )
    args = parser.parse_args(argv)

    engine = create_db_engine(args.database_url)
    session_factory = create_session_factory(engine)
    try:
        result = load_odds_archive(
            args.raw_root,
            session_factory,
            start_date=args.start_date,
            end_date=args.end_date,
            ledger_path=args.ledger,
            dry_run=args.dry_run,
            force=args.force,
        )
    finally:
        engine.dispose()

    print(
        f"done: loaded_files={result.loaded_files} "
        f"skipped_missing={result.skipped_missing} "
        f"skipped_already_loaded={result.skipped_already_loaded} "
        f"failed={len(result.failed)}"
    )
    print(result.stats)
    for relative_path, message in result.failed[:50]:
        print(f"  failed: {relative_path}: {message}")
    if len(result.failed) > 50:
        print(f"  ... and {len(result.failed) - 50} more")
    return 1 if result.failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
