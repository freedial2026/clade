"""Fit one model, freeze it, and register it as the active version.

`predict_daily` deliberately cannot fit anything. This is where fitting
happens, as a separate and infrequent act, so that "the model that made
this prediction" is a specific artifact with a checksum rather than
whatever the code would produce if run again.

The split of responsibility matters more than it looks. A daily job that
fit and predicted in one step would produce a record indistinguishable
from a backtest: every prediction would have been made by a model that
had seen everything up to that morning, which is exactly the setup a
walk-forward evaluation already covers and exactly what a *forward* test
must not be. Freezing the model is what makes the accumulating record
independent evidence.

The registry entry records the training window as `dataset_version`, so a
later analysis can tell which predictions came from which fit without
consulting anything outside the database and the registry file.

Two models are fit here, into two registry *roles*:

* `default` -- the card model. Every race, predicted once in the morning.
* `preview` -- the same features plus the 直前情報 block. Only the races
  whose 直前情報 has been published, predicted minutes before the
  deadline.

They are deliberately not one model with a missing-data path. The block
is worth +1.43% of log-loss when present (tasks/HANDOFF.md, 2026-08-01)
and `before_info_entries` begins on 2023-05-01, so a single model would
have to either discard two and a half years of training data or carry an
"is it there" indicator that makes the morning prediction pay for a
feature it never has. Two frozen models, each predicting what it can,
keeps both records clean and makes the block's forward value directly
measurable: the same race gets a card-only probability and a 直前情報 one,
and the pair can be compared against the same settled result.
"""

from __future__ import annotations

import argparse
import datetime as dt
import pickle
import subprocess
from pathlib import Path

from ..model_registry import DEFAULT_ROLE, ModelRegistry
from .dataset import build_dataset, feature_columns
from .evaluate_p1 import sklearn_logistic_factory
from .session import create_db_engine, create_session_factory

FEATURE_SET_VERSION = "card_meeting_form_phase_v1"
PREVIEW_FEATURE_SET_VERSION = "card_meeting_form_phase_beforeinfo_v1"
PREVIEW_ROLE = "preview"
DEFAULT_REGISTRY_PATH = Path("data/models/registry.json")
DEFAULT_ARTIFACT_DIR = Path("data/models")

EARLIEST_BEFORE_INFO_DATE = dt.date(2023, 5, 1)
"""First race date with 直前情報 in the database (`before_info_entries`,
backfilled from the Open API mirror on 2026-08-01). Training the preview
model over a window that starts earlier is not an error but silently
throws the earlier races away, so it is refused rather than tolerated."""


def _code_version() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except Exception:  # noqa: BLE001 -- provenance is best-effort, not a gate
        return "unknown"


def train_and_register(
    session,
    *,
    start_date: dt.date,
    end_date: dt.date,
    registry_path: Path,
    artifact_dir: Path,
    version_id: str | None = None,
    activate: bool = True,
    include_before_info: bool = False,
) -> str:
    if include_before_info and start_date < EARLIEST_BEFORE_INFO_DATE:
        raise ValueError(
            f"start_date {start_date} precedes the first 直前情報 "
            f"({EARLIEST_BEFORE_INFO_DATE}); those races would be dropped silently"
        )

    data = build_dataset(
        session,
        start_date=start_date,
        end_date=end_date,
        include_before_info=include_before_info,
    )
    if not len(data):
        raise ValueError(f"no usable races between {start_date} and {end_date}")

    model = sklearn_logistic_factory()()
    model.fit(data.X, data.y)

    prefix = "logistic_cards_preview" if include_before_info else "logistic_cards"
    version_id = version_id or f"{prefix}_{end_date.isoformat().replace('-', '')}"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_dir / f"{version_id}.pkl"
    with open(artifact_path, "wb") as handle:
        pickle.dump(model, handle)

    metrics = {
        "n_train_races": len(data),
        "feature_count": len(feature_columns(include_before_info=include_before_info)),
        "dataset_stats": str(data.stats),
    }
    if include_before_info:
        metrics["walk_forward_mean_log_loss"] = 1.19427
        metrics["walk_forward_note"] = (
            "from the 26-fold 2023-05-01.. run of the 直前情報 block "
            "(+1.433% over the card baseline, 26/26 folds); not recomputed here"
        )
    else:
        metrics["walk_forward_mean_log_loss"] = 1.21148
        metrics["walk_forward_note"] = (
            "from the 31-fold 2023-01-01..2026-07-29 run; not recomputed here"
        )

    registry = ModelRegistry(registry_path)
    registry.register(
        version_id,
        dataset_version=f"{start_date.isoformat()}..{end_date.isoformat()}",
        feature_set_version=(
            PREVIEW_FEATURE_SET_VERSION if include_before_info else FEATURE_SET_VERSION
        ),
        code_version=_code_version(),
        parameters={
            "model": "multinomial_logistic",
            "scaled": True,
            "max_iter": 1000,
            # Read back by `predict_daily` to decide whether to build the
            # 直前情報 block. Taking it from the artifact's own record
            # rather than a second CLI flag is what stops a model being
            # applied to a feature row of the wrong width -- which
            # sklearn would catch, and to a row of the right width but
            # the wrong *meaning*, which nothing would.
            "include_before_info": include_before_info,
        },
        # No calibration: a held-out binned recalibration was measured and
        # made both log-loss and ECE worse (tasks/HANDOFF.md, 2026-08-01),
        # so the raw model is what is registered.
        calibration_version="none",
        evaluation_metrics=metrics,
        artifact_path=artifact_path,
    )
    if activate:
        registry.activate(version_id, role=PREVIEW_ROLE if include_before_info else DEFAULT_ROLE)
    return version_id


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--start-date", type=dt.date.fromisoformat, required=True)
    parser.add_argument("--end-date", type=dt.date.fromisoformat, required=True)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY_PATH)
    parser.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    parser.add_argument("--version-id", default=None)
    parser.add_argument(
        "--with-before-info",
        action="store_true",
        help="add the 直前情報 block and register into the 'preview' role",
    )
    args = parser.parse_args(argv)

    engine = create_db_engine(args.database_url)
    try:
        with create_session_factory(engine)() as session:
            version_id = train_and_register(
                session,
                start_date=args.start_date,
                end_date=args.end_date,
                registry_path=args.registry,
                artifact_dir=args.artifact_dir,
                version_id=args.version_id,
                include_before_info=args.with_before_info,
            )
    finally:
        engine.dispose()
    role = PREVIEW_ROLE if args.with_before_info else DEFAULT_ROLE
    print(f"registered and activated: {version_id} (role={role})")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
