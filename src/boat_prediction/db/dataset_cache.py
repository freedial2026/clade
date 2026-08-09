"""On-disk cache for `build_dataset`.

Why this exists
---------------

`build_dataset` takes **50.1 s** for the 2023-01-01..2026-07-29 window
(198,264 races, measured on the runtime host 2026-08-09), and eight
modules call it: `evaluate_p1`, `evaluate_phase`, `evaluate_calibration`,
`evaluate_p2`, `evaluate_p2_walkforward`, `evaluate_bet_types` and
`train_model` -- with `evaluate_p2` and `evaluate_bet_types` calling it
*twice each*, once for train and once for test. Every one of those runs
re-issues identical SQL against identical rows. A single sweep across the
evaluation scripts spends the better part of ten minutes rebuilding the
same arrays.

Nothing about the dataset makes it unsafe to cache. Point-in-time
correctness is enforced *inside* `build_dataset` (the `available_at <=
scheduled_deadline_at` comparison and `dropped_late_feature`), so the
result is a pure function of the date window, the feature flags, the
rows in the database, and the constants the feature code reads. Caching
does not weaken `09-ml-data-science.md`'s leakage rule; it memoises a
function that already satisfies it.

Why `.npz` and not Parquet
--------------------------

Neither pyarrow nor pandas nor polars is installed on the runtime host,
and `docs/PROJECT_PROFILE.md` puts array libraries behind a "when
justified by dataset size" gate that `dataset.py`'s own docstring cites.
Adding a columnar stack to store one float matrix does not clear that
gate. numpy is already present as a scikit-learn dependency and is
already declared through the `ml` extra, and `numpy.savez_compressed`
round-trips float64 bit-exactly -- which matters, because a cached run
must produce the *same* log-loss as an uncached one, not a close one.

Invalidation
------------

This is the whole design problem. A cache that silently returns rows
built under different rules is worse than no cache: the model does not
error on a feature row of the right width with the wrong contents (the
hazard `predict_daily.py` documents for role/width mismatch). So the key
is a hash over two independent fingerprints, and anything that is not
provably irrelevant goes in.

**Recipe fingerprint** -- what changes a feature's *value* for fixed
rows: the resolved column list, `MEETING_FORM_SHRINKAGE_STARTS`,
`MEETING_WINDOW_MARGIN_DAYS`, `COURSE_BASE_WIN_RATE`,
`COURSE_SHRINKAGE_STARTS`, `_CLASS_RANK`, and `CACHE_FORMAT_VERSION`.
Every one of these is a module-level constant in `dataset.py` that the
feature code reads at call time, so editing one changes the output
without changing any signature. `MEETING_FORM_SHRINKAGE_STARTS` is not
hypothetical -- `tasks/CURRENT.md` lists sweeping it as planned work, and
a date-keyed cache would have quietly served the pre-sweep features to
every point in that sweep.

They are hashed unconditionally, including the per-course constants when
`include_racer_stats` is off and they go unread. Over-invalidation costs
one 50 s rebuild; under-invalidation costs a wrong number that looks
right. A future constant added to `dataset.py` must be added here too --
`test_dataset_cache.py` pins the current set so that omission surfaces as
a failing test rather than as a stale cache.

**Data fingerprint** -- `COUNT(*)` and `MAX(updated_at)` for each table
the query reads, over the same window the query reads. `COUNT(*)` catches
inserts and deletes; `MAX(updated_at)` catches in-place `UPDATE`s, which
a `MAX(id)` fingerprint would miss even with uuid7 keys. The loaders here
mostly delete-and-reinsert (either column catches that), but
`backfill_winning_method.py` updates in place, and the next backfill that
touches a column a feature reads would slip straight past an id-only
check.

The window is widened by `MEETING_WINDOW_MARGIN_DAYS` at the front,
because `_MEETING_CTE` reaches back that far for a 節's earlier days --
a race *before* `start_date` can change a feature *inside* it.
`racer_period_course_stats` carries no race date, so it is fingerprinted
whole.

`MAX(updated_at)` relies on `TimestampMixin`'s `onupdate=func.now()`
having finer resolution than the gap between two runs. On PostgreSQL,
which is what the runtime host uses, `now()` is microsecond and
transaction-scoped, so any later transaction gets a strictly greater
value. On SQLite it resolves to `CURRENT_TIMESTAMP` at **one-second**
resolution, so two writes inside the same second are indistinguishable
-- fine for the test suite's fixtures, but a reason not to point this
cache at a SQLite database being written concurrently.

What this does not catch: a change to the *body* of a feature function
that reads no constant, and a database restored to an older state whose
row count and `updated_at` happen to coincide. `refresh=True` forces a
rebuild for both.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import uuid
from dataclasses import asdict
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from . import dataset as _dataset
from .dataset import Dataset, DatasetStats, build_dataset

# The tunable constants are read through `_dataset` at call time rather
# than bound with `from .dataset import ...`, and that is load-bearing
# rather than stylistic. A `from` import fixes the value at *this
# module's* import time, so a sweep that assigns
# `dataset.MEETING_FORM_SHRINKAGE_STARTS = x` between runs -- which is
# how `tasks/CURRENT.md`'s planned sweep would be written -- would move
# the features while leaving this hash frozen, serving every point of
# the sweep the same stale rows. `RecipeInvalidationTest` pins it.

DEFAULT_CACHE_DIR = Path("data/cache/datasets")

CACHE_FORMAT_VERSION = 1
"""Bump when the stored layout changes, or when a `dataset.py` change
alters feature values without altering any constant hashed below. Old
entries then miss rather than being read back under the new rules."""

MAX_CACHE_ENTRIES = 8
"""How many entries to keep, oldest evicted first.

