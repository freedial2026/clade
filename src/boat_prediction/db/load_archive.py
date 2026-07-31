"""Walk the downloaded B-file/K-file archive and load it into the schema.

Reusable entry point for the "load 21 years of data" step flagged as not
yet done at the end of every prior B-file/K-file HANDOFF entry. Layout
matches what `official_source.py`/`b_file_source.py` actually downloaded
(tasks/HANDOFF.md): `data/raw/boatrace/{K,B}/{YYYYMM}/{k,b}{YYMMDD}.lzh`.

Idempotent two ways at once, matching `ingest.py`'s ledger pattern:

- The DB writes themselves are idempotent per `(race_date, venue_code)`
  (`loader.load_b_file_day`/`load_k_file_day` fully replace that day's
  rows), so re-running any date range is always safe.
- A JSON ledger keyed by `(kind, source_file_hash)`
  (`data/manifests/db_load_ledger.json` by default) additionally lets a
  re-run *skip* files it already loaded, which matters at this archive's
  actual size (~15,700 files) -- without it, resuming after an
  interruption would mean re-parsing and re-loading everything already
  done, not just what failed.

One file's failure (corrupt archive, parse defect) does not stop the
run: it is recorded in `ArchiveLoadResult.failed` and the date range
continues, mirroring `ingest_directory`'s partial-failure recovery
(rule 03: fail clearly, but don't let one bad input block a large batch).

`--dry-run` parses and would-load every file without committing or
updating the ledger, per rule 03's idempotent-or-dry-run requirement --
useful for checking a date range parses cleanly before the real load.
"""

from __future__ import annotations

import argparse
import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

from ..bfile_parser import BFileParseError, parse_b_file_text
from ..checksums import sha256_file
from ..json_store import read_json_or_default, write_json
from ..kfile_parser import KFileParseError, parse_k_file_text
from ..official_source import OfficialSourceError, extract_k_file_text
from . import loader
from .loader import JST
from .session import create_db_engine, create_session_factory

DEFAULT_RAW_ROOT = Path("data/raw/boatrace")
DEFAULT_LEDGER_PATH = Path("data/manifests/db_load_ledger.json")
DEFAULT_START_DATE = dt.date(2005, 1, 1)

_KIND_B = "b_file"
_KIND_K = "k_file"


class ArchiveLoadError(ValueError):
    """Raised for invalid input to the archive walk itself (not a
    per-file failure, which is recorded rather than raised -- see the
    module docstring)."""


@dataclass
class ArchiveLoadResult:
    stats: loader.LoadStats = field(default_factory=loader.LoadStats)
    loaded_files: int = 0
    skipped_already_loaded: int = 0
    skipped_missing: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)


def _file_path(raw_root: Path, kind: str, target_date: dt.date) -> Path:
    prefix, subdir = ("b", "B") if kind == _KIND_B else ("k", "K")
    return raw_root / subdir / target_date.strftime("%Y%m") / f"{prefix}{target_date:%y%m%d}.lzh"


def _iter_dates(start_date: dt.date, end_date: dt.date):
    if end_date < start_date:
        raise ArchiveLoadError(f"end_date {end_date} is before start_date {start_date}")
    current = start_date
    one_day = dt.timedelta(days=1)
    while current <= end_date:
        yield current
        current += one_day


def _ledger_key(kind: str, file_hash: str) -> str:
    return f"{kind}:{file_hash}"


def load_archive(
    raw_root: Path,
    session_factory,
    *,
    start_date: dt.date = DEFAULT_START_DATE,
    end_date: dt.date | None = None,
    ledger_path: Path = DEFAULT_LEDGER_PATH,
    dry_run: bool = False,
    force: bool = False,
    progress: object | None = print,
    extract_text: object = extract_k_file_text,
) -> ArchiveLoadResult:
    """Load every archived B-file and K-file in `[start_date, end_date]`.

    `session_factory` is a `sqlalchemy.orm.sessionmaker` (injectable so
    tests can point this at an in-memory database rather than a real
    Postgres). `extract_text` defaults to
    `official_source.extract_k_file_text` (LZH -> Shift-JIS text,
    generic despite the name -- reused for B-files too) and is
    injectable for the same reason `official_source.download_k_file`
    takes an `opener`: tests should not need real `.lzh` archives on
    disk. K-files are loaded before B-files for each date so a B-file
    that arrives first still gets its results linked immediately by
    `loader.load_b_file_day`'s `_relink_result_entries` call, rather
    than only on the next re-run.
    """
    if not raw_root.is_dir():
        raise ArchiveLoadError(f"raw_root is not an existing directory: {raw_root}")
    # JST, not the host's local date: race days are defined in JST
    # everywhere else in this loader (card_available_at,
    # results_available_at), so "today" should mean the same thing here.
    end_date = end_date or dt.datetime.now(JST).date()

    ledger = {} if dry_run else dict(read_json_or_default(ledger_path, {}, error_type=ArchiveLoadError))
    result = ArchiveLoadResult()

    session = session_factory()
    try:
        # Committed unconditionally, even under --dry-run: seeding
        # venues/data_sources is idempotent and non-destructive, and
        # committing it up front means the per-file rollback below (used
        # for both --dry-run and error recovery) never has to worry
        # about accidentally undoing it.
        loader.ensure_reference_data(session)
        session.commit()

        for target_date in _iter_dates(start_date, end_date):
            for kind, parse, load_day, error_type in (
                (_KIND_K, parse_k_file_text, loader.load_k_file_day, KFileParseError),
                (_KIND_B, parse_b_file_text, loader.load_b_file_day, BFileParseError),
            ):
                path = _file_path(raw_root, kind, target_date)
                if not path.is_file():
                    result.skipped_missing += 1
                    continue

                file_hash = sha256_file(path)
                key = _ledger_key(kind, file_hash)
                if not force and key in ledger:
                    result.skipped_already_loaded += 1
                    continue

                relative = path.relative_to(raw_root).as_posix()
                try:
                    text = extract_text(path)
                    parsed = parse(text)
                    day_stats = load_day(session, target_date, parsed)
                except (OfficialSourceError, error_type, loader.LoaderError) as exc:
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
                # A single Session runs for the whole archive (up to
                # ~15,700 files); without detaching committed objects
                # from the identity map, it would grow unboundedly over
                # a 21-year run. Safe because nothing below re-uses these
                # objects -- the next file's lookups (e.g. `_venue`,
                # `_resolve_racers`) re-query the database.
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
        result = load_archive(
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
