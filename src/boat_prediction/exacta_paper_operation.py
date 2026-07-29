"""Exacta paper operation (P3-T004).

Runs the exacta policy — P3-T003's 30-class combination probabilities,
priced with P2-T003's conservative expected value, gated by P2-T004's
abstention policy — through P2-T005's fixed-stake `PaperSimulator`
(never a real transaction), and reports P2-T006-style subgroup
risk/concentration over the resulting bets.

No path to a real transaction exists anywhere in this module: there is
no broker/exchange client, no HTTP call, and no order-submission
function. `PROMOTION_REQUIRES_SEPARATE_APPROVAL` documents, as a
standing constant, that moving from paper operation to any real,
money-affecting use requires a separate, explicit approval step outside
this codebase (docs/PROJECT_PROFILE.md: "Production promotion requires
independent holdout and paper operation";
.claude/rules/01-approval-policy.md: model promotion needs approval) —
there is deliberately no `promote()`/`go_live()` function here or
anywhere in this codebase.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from .abstention import AbstentionThresholds, evaluate_abstention
from .expected_value import compute_conservative_ev
from .paper_simulation import (
    RESULT_LOSS,
    RESULT_SKIPPED,
    RESULT_VOID,
    RESULT_WIN,
    BetCandidate,
    BetRecord,
    PaperSimulator,
)
from .stability import StabilityReport, assess_subgroup_stability

PROMOTION_REQUIRES_SEPARATE_APPROVAL = True


class ExactaPaperOperationError(ValueError):
    """Raised for invalid paper-operation input."""


def select_best_combination(
    exacta_probs: dict[int, float],
    odds_by_combination: dict[int, float],
    *,
    calibration_error: float,
    n_samples: int,
    model_std: float = 0.0,
) -> tuple[int, object]:
    """Conservative-EV-ranked pick among combinations that have odds
    available. Returns (combination_code, ExpectedValueReport)."""
    priced = {code: prob for code, prob in exacta_probs.items() if code in odds_by_combination}
    if not priced:
        raise ExactaPaperOperationError("no exacta combination has odds available")

    best_code = None
    best_report = None
    for code, prob in priced.items():
        report = compute_conservative_ev(
            prob,
            odds_by_combination[code],
            calibration_error,
            n_samples,
            model_std,
        )
        if best_report is None or report.conservative_ev > best_report.conservative_ev:
            best_code, best_report = code, report

    return best_code, best_report


def decide_and_record(
    simulator: PaperSimulator,
    *,
    race_id: str,
    prediction_id: str,
    decision_at: datetime,
    exacta_probs: dict[int, float],
    odds_by_combination: dict[int, float],
    thresholds: AbstentionThresholds,
    data_quality_score: float | None,
    model_disagreement: float | None,
    calibration_error: float,
    n_samples: int,
    actual_combination: int | None,
) -> BetRecord:
    """Decide whether to bet the best-EV combination and record the
    outcome in `simulator` (paper only). `actual_combination` is the
    real outcome (for backtesting/paper evaluation against a known
    result) — `None` means the race was void/cancelled."""
    if not odds_by_combination:
        raise ExactaPaperOperationError("odds_by_combination must not be empty")

    best_code, ev_report = select_best_combination(
        exacta_probs,
        odds_by_combination,
        calibration_error=calibration_error,
        n_samples=n_samples,
    )

    decision = evaluate_abstention(
        thresholds=thresholds,
        data_quality_score=data_quality_score,
        odds_found=True,
        model_disagreement=model_disagreement,
        conservative_ev=ev_report.conservative_ev,
    )

    candidate = BetCandidate(
        race_id=race_id,
        prediction_id=prediction_id,
        decision_at=decision_at,
        combination=str(best_code),
        odds_at_decision=odds_by_combination[best_code],
        model_probability=ev_report.raw_probability,
        conservative_probability=ev_report.conservative_probability,
        expected_value=ev_report.conservative_ev,
        recommended=not decision.abstain,
        skip_reason=decision.to_skip_reason_string(),
    )

    if decision.abstain:
        return simulator.record_skip(candidate)

    if actual_combination is None:
        return simulator.record_outcome(candidate, result=RESULT_VOID)
    if actual_combination == best_code:
        payout_yen = round(simulator.stake_yen * odds_by_combination[best_code])
        return simulator.record_outcome(candidate, result=RESULT_WIN, payout_yen=payout_yen)
    return simulator.record_outcome(candidate, result=RESULT_LOSS, payout_yen=0)


def report_risk(
    records: list[BetRecord],
    group_key: Callable[[BetRecord], object],
    *,
    confidence_level: float = 0.95,
    concentration_threshold: float = 0.5,
) -> StabilityReport:
    """Subgroup risk/concentration over placed bets (net PnL per bet).
    Skipped candidates carry no stake and are excluded, matching
    `paper_simulation.summarize()`'s convention."""
    placed = [r for r in records if r.result != RESULT_SKIPPED]
    if not placed:
        raise ExactaPaperOperationError("no placed bets to assess risk for")

    return assess_subgroup_stability(
        placed,
        group_key,
        lambda r: r.payout_yen - r.stake_yen,
        confidence_level=confidence_level,
        concentration_threshold=concentration_threshold,
    )