Not optional housekeeping. The data fingerprint is part of the key and
the database gains races every day, so the *same* command run on two
days writes two entries -- a daily evaluation would otherwise grow the
directory without bound. The P1 window measures **29 MB** per entry on
real data (synthetic Gaussian noise compresses far worse and suggested
88 MB, which is why this figure is the one taken from `.21`), so 8 caps
this at roughly 230 MB.

Eviction is by modification time, which is a true LRU only because a hit
does not rewrite the file. `load_dataset` deliberately does not touch
mtime, so an entry read every day still ages out; that is acceptable
because rebuilding it costs one 50 s miss, not a wrong answer.
"""

# `y_second` is nullable and lanes are 1..6, so 0 is free as the "no
# single second place" sentinel -- see `Dataset.y_second` for why that
# case is carried rather than excluded.
_NO_SECOND = 0

_FINGERPRINT_SQL = text(
    """
SELECT
  (SELECT COUNT(*) FROM races r
     WHERE r.race_date BETWEEN :window_start AND :end_date) AS races_n,
  (SELECT MAX(r.updated_at) FROM races r
     WHERE r.race_date BETWEEN :window_start AND :end_date) AS races_t,

  (SELECT COUNT(*) FROM race_entries e JOIN races r ON r.id = e.race_id
     WHERE r.race_date BETWEEN :window_start AND :end_date) AS entries_n,
  (SELECT MAX(e.updated_at) FROM race_entries e JOIN races r ON r.id = e.race_id
     WHERE r.race_date BETWEEN :window_start AND :end_date) AS entries_t,

  (SELECT COUNT(*) FROM race_results s JOIN races r ON r.id = s.race_id
     WHERE r.race_date BETWEEN :window_start AND :end_date) AS results_n,
  (SELECT MAX(s.updated_at) FROM race_results s JOIN races r ON r.id = s.race_id
     WHERE r.race_date BETWEEN :window_start AND :end_date) AS results_t,

  (SELECT COUNT(*) FROM race_result_entries re
     JOIN race_results s ON s.id = re.race_result_id
     JOIN races r ON r.id = s.race_id
     WHERE r.race_date BETWEEN :window_start AND :end_date) AS result_entries_n,
  (SELECT MAX(re.updated_at) FROM race_result_entries re
     JOIN race_results s ON s.id = re.race_result_id
     JOIN races r ON r.id = s.race_id
     WHERE r.race_date BETWEEN :window_start AND :end_date) AS result_entries_t,

  (SELECT COUNT(*) FROM before_info_entries b JOIN races r ON r.id = b.race_id
     WHERE r.race_date BETWEEN :window_start AND :end_date) AS before_info_n,
  (SELECT MAX(b.updated_at) FROM before_info_entries b JOIN races r ON r.id = b.race_id
     WHERE r.race_date BETWEEN :window_start AND :end_date) AS before_info_t,

  (SELECT COUNT(*) FROM racer_period_course_stats) AS course_stats_n,
  (SELECT MAX(updated_at) FROM racer_period_course_stats) AS course_stats_t
