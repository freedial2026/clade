"""Abstention reasons and policy (P2-T004).

Abstaining is a normal outcome, not an error
(docs/domain/claude_boatrace_prediction_system_implementation_guide.md
§15: "見送りは正常な結果であり、エラーではない"). Every abstain decision
carries one or more explicit reason codes (§15.1); any required input
that is missing (`None`) defaults to an abstain decision rather than
being silently treated as passing, and every threshold is supplied by
the caller — e.g. loaded from a config file — rather than hardcoded here
(§15.2: "閾値は設定ファイル化し、ハードコードしない").
"""

from __future__ import annotations

from dataclasses import dataclass

ABSTENTION_POLICY_VERSION = "abstention_policy_v1"

# §15.1, plus one addition. Named as module-level constants (not just
# entries in REASON_CODES below) so other modules can `import` a code
# directly instead of restating the string literal themselves.
DQ_LOW_DATA_QUALITY = "DQ_LOW_DATA_QUALITY"
DQ_MISSING_REQUIRED_DATA = "DQ_MISSING_REQUIRED_DATA"
DQ_POINT_IN_TIME_VIOLATION = "DQ_POINT_IN_TIME_VIOLATION"
MD_MODEL_DISAGREEMENT = "MD_MODEL_DISAGREEMENT"
MD_LOW_CALIBRATION_CONFIDENCE = "MD_LOW_CALIBRATION_CONFIDENCE"
MD_OUT_OF_DISTRIBUTION = "MD_OUT_OF_DISTRIBUTION"
OD_ODDS_MISSING = "OD_ODDS_MISSING"
OD_ODDS_STALE = "OD_ODDS_STALE"
OD_ODDS_SHARP_CHANGE = "OD_ODDS_SHARP_CHANGE"
RC_ENTRY_CHANGE = "RC_ENTRY_CHANGE"
RC_EXHIBITION_UNSTABLE = "RC_EXHIBITION_UNSTABLE"
RC_WEATHER_EXTREME = "RC_WEATHER_EXTREME"
RM_DAILY_LIMIT_REACHED = "RM_DAILY_LIMIT_REACHED"
RM_MONTHLY_LIMIT_REACHED = "RM_MONTHLY_LIMIT_REACHED"
# Not in the guide's §15.1 list. §15.2's fifth initial rule ("conservative
# EV below threshold -> abstain") has no listed code to match it — added
# here to fill that documented gap.
EV_CONSERVATIVE_BELOW_THRESHOLD = "EV_CONSERVATIVE_BELOW_THRESHOLD"

REASON_CODES: frozenset[str] = frozenset(
    {
        DQ_LOW_DATA_QUALITY,
        DQ_MISSING_REQUIRED_DATA,
        DQ_POINT_IN_TIME_VIOLATION,
        MD_MODEL_DISAGREEMENT,
        MD_LOW_CALIBRATION_CONFIDENCE,
        MD_OUT_OF_DISTRIBUTION,
        OD_ODDS_MISSING,
        OD_ODDS_STALE,
        OD_ODDS_SHARP_CHANGE,
        RC_ENTRY_CHANGE,
        RC_EXHIBITION_UNSTABLE,
        RC_WEATHER_EXTREME,
        RM_DAILY_LIMIT_REACHED,
        RM_MONTHLY_LIMIT_REACHED,
        EV_CONSERVATIVE_BELOW_THRESHOLD,
    }
)


class AbstentionError(ValueError):
    """Raised for invalid abstention policy input."""


@dataclass(frozen=True)
class AbstentionThresholds:
    """Every threshold this policy compares against. Supplied by the
    caller (e.g. loaded from a config file) — never hardcoded here."""

    min_data_quality_score: float
    max_model_disagreement: float
    min_conservative_ev: float


@dataclass(frozen=True)
class AbstentionDecision:
    abstain: bool
    reason_codes: tuple[str, ...]
    policy_version: str

    def to_dict(self) -> dict:
        return {
            "abstain": self.abstain,
            "reason_codes": list(self.reason_codes),
            "policy_version": self.policy_version,
        }

    def to_skip_reason_string(self) -> str | None:
        """A single comma-joined string for callers (e.g.
        `paper_simulation.BetCandidate.skip_reason`) that only have room
        for one `str | None` field. `None` when there's nothing to skip
        for. Kept here, next to `reason_codes`, rather than reimplemented
        ad hoc at each call site."""
        return ",".join(self.reason_codes) if self.reason_codes else None


def evaluate_abstention(
    *,
    thresholds: AbstentionThresholds,
    data_quality_score: float | None,
    odds_found: bool,
    model_disagreement: float | None,
    conservative_ev: float | None,
) -> AbstentionDecision:
    """Missing (`None`) inputs each contribute their own abstain reason
    rather than being silently skipped or defaulted to a passing value."""
    reasons: list[str] = []

    if data_quality_score is None:
        reasons.append(DQ_MISSING_REQUIRED_DATA)
    elif data_quality_score < thresholds.min_data_quality_score:
        reasons.append(DQ_LOW_DATA_QUALITY)

    if not odds_found:
        reasons.append(OD_ODDS_MISSING)

    if model_disagreement is None:
        reasons.append(DQ_MISSING_REQUIRED_DATA)
    elif model_disagreement > thresholds.max_model_disagreement:
        reasons.append(MD_MODEL_DISAGREEMENT)

    if conservative_ev is None:
        reasons.append(DQ_MISSING_REQUIRED_DATA)
    elif conservative_ev < thresholds.min_conservative_ev:
        reasons.append(EV_CONSERVATIVE_BELOW_THRESHOLD)

    unique_reasons = tuple(dict.fromkeys(reasons))
    return AbstentionDecision(
        abstain=bool(unique_reasons),
        reason_codes=unique_reasons,
        policy_version=ABSTENTION_POLICY_VERSION,
    )
