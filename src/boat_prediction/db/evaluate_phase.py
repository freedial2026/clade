"""Break P1's walk-forward result down by 節 phase.

`evaluate_p1.py` answers whether card features beat the lane prior
overall. It cannot say *where* the edge lives -- whether it holds up in
準優勝戦/優勝戦 (5.7% of races, where lanes are seeded by 点率 standing
rather than arbitrary) as well as it does in 予選. This module answers
that, using `stability.assess_subgroup_stability` with `race_phase` as
the grouping key: the same generic subgroup machinery P2 uses for
month/venue/grade checks, applied here to phase.

`concentration_flag` there means the opposite of what a phase breakdown
needs: it fires when *one* group's share exceeds a threshold (the
overall number risks being an artifact of that one dominant group), not
when a group is small. 決勝 (1.5% of races) will never trip it. The
signal to read for a sparse phase is its confidence interval width, not
that flag -- `render()` prints `ci_low..ci_high` for exactly that
reason. A headline number computed only on 予選 (47.7% of races) would
silently say nothing about whether the seeded rounds -- the ones a
bettor watches most closely -- behave the same way.

Not a decision about where to bet: this stays P1 (prediction quality),
not P2 (market comparison, still waiting on the pre-deadline odds series
-- see tasks/CURRENT.md). It only says whether the model's edge is even
in the rounds that matter.
"""

from __future__ import annotations

import argparse
import datetime as dt
from dataclasses import dataclass

from ..stability import StabilityReport, assess_subgroup_stability
from ..walk_forward import generate_monthly_folds
from .dataset import LANES, build_dataset
from .evaluate_p1 import (
    _sklearn_available,
    lane_prior_factory,
    per_race_log_loss,
    sklearn_logistic_factory,
    uniform_factory,
)
from .session import create_db_engine, create_session_factory

_PHASE_ORDER = (
    "trial",
    "qualifier",
    "semifinal",
    "final",
    "selection",
    "general",
    "unknown",
)


@dataclass(frozen=True)
class _Record:
    phase: str
    loss: float


@dataclass
class PhaseEvaluationResult:
    n_races: int
    n_folds: int
    reports: dict[str, StabilityReport]
    skipped_models: list[str]


def evaluate(
    session, *, start_date: dt.date, end_date: dt.date, min_train_months: int = 6
) -> PhaseEvaluationResult:
    """Fit each model per fold directly (not through `model_comparison`,
    which discards per-race predictions) so every test race's own loss
    can be tagged with its phase and pooled across folds before the
    subgroup breakdown."""
    data = build_dataset(session, start_date=start_date, end_date=end_date)
    if not len(data):
        raise ValueError("no usable races in the requested range")

    folds = generate_monthly_folds(data.dates, min_train_months=min_train_months)

    model_factories = {"uniform": uniform_factory, "lane_prior": lane_prior_factory}
    skipped = []
    if _sklearn_available():
        model_factories["logistic_cards"] = sklearn_logistic_factory()
    else:
        skipped.append("logistic_cards (scikit-learn not installed)")

    records_by_model: dict[str, list[_Record]] = {name: [] for name in model_factories}

    for fold in folds:
        train_idx = fold.train_indices(data.dates)
        test_idx = fold.test_indices(data.dates)
        if not train_idx or not test_idx:
            continue
        X_train = [data.X[i] for i in train_idx]
        y_train = [data.y[i] for i in train_idx]
        X_test = [data.X[i] for i in test_idx]
        y_test = [data.y[i] for i in test_idx]
        test_phases = [data.phases[i] for i in test_idx]

        for name, factory in model_factories.items():
            model = factory()
            model.fit(X_train, y_train)
            probs = model.predict_proba(X_test)
            losses = per_race_log_loss(y_test, probs, list(LANES))
            records_by_model[name].extend(
                _Record(phase=phase, loss=loss) for phase, loss in zip(test_phases, losses)
            )

    reports = {
        name: assess_subgroup_stability(
            records, group_key=lambda r: r.phase, value_key=lambda r: r.loss
        )
        for name, records in records_by_model.items()
        if records
    }

    return PhaseEvaluationResult(
        n_races=len(data), n_folds=len(folds), reports=reports, skipped_models=skipped
    )


def render(result: PhaseEvaluationResult) -> str:
    lines = [
        f"phase-broken-down P1 evaluation  races={result.n_races} folds={result.n_folds}",
        "",
    ]
    model_names = list(result.reports)
    phases_seen = {s.group for report in result.reports.values() for s in report.subgroups}
    ordered_phases = [p for p in _PHASE_ORDER if p in phases_seen]
    ordered_phases += sorted(phases_seen - set(ordered_phases))

    by_model_phase = {
        name: {s.group: s for s in report.subgroups} for name, report in result.reports.items()
    }
    for name in model_names:
        lines.append(f"{name}:")
        lines.append(f"  {'phase':<12}{'n':>8}{'share':>8}{'mean':>10}{'95% CI':>18}")
        for phase in ordered_phases:
            stats = by_model_phase[name].get(phase)
            if stats is None:
                continue
            lines.append(
                f"  {phase:<12}{stats.n:>8}{100 * stats.share_of_total:>7.2f}%"
                f"{stats.mean:>10.4f}  [{stats.ci_low:.4f}, {stats.ci_high:.4f}]"
            )
        lines.append("")
    lines.append(
        "Read the CI width, not concentration_flag, for a sparse phase's "
        "trustworthiness -- see the module docstring for why."
    )
    for skipped in result.skipped_models:
        lines.append(f"  [skipped] {skipped}")
    return "\n".join(lines)


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--start-date", type=dt.date.fromisoformat, required=True)
    parser.add_argument("--end-date", type=dt.date.fromisoformat, required=True)
    parser.add_argument("--min-train-months", type=int, default=6)
    args = parser.parse_args(argv)

    engine = create_db_engine(args.database_url)
    try:
        with create_session_factory(engine)() as session:
            result = evaluate(
                session,
                start_date=args.start_date,
                end_date=args.end_date,
                min_train_months=args.min_train_months,
            )
    finally:
        engine.dispose()

    print(render(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
