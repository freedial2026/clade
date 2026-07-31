"""Walk the downloaded JMA daily-weather archive and load it into
`weather_observations`.

Layout matches `jma_weather_source.fetch_all`: one file per venue per
month, covering that whole month's daily values --
`data/raw/boatrace/jma/{venue_code}/{YYYYMM}.html`. Iteration is
therefore over (year, month) x venue rather than over dates, unlike
`load_archive.py`/`load_odds_archive.py`.

Idempotent the same two ways as the other archive loaders:

- `loader.load_weather_month` fully replaces a venue-month's existing
  observations, so re-running any range is always safe.
- A JSON ledger keyed by `("jma_weather", source_file_hash)`
  (`data/manifests/jma_load_ledger.json` by default) lets a re-run skip
  files it already loaded.

One file's failure does not stop the run: it is recorded in
`JmaArchiveLoadResult.failed` and the walk continues, matching the other
loaders' partial-failure recovery.

`--dry-run` parses and would-load every file without committing or
updating the ledger.
"""

from __future__ import annotations

import argparse
import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

from ..checksums import sha256_file
from ..jma_weather_source import VENUE_STATIONS, JmaWeatherSourceError, parse_daily_month_html
from ..json_store import read_json_or_default, write_json
from . import loader
from .loader import JST
from .session import create_db_engine, create_session_factory

DEFAULT_RAW_ROOT = Path("data/raw/boatrace/jma")
DEFAULT_LEDGER_PATH = Path("data/manifests/jma_load_ledger.json")
DEFAULT_START_DATE = dt.date(2005, 1, 1)

_KIND_JMA = "jma_weather"


class JmaArchiveLoadError(ValueError):
    """Raised for invalid input to the archive walk itself (not a
    per-file failure, which is recorded rather than raised -- see the
    module docstring)."""


@dataclass
class JmaArchiveLoadResult:
    stats: loader.WeatherLoadStats = field(default_factory=loader.WeatherLoadStats)
    loaded_files: int = 0
    skipped_already_loaded: int = 0
    skipped_missing: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)


def _file_path(raw_root: Path, venue_code: str, year: int, month: int) -> Path:
    return raw_root / venue_code / f"{year:04d}{month:02d}.html"


def _iter_year_months(start_date: dt.date, end_date: dt.date):
    if end_date < start_date:
        raise JmaArchiveLoadError(f"end_date {end_date} is before start_date {start_date}")
    year, month = start_date.year, start_date.month
    end_year, end_month = end_date.year, end_date.month
    while (year, month) <= (end_year, end_month):
        yield year, month
        month += 1
        if month > 12:
            month = 1
            year += 1


def _ledger_key(file_hash: str) -> str:
    return f"{_KIND_JMA}:{file_hash}"


def load_jma_archive(
    raw_root: Path,
    session_factory,
    *,
    start_date: dt.date = DEFAULT_START_DATE,
    end_date: dt.date | None = None,
    ledger_path: Path = DEFAULT_LEDGER_PATH,
    dry_run: bool = False,
    force: bool = False,
    progress: object | None = print,
    parse: object = parse_daily_month_html,
) -> JmaArchiveLoadResult:
    """Load every archived JMA venue-month page in `[start_date, end_date]`.

    `session_factory` is a `sqlalchemy.orm.sessionmaker` (injectable so
    tests can point this at an in-memory database). `parse` defaults to
    `jma_weather_source.parse_daily_month_html` and is injectable for the
    same reason: tests should not need real fetched HTML on disk.
    """
    if not raw_root.is_dir():
        raise JmaArchiveLoadError(f"raw_root is not an existing directory: {raw_root}")
    # JST, not the host's local date, matching every other loader's date
    # semantics in this project.
    end_date = end_date or dt.datetime.now(JST).date()

    ledger = (
        {} if dry_run else dict(read_json_or_default(ledger_path, {}, error_type=JmaArchiveLoadError))
    )
    result = JmaArchiveLoadResult()

    session = session_factory()
    try:
        # Committed unconditionally, same reasoning as the other archive
        # loaders: idempotent/non-destructive, so the per-file rollback
        # below never has to worry about undoing it.
        loader.ensure_reference_data(session)
        session.commit()

        for year, month in _iter_year_months(start_date, end_date):
            for venue_code in sorted(VENUE_STATIONS):
                path = _file_path(raw_root, venue_code, year, month)
                if not path.is_file():
                    result.skipped_missing += 1
                    continue

                file_hash = sha256_file(path)
                key = _ledger_key(file_hash)
                if not force and key in ledger:
                    result.skipped_already_loaded += 1
                    continue

                relative = path.relative_to(raw_root).as_posix()
                try:
                    html = path.read_text(encoding="utf-8")
                    daily_weathers = parse(html, year, month)
                    month_stats = loader.load_weather_month(
                        session, venue_code, year, month, daily_weathers
                    )
                except JmaWeatherSourceError as exc:
                    session.rollback()
                    result.failed.append((relative, str(exc)))
                    continue

                if dry_run:
                    session.rollback()
                else:
                    session.commit()
                    ledger[key] = {
                        "loaded_at": dt.datetime.now(dt.UTC).isoformat(),
                        "year_month": f"{year:04d}-{month:02d}",
                    }
                # See load_archive.py's identical call for why: a single
                # Session runs for the whole archive, so committed objects
                # must be detached to avoid unbounded identity-map growth.
                session.expunge_all()
                result.stats = result.stats.merge(month_stats)
                result.loaded_files += 1

            if progress is not None:
                progress(
                    f"{year:04d}-{month:02d}: loaded={result.loaded_files} "
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
        result = load_jma_archive(
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
