"""Walk the downloaded 3連単/3連複/拡連複 odds archive and load it into
`odds_snapshots`.

Mirrors `load_odds_archive.py`'s ledger/dry-run/injectable-session-factory
pattern exactly, adapted for the one real structural difference: these
three pools live on three separate pages
(`odds_source.fetch_trifecta_family_range` writes
`data/raw/boatrace/odds_trifecta/{YYYYMMDD}/{venue}_{race}_{page}.html`,
`page` one of `odds3t`/`odds3f`/`oddsk`), so a day's directory has three
files per race instead of `load_odds_archive.py`'s one, and each needs
its own parser (`_PAGE_PARSERS`) and lands through
`loader.load_combination_odds_archive_day` rather than `load_odds_day` --
that function replaces only the bet_type(s) a call carries, not the
race's whole snapshot set, since 単勝/複勝/2連単/2連複 are typically
already on the race by the time this runs.

Idempotent the same two ways as `load_odds_archive.py`:

- `load_combination_odds_archive_day` replaces just the bet_types one
  call carries, so re-running any date range is always safe.
- A JSON ledger keyed by `("trifecta", page, source_file_hash)`
  (`data/manifests/trifecta_load_ledger.json` by default) lets a re-run
  skip files it already loaded. A separate ledger from
  `odds_load_ledger.json`'s, since the two loaders never share a key
  space and mixing them would just be two loaders' bookkeeping tangled
  in one file for no benefit.

One file's failure does not stop the run: it is recorded in
`TrifectaArchiveLoadResult.failed` and the walk continues.

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
from ..odds_source import (
    EARLIEST_RETAINED_DATE,
    OddsSourceError,
    parse_sanrenpuku_odds,
    parse_trifecta_odds,
    parse_wide_odds,
)
from ..race_id import VALID_VENUE_CODES
from . import loader
from .loader import JST
from .session import create_db_engine, create_session_factory

DEFAULT_RAW_ROOT = Path("data/raw/boatrace/odds_trifecta")
DEFAULT_LEDGER_PATH = Path("data/manifests/trifecta_load_ledger.json")
DEFAULT_START_DATE = EARLIEST_RETAINED_DATE

_KIND_TRIFECTA = "trifecta"

_PAGE_PARSERS = {
    "odds3t": parse_trifecta_odds,
    "odds3f": parse_sanrenpuku_odds,
    "oddsk": parse_wide_odds,
}
"""Which parser reads which page suffix -- the same three pages
`odds_source.fetch_trifecta_family_range` fetches, in the same order
`evaluate_bet_types.BET_TYPE_SPECS` expects their `bet_type` values in."""


class TrifectaArchiveLoadError(ValueError):
    """Raised for invalid input to the archive walk itself (not a
    per-file failure, which is recorded rather than raised -- see the
    module docstring)."""


@dataclass
class TrifectaArchiveLoadResult:
    stats: loader.OddsLoadStats = field(default_factory=loader.OddsLoadStats)
    loaded_files: int = 0
    skipped_already_loaded: int = 0
    skipped_missing: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)


def _day_dir(raw_root: Path, target_date: dt.date) -> Path:
    return raw_root / target_date.strftime("%Y%m%d")


def _iter_dates(start_date: dt.date, end_date: dt.date):
    if end_date < start_date:
        raise TrifectaArchiveLoadError(f"end_date {end_date} is before start_date {start_date}")
    current = start_date
    one_day = dt.timedelta(days=1)
    while current <= end_date:
        yield current
        current += one_day


def _ledger_key(page: str, file_hash: str) -> str:
    return f"{_KIND_TRIFECTA}:{page}:{file_hash}"


def _race_files(day_dir: Path, parsers: dict) -> list[tuple[str, int, str, Path]]:
    """Discover `(venue_code, race_number, page, path)` quadruples in a
    day's directory, skipping the `_venues.txt` marker (shared with the
    win/place fetcher when pointed at the same directory) and anything
    that doesn't match the `{venue}_{race}_{page}.html` shape
    `fetch_trifecta_family_range` writes."""
    found = []
    for path in sorted(day_dir.glob("*_*_*.html")):
        venue_code, _, remainder = path.stem.partition("_")
        race_part, _, page = remainder.partition("_")
        if venue_code in VALID_VENUE_CODES and race_part.isdigit() and page in parsers:
            found.append((venue_code, int(race_part), page, path))
    return found


def load_trifecta_archive(
    raw_root: Path,
    session_factory,
    *,
    start_date: dt.date = DEFAULT_START_DATE,
    end_date: dt.date | None = None,
    ledger_path: Path = DEFAULT_LEDGER_PATH,
    dry_run: bool = False,
    force: bool = False,
    progress: object | None = print,
    parsers: dict = _PAGE_PARSERS,
) -> TrifectaArchiveLoadResult:
    """Load every archived 3連単/3連複/拡連複 page in `[start_date, end_date]`.

    `session_factory` is a `sqlalchemy.orm.sessionmaker` (injectable so
    tests can point this at an in-memory database). `parsers` defaults to
    `_PAGE_PARSERS` and is injectable for the same reason
    `load_odds_archive`'s `parse` is: tests should not need real fetched
    HTML on disk, just a placeholder file at the expected path.
    """
    if not raw_root.is_dir():
        raise TrifectaArchiveLoadError(f"raw_root is not an existing directory: {raw_root}")
    # JST, not the host's local date: race days are defined in JST
    # everywhere else in this loader.
    end_date = end_date or dt.datetime.now(JST).date()

    ledger = (
        {}
        if dry_run
        else dict(read_json_or_default(ledger_path, {}, error_type=TrifectaArchiveLoadError))
    )
    result = TrifectaArchiveLoadResult()

    session = session_factory()
    try:
        # Committed unconditionally, same reasoning as load_odds_archive.py:
        # idempotent/non-destructive, so the per-file rollback below never
        # has to worry about undoing it.
        loader.ensure_reference_data(session)
        session.commit()

        for target_date in _iter_dates(start_date, end_date):
            day_dir = _day_dir(raw_root, target_date)
            if not day_dir.is_dir():
                result.skipped_missing += 1
                continue

            for venue_code, race_number, page, path in _race_files(day_dir, parsers):
                file_hash = sha256_file(path)
                key = _ledger_key(page, file_hash)
                if not force and key in ledger:
                    result.skipped_already_loaded += 1
                    continue

                relative = path.relative_to(raw_root).as_posix()
                try:
                    html = path.read_text(encoding="utf-8")
                    race_odds = parsers[page](html)
                    day_stats = loader.load_combination_odds_archive_day(
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
                        "page": page,
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
        result = load_trifecta_archive(
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
