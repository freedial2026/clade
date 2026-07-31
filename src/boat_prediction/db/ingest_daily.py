"""Download and load one day's race card or results.

The archive loaders (`load_archive`) only read files already on disk;
the downloaders (`b_file_source`, `official_source`) only write them.
Daily operation needs the two joined, and needs it to be safe to run
from cron, which is what this is.

Two kinds, because they are published at opposite ends of a racing day:

- `card` (B-file, 番組表) is published the day *before* it races, and it
  is what `db.capture_odds` keys on: without it the database has no
  `scheduled_deadline_at` for today and nothing can be captured. Run it
  in the early morning, before the day's first deadline (the earliest
  seen in the archive is 08:32 JST).
- `results` (K-file) is published after racing ends, so a run defaults
  to *yesterday*. Asking for today's results before the last race has
  finished would fetch a 404 or a partial file.

Both steps are idempotent. The download overwrites its file, and
`load_archive`'s ledger plus the loaders' replace-then-reinsert
behaviour mean re-running a date changes nothing. A failed download
leaves the previous file untouched.

Exit codes matter here because cron reports them: 0 when the day was
downloaded and loaded, 1 when anything failed. A date that simply has
no racing is not a failure -- the source returns a 404 for it, which is
reported as `skipped_missing` and exits 0, so a quiet day does not page
anyone.
"""

from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

from ..b_file_source import BFileSourceError, download_b_file
from ..official_source import OfficialSourceError, download_k_file
from . import load_archive
from .load_archive import DEFAULT_LEDGER_PATH, DEFAULT_RAW_ROOT
from .loader import JST
from .session import create_db_engine, create_session_factory

KIND_CARD = "card"
KIND_RESULTS = "results"


class IngestDailyError(ValueError):
    """Raised for invalid input to the daily ingest itself."""


def default_date_for(kind: str, *, now: dt.datetime | None = None) -> dt.date:
    """Today in JST for a card, yesterday for results.

    Not UTC: a racing day is a JST calendar day, and around midnight UTC
    the two disagree by one -- which would silently ingest the wrong day.
    """
    now = now or dt.datetime.now(dt.UTC)
    today = now.astimezone(JST).date()
    return today if kind == KIND_CARD else today - dt.timedelta(days=1)


def download_for(
    kind: str,
    target_date: dt.date,
    raw_root: Path,
    *,
    opener: object | None = None,
) -> Path:
    """Download one day's file into the archive layout `load_archive`
    expects (`{raw_root}/{B|K}/{YYYYMM}/{b|k}{YYMMDD}.lzh`)."""
    if kind == KIND_CARD:
        dest = raw_root / "B" / target_date.strftime("%Y%m")
        return download_b_file(target_date, dest, opener=opener)
    if kind == KIND_RESULTS:
        dest = raw_root / "K" / target_date.strftime("%Y%m")
        return download_k_file(target_date, dest, opener=opener)
    raise IngestDailyError(f"unknown kind: {kind!r}")


def ingest_day(
    kind: str,
    target_date: dt.date,
    raw_root: Path,
    session_factory,
    *,
    ledger_path: Path = DEFAULT_LEDGER_PATH,
    opener: object | None = None,
    progress: object | None = print,
) -> load_archive.ArchiveLoadResult:
    """Download `target_date`'s file, then load that single date.

    The load covers the one date only, so a cron run does no work
    proportional to the size of the archive behind it.
    """
    download_for(kind, target_date, raw_root, opener=opener)
    return load_archive.load_archive(
        raw_root,
        session_factory,
        start_date=target_date,
        end_date=target_date,
        ledger_path=ledger_path,
        progress=progress,
    )


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "kind",
        choices=(KIND_CARD, KIND_RESULTS),
        help="card = today's B-file (race card); results = yesterday's K-file",
    )
    parser.add_argument(
        "--date",
        type=dt.date.fromisoformat,
        default=None,
        help="override the date (default: today in JST for card, yesterday for results)",
    )
    parser.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER_PATH)
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args(argv)

    target_date = args.date or default_date_for(args.kind)

    engine = create_db_engine(args.database_url)
    try:
        session_factory = create_session_factory(engine)
        try:
            result = ingest_day(
                args.kind,
                target_date,
                args.raw_root,
                session_factory,
                ledger_path=args.ledger,
            )
        except (BFileSourceError, OfficialSourceError) as exc:
            # A day with no racing 404s here. It is not an error worth
            # waking anyone for, but it is worth saying out loud.
            print(f"{args.kind} {target_date}: download failed: {exc}")
            return 1
    finally:
        engine.dispose()

    print(
        f"done: kind={args.kind} date={target_date} "
        f"loaded_files={result.loaded_files} "
        f"skipped_missing={result.skipped_missing} "
        f"skipped_already_loaded={result.skipped_already_loaded} "
        f"failed={len(result.failed)}"
    )
    print(result.stats)
    for relative_path, message in result.failed[:20]:
        print(f"  failed: {relative_path}: {message}")
    return 1 if result.failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
