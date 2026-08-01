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
"""

from __future__ import annotations

import argparse
import datetime as dt
import pickle
import subprocess
from pathlib import Path

from ..model_registry import ModelRegistry
from .dataset import build_dataset, feature_columns
from .evaluate_p1 import sklearn_logistic_factory
from .session import create_db_engine, create_session_factory

FEATURE_SET_VERSION = "card_meeting_form_phase_v1"
DEFAULT_REGISTRY_PATH = Path("data/models/registry.json")
DEFAULT_ARTIFACT_DIR = Path("data/models")


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
) -> str:
    data = build_dataset(session, start_date=start_date, end_date=end_date)
    if not len(data):
        raise ValueError(f"no usable races between {start_date} and {end_date}")

    model = sklearn_logistic_factory()()
    model.fit(data.X, data.y)

    version_id = version_id or f"logistic_cards_{end_date.isoformat().replace('-', '')}"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_dir / f"{version_id}.pkl"
    with open(artifact_path, "wb") as handle:
        pickle.dump(model, handle)

    registry = ModelRegistry(registry_path)
    registry.register(
        version_id,
        dataset_version=f"{start_date.isoformat()}..{end_date.isoformat()}",
        feature_set_version=FEATURE_SET_VERSION,
        code_version=_code_version(),
        parameters={"model": "multinomial_logistic", "scaled": True, "max_iter": 1000},
        # No calibration: a held-out binned recalibration was measured and
        # made both log-loss and ECE worse (tasks/HANDOFF.md, 2026-08-01),
        # so the raw model is what is registered.
        calibration_version="none",
        evaluation_metrics={
            "n_train_races": len(data),
            "walk_forward_mean_log_loss": 1.21148,
            "walk_forward_note": (
                "from the 31-fold 2023-01-01..2026-07-29 run; not recomputed here"
            ),
            "feature_count": len(feature_columns()),
        },
        artifact_path=artifact_path,
    )
    if activate:
        registry.activate(version_id)
    return version_id


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--start-date", type=dt.date.fromisoformat, required=True)
    parser.add_argument("--end-date", type=dt.date.fromisoformat, required=True)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY_PATH)
    parser.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    parser.add_argument("--version-id", default=None)
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
            )
    finally:
        engine.dispose()
    print(f"registered and activated: {version_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
