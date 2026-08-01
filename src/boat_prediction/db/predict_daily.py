"""Record first-place probabilities for a day's races, before they run.

The forward half of the P2 test. `capture_odds` has been recording
pre-deadline quotes since 2026-08-01; this records what the model
believed at the same time, so that a price-selection rule can later be
evaluated on evidence that was not available to whoever designs the rule.

Two properties make the record worth keeping, and both are enforced here
rather than asserted afterwards:

**The model is frozen.** It is loaded from `model_registry`'s active
version and never refit, so a prediction made today cannot benefit from
data that arrives tomorrow. Refitting nightly would quietly turn the
whole record back into a backtest.

**Every row is dated twice.** `predicted_at` is when this ran;
`features_available_at` is the latest `available_at` among the card rows
consumed. A race whose deadline has already passed is skipped, so the
invariant `features_available_at <= predicted_at <= scheduled_deadline_at`
holds for every row written and can be checked with a query.

Idempotent per `(race, lane, model_version, predicted_at)`; re-running
within the same minute rewrites nothing, and a later run simply adds a
second, later-stamped prediction rather than overwriting the first --
which is correct, since both really were made.
"""

from __future__ import annotations

import argparse
import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..model_registry import ModelRegistry
from .dataset import LANES, build_prediction_rows
from .models import Race, RaceEntry, RacePrediction
from .session import create_db_engine, create_session_factory

JST = ZoneInfo("Asia/Tokyo")

DEFAULT_REGISTRY_PATH = Path("data/models/registry.json")


class PredictionError(RuntimeError):
    """Raised when a day's predictions cannot be produced honestly."""


@dataclass
class PredictionRunStats:
    race_date: dt.date
    model_version: str
    races_in_dataset: int = 0
    races_predicted: int = 0
    skipped_deadline_passed: int = 0
    rows_written: int = 0
    dataset_stats: str = ""

    def __str__(self) -> str:
        return (
            f"{self.race_date} model={self.model_version} "
            f"races_in_dataset={self.races_in_dataset} "
            f"races_predicted={self.races_predicted} "
            f"skipped_deadline_passed={self.skipped_deadline_passed} "
            f"rows_written={self.rows_written} [{self.dataset_stats}]"
        )


def _as_utc(value) -> dt.datetime | None:
    """Normalize a timestamp read through raw SQL.

    SQLite hands these back as text where PostgreSQL gives an aware
    datetime -- the same split `dataset.py` already handles for dates. A
    naive value is treated as UTC, which is what every timestamp column in
    this schema stores (see models.py's time-semantics note).
    """
    if value is None:
        return None
    if isinstance(value, str):
        value = dt.datetime.fromisoformat(value)
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    return value


def _race_context(session: Session, race_ids: list) -> dict:
    """`scheduled_deadline_at` and the latest feature `available_at` per
    race, in one query rather than one per race.

    Expressed through the ORM rather than raw SQL specifically so the
    `Uuid` and `DateTime` column types are handled by SQLAlchemy on both
    backends. An earlier raw-SQL version had to hand-convert ids and
    timestamps in three places and still could not bind a UUID on SQLite.
    """
    if not race_ids:
        return {}
    rows = session.execute(
        select(
            Race.id,
            Race.scheduled_deadline_at,
            func.max(RaceEntry.available_at),
        )
        .join(RaceEntry, RaceEntry.race_id == Race.id)
        .where(Race.id.in_(race_ids))
        .group_by(Race.id, Race.scheduled_deadline_at)
    )
    return {row[0]: (_as_utc(row[1]), _as_utc(row[2])) for row in rows}


def predict_day(
    session: Session,
    *,
    race_date: dt.date,
    model,
    model_version: str,
    now: dt.datetime | None = None,
) -> PredictionRunStats:
    """Predict and store every race on `race_date` whose deadline is still
    ahead. `model` must already be fitted -- this never calls `fit`."""
    now = now or dt.datetime.now(dt.UTC)
    stats = PredictionRunStats(race_date=race_date, model_version=model_version)

    rows = build_prediction_rows(session, race_date=race_date)
    stats.races_in_dataset = len(rows)
    stats.dataset_stats = str(rows.stats)
    if not len(rows):
        return stats

    context = _race_context(session, rows.race_ids)

    pending_ids: list = []
    pending_X: list[list[float]] = []
    for race_id, features in zip(rows.race_ids, rows.X):
        deadline, _feat_at = context.get(race_id, (None, None))
        if deadline is None:
            continue
        if deadline <= now:
            stats.skipped_deadline_passed += 1
            continue
        pending_ids.append(race_id)
        pending_X.append(features)

    if not pending_ids:
        return stats

    probabilities = model.predict_proba(pending_X)

    for race_id, row in zip(pending_ids, probabilities):
        total = sum(row)
        if total <= 0:
            raise PredictionError(f"model returned a degenerate distribution for {race_id}")
        _deadline, feat_at = context[race_id]
        for index, lane in enumerate(LANES):
            session.add(
                RacePrediction(
                    race_id=race_id,
                    lane_number=lane,
                    model_version=model_version,
                    win_probability=float(row[index]) / total,
                    predicted_at=now,
                    features_available_at=feat_at,
                )
            )
            stats.rows_written += 1
        stats.races_predicted += 1

    session.flush()
    return stats


def load_active_model(registry_path: Path):
    """The frozen model from the registry, with its artifact checksum
    verified -- a silently swapped file would otherwise be indistinguishable
    from the model that produced yesterday's rows."""
    import pickle

    registry = ModelRegistry(registry_path)
    version = registry.get_active()
    if not registry.verify_artifact(version.version_id):
        raise PredictionError(
            f"artifact checksum mismatch for model {version.version_id}; refusing to predict"
        )
    with open(version.artifact_path, "rb") as handle:
        return pickle.load(handle), version.version_id


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=None)
    parser.add_argument(
        "--date",
        type=dt.date.fromisoformat,
        default=None,
        help="race date (JST); defaults to today",
    )
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY_PATH)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    race_date = args.date or dt.datetime.now(JST).date()
    model, version_id = load_active_model(args.registry)

    engine = create_db_engine(args.database_url)
    try:
        with create_session_factory(engine)() as session:
            stats = predict_day(
                session, race_date=race_date, model=model, model_version=version_id
            )
            if args.dry_run:
                session.rollback()
                print(f"dry-run (rolled back): {stats}")
            else:
                session.commit()
                print(str(stats))
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