"""
)


class DatasetCacheError(RuntimeError):
    """Raised when a cache entry exists but cannot be trusted."""


def recipe_fingerprint(*, include_before_info: bool, include_racer_stats: bool) -> str:
    """Hash of everything that changes a feature's value for fixed rows.

    See the module docstring for why the per-course constants are hashed
    even when `include_racer_stats` leaves them unread.
    """
    payload = {
        "format": CACHE_FORMAT_VERSION,
        "columns": _dataset.feature_columns(
            include_before_info=include_before_info,
            include_racer_stats=include_racer_stats,
        ),
        "meeting_form_shrinkage_starts": _dataset.MEETING_FORM_SHRINKAGE_STARTS,
        "meeting_window_margin_days": _dataset.MEETING_WINDOW_MARGIN_DAYS,
        "course_base_win_rate": {
            str(k): v for k, v in sorted(_dataset.COURSE_BASE_WIN_RATE.items())
        },
        "course_shrinkage_starts": {
            str(k): v for k, v in sorted(_dataset.COURSE_SHRINKAGE_STARTS.items())
        },
        "class_rank": dict(sorted(_dataset._CLASS_RANK.items())),
    }
    return _sha256_json(payload)


def data_fingerprint(session: Session, *, start_date: dt.date, end_date: dt.date) -> str:
    """Hash of the rows the query will read, over the window it reads.

    The window starts `MEETING_WINDOW_MARGIN_DAYS` before `start_date`
    because `_MEETING_CTE` does: a race outside the requested range can
    still move a `meeting_form_score` inside it.
    """
    window_start = start_date - dt.timedelta(days=_dataset.MEETING_WINDOW_MARGIN_DAYS)
    row = (
        session.execute(_FINGERPRINT_SQL, {"window_start": window_start, "end_date": end_date})
        .mappings()
        .one()
    )
    payload = {k: (v.isoformat() if isinstance(v, dt.datetime) else v) for k, v in row.items()}
    return _sha256_json(payload)


def cache_key(
    *,
    start_date: dt.date,
    end_date: dt.date,
    include_before_info: bool,
    include_racer_stats: bool,
    recipe: str,
    data: str,
) -> str:
    return _sha256_json(
        {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "include_before_info": include_before_info,
            "include_racer_stats": include_racer_stats,
            "recipe": recipe,
            "data": data,
        }
    )[:32]


def cache_path(cache_dir: Path, key: str) -> Path:
    return Path(cache_dir) / f"dataset-{key}.npz"


def prune(cache_dir: Path, *, keep: int = MAX_CACHE_ENTRIES) -> list[Path]:
    """Evict all but the `keep` newest entries; return what was removed.

    Only files this module wrote are considered, so pointing `--cache-dir`
    at a populated directory by mistake cannot delete anything else. A
    file that vanishes between the listing and the unlink is another
    process pruning concurrently, which is the outcome we wanted anyway.
    """
    entries = sorted(
        Path(cache_dir).glob("dataset-*.npz"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    removed = []
    for stale in entries[keep:]:
        try:
            stale.unlink()
        except FileNotFoundError:
            continue
        removed.append(stale)
    return removed


def save_dataset(path: Path, data: Dataset, *, meta: dict) -> None:
    """Write `data` to `path` atomically.

    A partially written `.npz` that a later run reads as a hit would be
    indistinguishable from a real cache entry, so the file is built under
    a temporary name and renamed only once complete.
    """
    import numpy as np

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    race_id_kind = "uuid" if data.race_ids and isinstance(data.race_ids[0], uuid.UUID) else "str"
    stats = asdict(data.stats)
    stats["excluded_dates"] = [d.isoformat() for d in data.stats.excluded_dates]

    tmp = path.with_suffix(".npz.tmp")
    with open(tmp, "wb") as fh:
        np.savez_compressed(
            fh,
            # float64 throughout: a cached run must reproduce an uncached
            # run's log-loss exactly, not approximately.
            X=np.asarray(data.X, dtype=np.float64).reshape(len(data.y), -1),
            y=np.asarray(data.y, dtype=np.int16),
            date_ordinals=np.asarray([d.toordinal() for d in data.dates], dtype=np.int32),
            phases=np.asarray(data.phases, dtype=np.str_),
            feature_names=np.asarray(data.feature_names, dtype=np.str_),
            race_ids=np.asarray([str(r) for r in data.race_ids], dtype=np.str_),
            y_second=np.asarray(
                [_NO_SECOND if s is None else s for s in data.y_second], dtype=np.int16
            ),
            meta=np.asarray(
                json.dumps({**meta, "stats": stats, "race_id_kind": race_id_kind}), dtype=np.str_
            ),
        )
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def load_dataset(path: Path) -> tuple[Dataset, dict]:
    """Read back what `save_dataset` wrote.

    Raises `DatasetCacheError` rather than returning a partial object:
    the caller's fallback is to rebuild, which is always correct, and a
    half-populated `Dataset` is not something a model would reject.
    """
    import numpy as np

    try:
        with np.load(Path(path), allow_pickle=False) as npz:
            meta = json.loads(str(npz["meta"]))
            stats_raw = dict(meta.pop("stats"))
            excluded = [dt.date.fromisoformat(d) for d in stats_raw.pop("excluded_dates", [])]
            kind = meta.pop("race_id_kind", "str")
            raw_ids = [str(r) for r in npz["race_ids"].tolist()]
            data = Dataset(
                X=npz["X"].tolist(),
                y=[int(v) for v in npz["y"].tolist()],
                dates=[dt.date.fromordinal(int(o)) for o in npz["date_ordinals"].tolist()],
                phases=[str(p) for p in npz["phases"].tolist()],
                feature_names=[str(c) for c in npz["feature_names"].tolist()],
                stats=DatasetStats(**stats_raw, excluded_dates=excluded),
                race_ids=[uuid.UUID(r) for r in raw_ids] if kind == "uuid" else raw_ids,
                y_second=[
                    None if int(v) == _NO_SECOND else int(v) for v in npz["y_second"].tolist()
                ],
            )
    except DatasetCacheError:
        raise
    except Exception as exc:
        raise DatasetCacheError(f"unreadable cache entry {path}: {exc}") from exc
    return data, meta


def build_dataset_cached(
    session: Session,
    *,
    start_date: dt.date,
    end_date: dt.date,
    include_before_info: bool = False,
    include_racer_stats: bool = False,
    cache_dir: Path | str | None = DEFAULT_CACHE_DIR,
    refresh: bool = False,
    on_event=None,
) -> Dataset:
    """`build_dataset`, memoised on disk.

    Drop-in: same arguments, same `Dataset` back. `cache_dir=None`
    disables caching entirely, which is what a caller that must not read
    a stale entry under any circumstance should pass.

    `refresh=True` rebuilds and overwrites. `on_event` receives
    `("hit"|"miss"|"refresh"|"unreadable", path)` for logging.
    """
    if cache_dir is None:
        return build_dataset(
            session,
            start_date=start_date,
            end_date=end_date,
            include_before_info=include_before_info,
            include_racer_stats=include_racer_stats,
        )
    if end_date < start_date:
        raise ValueError(f"end_date {end_date} precedes start_date {start_date}")

    recipe = recipe_fingerprint(
        include_before_info=include_before_info, include_racer_stats=include_racer_stats
    )
    data_fp = data_fingerprint(session, start_date=start_date, end_date=end_date)
    key = cache_key(
        start_date=start_date,
        end_date=end_date,
        include_before_info=include_before_info,
        include_racer_stats=include_racer_stats,
        recipe=recipe,
        data=data_fp,
    )
    path = cache_path(Path(cache_dir), key)

    def emit(event: str, subject: Path | None = None) -> None:
        if on_event is not None:
            on_event(event, subject or path)

    if path.exists() and not refresh:
        try:
            data, _meta = load_dataset(path)
        except DatasetCacheError:
            emit("unreadable")
        else:
            emit("hit")
            return data
    else:
        emit("refresh" if refresh and path.exists() else "miss")

    data = build_dataset(
        session,
        start_date=start_date,
        end_date=end_date,
        include_before_info=include_before_info,
        include_racer_stats=include_racer_stats,
    )
    save_dataset(
        path,
        data,
        meta={
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "include_before_info": include_before_info,
            "include_racer_stats": include_racer_stats,
            "recipe_fingerprint": recipe,
            "data_fingerprint": data_fp,
            "n_races": len(data),
        },
    )
    for evicted in prune(Path(cache_dir)):
        emit("evicted", evicted)
    return data


def add_cache_arguments(parser) -> None:
    """Attach the cache flags to an evaluation script's argument parser.

    Caching is on for the command line and off for the library: every
    `evaluate(...)` keeps `cache_dir=None` as its default, so the test
    suite and any programmatic caller behave exactly as before and no
    test run writes into `data/cache/`.
    """
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=DEFAULT_CACHE_DIR,
        help=f"dataset cache directory (default: {DEFAULT_CACHE_DIR})",
    )
    parser.add_argument("--no-cache", action="store_true", help="always rebuild, and write nothing")
    parser.add_argument(
        "--refresh-cache", action="store_true", help="rebuild and overwrite the cached entry"
    )


def cache_options(args) -> dict:
    """`{cache_dir, refresh_cache}` from a parser built with
    `add_cache_arguments`, ready to splat into an `evaluate(...)` call."""
    return {
        "cache_dir": None if args.no_cache else args.cache_dir,
        "refresh_cache": args.refresh_cache,
    }


def report_to_stderr(event: str, path: Path) -> None:
    """Default `on_event`: says which way the cache went.

    On stderr so that `--json-out` and piped stdout stay machine-readable.
    """
    import sys

    print(f"dataset cache: {event} ({path.name})", file=sys.stderr)


def _sha256_json(payload) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
