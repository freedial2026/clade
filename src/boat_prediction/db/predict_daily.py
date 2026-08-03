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

Two roles, two moments
----------------------

`--role default` (the morning run) predicts every race on the card, from
card features alone, once. `--role preview` runs every two minutes
through the racing day and predicts only the races whose 直前情報 has
arrived, using the model trained on it (`train_model --with-before-info`).

Which features the row is built from is **not** a flag here -- it is read
from the registry entry's `include_before_info` parameter. A CLI flag
would be a second place to state the same fact, and the failure it
allows is the quiet kind: a preview model fed card-only rows is a shape
error sklearn catches, but a card model fed preview-width rows in some
future feature set need not be, and either way the model would be
scoring numbers it was not fit on.

The preview run also predicts each race **once**, unlike the morning
run's "a later run adds a later-stamped row". That difference is not an
inconsistency: odds move, so a second odds-time reading is a second
fact, but 直前情報 does not change once published, so a second prediction
from it would be the same arithmetic on the same inputs -- fifteen
identical rows per race over a thirty-minute window, none of them new
evidence. `already_predicted` counts them instead.
"""

from __future__ import annotations

import argparse
import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..model_registry import DEFAULT_ROLE, ModelRegistry
from .dataset import LANES, build_prediction_rows
from .models import Race, RaceEntry, RacePrediction
from .session import create_db_engine, create_session_factory

JST = ZoneInfo("Asia/Tokyo")

DEFAULT_REGISTRY_PATH = Path("data/models/registry.json")

PREVIEW_ROLE = "preview"

DEFAULT_PREVIEW_LEAD_MINUTES = 10
"""How far ahead of a deadline the preview run will predict.

`capture_beforeinfo` collects 5-30 minutes out, so a race becomes
predictable somewhere in that window and this decides where inside it the
prediction is taken. 10 matches the earlier of `capture_odds`' two leads,
so the probability and a price exist at roughly the same moment and the
pair can be compared without interpolating between odds readings -- which
is the whole purpose of the record (selection on price, item 2 of the
plan in tasks/CURRENT.md).

It is a *maximum*, not a target: a race is predicted on the first run
that finds it inside the window with its 直前情報 in, which on a
two-minute schedule is within two minutes of the bound."""


class PredictionError(RuntimeError):
    """Raised when a day's predictions cannot be produced honestly."""


@dataclass
class PredictionRunStats:
    race_date: dt.date
    model_version: str
    races_in_dataset: int = 0
    races_predicted: int = 0
    skipped_deadline_passed: int = 0
    skipped_not_yet_due: int = 0
    skipped_already_predicted: int = 0
    rows_written: int = 0
    dataset_stats: str = ""

    def __str__(self) -> str:
        return (
            f"{self.race_date} model={self.model_version} "
            f"races_in_dataset={self.races_in_dataset} "
            f"races_predicted={self.races_predicted} "
            f"skipped_deadline_passed={self.skipped_deadline_passed} "
            f"skipped_not_yet_due={self.skipped_not_yet_due} "
            f"skipped_already_predicted={self.skipped_already_predicted} "
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


def _already_predicted(session: Session, race_ids: list, model_version: str) -> set:
    """Races this model version has already predicted today."""
    if not race_ids:
        return set()
    return set(
        session.scalars(
            select(RacePrediction.race_id).where(
                RacePrediction.race_id.in_(race_ids),
                RacePrediction.model_version == model_version,
            )
        )
    )


def predict_day(
    session: Session,
    *,
    race_date: dt.date,
    model,
    model_version: str,
    now: dt.datetime | None = None,
    include_before_info: bool = False,
    max_lead_minutes: int | None = None,
    once_per_race: bool = False,
) -> PredictionRunStats:
    """Predict and store every race on `race_date` whose deadline is still
    ahead. `model` must already be fitted -- this never calls `fit`.

    `include_before_info` must match what `model` was fit on; callers get
    it from the registry rather than choosing it.

    `max_lead_minutes` restricts the run to races whose deadline is that
    close, and `once_per_race` skips races this `model_version` has
    already predicted today. Both are off by default, which is the
    morning card run: every race, once, as early as the card allows.
    """
    now = now or dt.datetime.now(dt.UTC)
    stats = PredictionRunStats(race_date=race_date, model_version=model_version)

    rows = build_prediction_rows(
        session, race_date=race_date, include_before_info=include_before_info
    )
    stats.races_in_dataset = len(rows)
    stats.dataset_stats = str(rows.stats)
    if not len(rows):
        return stats

    context = _race_context(session, rows.race_ids)
    seen = (
        _already_predicted(session, rows.race_ids, model_version) if once_per_race else set()
    )
    horizon = (
        now + dt.timedelta(minutes=max_lead_minutes) if max_lead_minutes is not None else None
    )

    pending_ids: list = []
    pending_X: list[list[float]] = []
    for race_id, features in zip(rows.race_ids, rows.X):
        deadline, _feat_at = context.get(race_id, (None, None))
        if deadline is None:
            continue
        if deadline <= now:
            stats.skipped_deadline_passed += 1
            continue
        if horizon is not None and deadline > horizon:
            stats.skipped_not_yet_due += 1
            continue
        if race_id in seen:
            stats.skipped_already_predicted += 1
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


def load_active_model(registry_path: Path, role: str = DEFAULT_ROLE):
    """The role's frozen model, with its artifact checksum verified -- a
    silently swapped file would otherwise be indistinguishable from the
    model that produced yesterday's rows.

    Returns the feature-set choice alongside the model, so the caller
    cannot build rows the model was not fit on.
    """
    import pickle

    registry = ModelRegistry(registry_path)
    version = registry.get_active(role)
    if not registry.verify_artifact(version.version_id):
        raise PredictionError(
            f"artifact checksum mismatch for model {version.version_id}; refusing to predict"
        )
    with open(version.artifact_path, "rb") as handle:
        model = pickle.load(handle)
    return model, version.version_id, bool(version.parameters.get("include_before_info", False))


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
    parser.add_argument(
        "--role",
        default=DEFAULT_ROLE,
        help=(
            f"registry role to predict with; {PREVIEW_ROLE!r} runs the 直前情報 model "
            "over the races whose 直前情報 has arrived"
        ),
    )
    parser.add_argument(
        "--max-lead-minutes",
        type=int,
        default=None,
        help=(
            "only predict races whose deadline is at most this close; "
            f"defaults to {DEFAULT_PREVIEW_LEAD_MINUTES} for the {PREVIEW_ROLE!r} role "
            "and to no bound otherwise"
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    race_date = args.date or dt.datetime.now(JST).date()
    model, version_id, include_before_info = load_active_model(args.registry, args.role)

    is_preview = args.role == PREVIEW_ROLE
    max_lead_minutes = args.max_lead_minutes
    if max_lead_minutes is None and is_preview:
        max_lead_minutes = DEFAULT_PREVIEW_LEAD_MINUTES

    engine = create_db_engine(args.database_url)
    try:
        with create_session_factory(engine)() as session:
            stats = predict_day(
                session,
                race_date=race_date,
                model=model,
                model_version=version_id,
                include_before_info=include_before_info,
                max_lead_minutes=max_lead_minutes,
                once_per_race=is_preview,
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
