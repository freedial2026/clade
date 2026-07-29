"""Fixed-stake paper simulation (P2-T005).

Simulates betting outcomes without any real transaction
(docs/domain/claude_boatrace_prediction_system_implementation_guide.md
§16.1): a fixed stake per bet, at most one bet per race, a daily stake
cap, no martingale (stake never increases after a loss — it is a single
constant for the simulator's lifetime), and no Kelly-criterion sizing.
Every candidate is recorded — bought or skipped — per §16.2's field
list. Void/refunded races are tracked with their own `result` value
(§16.3: "返還レースは通常の的中・不的中と分離し、投資額を適切に戻す" —
separate a void race from normal win/loss and return the stake) so a
refund is never counted as either a win or a loss.

There is no betting integration anywhere in this module, or anywhere
else in this codebase — this only computes hypothetical paper results.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime

DEFAULT_STAKE_YEN = 100
DEFAULT_DAILY_CAP_YEN = 1_200

RESULT_WIN = "win"
RESULT_LOSS = "loss"
RESULT_VOID = "void"
RESULT_SKIPPED = "skipped"
_OUTCOME_RESULTS = (RESULT_WIN, RESULT_LOSS, RESULT_VOID)


class PaperSimulationError(ValueError):
    """Raised for invalid paper-simulation input or a rule violation
    (max one bet per race, daily cap)."""


@dataclass(frozen=True)
class BetCandidate:
    race_id: str
    prediction_id: str
    decision_at: datetime
    combination: str
    odds_at_decision: float
    model_probability: float
    conservative_probability: float
    expected_value: float
    recommended: bool
    skip_reason: str | None = None


@dataclass(frozen=True)
class BetRecord:
    race_id: str
    prediction_id: str
    decision_at: datetime
    combination: str
    odds_at_decision: float
    model_probability: float
    conservative_probability: float
    expected_value: float
    recommended: bool
    skip_reason: str | None
    stake_yen: int
    result: str
    payout_yen: int

    def to_dict(self) -> dict:
        data = asdict(self)
        data["decision_at"] = self.decision_at.isoformat()
        return data


class PaperSimulator:
    """Fixed-stake, no-martingale, no-Kelly paper simulator. `stake_yen`
    is a single constant for the simulator's lifetime — nothing here
    ever changes it based on past results."""

    def __init__(
        self,
        *,
        stake_yen: int = DEFAULT_STAKE_YEN,
        daily_cap_yen: int = DEFAULT_DAILY_CAP_YEN,
    ) -> None:
        if stake_yen <= 0:
            raise PaperSimulationError(f"stake_yen must be > 0: {stake_yen!r}")
        if daily_cap_yen < stake_yen:
            raise PaperSimulationError("daily_cap_yen must be >= stake_yen")
        self.stake_yen = stake_yen
        self.daily_cap_yen = daily_cap_yen
        self._records: list[BetRecord] = []
        self._bet_race_ids: set[str] = set()
        self._current_day: date | None = None
        self._spent_today = 0

    @property
    def records(self) -> list[BetRecord]:
        return list(self._records)

    def _reset_if_new_day(self, decision_at: datetime) -> None:
        day = decision_at.date()
        if self._current_day != day:
            self._current_day = day
            self._spent_today = 0

    def record_skip(self, candidate: BetCandidate) -> BetRecord:
        if candidate.recommended:
            raise PaperSimulationError(
                f"candidate for race {candidate.race_id!r} is recommended; use record_outcome()"
            )
        if candidate.skip_reason is None:
            raise PaperSimulationError("skip_reason is required for a skipped candidate")

        record = BetRecord(
            **asdict(candidate), stake_yen=0, result=RESULT_SKIPPED, payout_yen=0
        )
        self._records.append(record)
        return record

    def record_outcome(
        self, candidate: BetCandidate, *, result: str, payout_yen: int = 0
    ) -> BetRecord:
        """Record a bet actually placed in the simulation (never a real
        transaction). Enforces: at most one bet per race, the fixed
        configured stake, and the daily cap. A void result refunds the
        stake in full rather than counting as a loss."""
        if not candidate.recommended:
            raise PaperSimulationError(
                f"candidate for race {candidate.race_id!r} is not recommended; use record_skip()"
            )
        if result not in _OUTCOME_RESULTS:
            raise PaperSimulationError(f"invalid result: {result!r}")
        if candidate.race_id in self._bet_race_ids:
            raise PaperSimulationError(
                f"race {candidate.race_id!r} already has a bet (max 1 bet per race)"
            )

        self._reset_if_new_day(candidate.decision_at)
        if self._spent_today + self.stake_yen > self.daily_cap_yen:
            raise PaperSimulationError(f"daily cap of {self.daily_cap_yen} yen would be exceeded")

        effective_payout = self.stake_yen if result == RESULT_VOID else payout_yen
        record = BetRecord(
            **asdict(candidate),
            stake_yen=self.stake_yen,
            result=result,
            payout_yen=effective_payout,
        )
        self._records.append(record)
        self._bet_race_ids.add(candidate.race_id)
        self._spent_today += self.stake_yen
        return record


@dataclass(frozen=True)
class SimulationSummary:
    n_bets: int
    n_skipped: int
    n_void: int
    total_stake_yen: int
    total_payout_yen: int
    net_return_yen: int
    max_drawdown_yen: int
    max_win_streak: int
    max_loss_streak: int

    def to_dict(self) -> dict:
        return asdict(self)


def _max_streak(records: list[BetRecord], result: str) -> int:
    best = current = 0
    for record in records:
        if record.result == result:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def summarize(records: list[BetRecord]) -> SimulationSummary:
    """Returns, drawdown, and streaks over `records`, in the given
    (chronological) order. Skipped candidates carry no stake/payout and
    are excluded from every calculation except their own count; void
    races are refunded in full (net 0) and excluded from win/loss
    streaks, since a refund is neither a win nor a loss."""
    bet_records = [r for r in records if r.result != RESULT_SKIPPED]
    n_skipped = sum(1 for r in records if r.result == RESULT_SKIPPED)
    n_void = sum(1 for r in records if r.result == RESULT_VOID)

    total_stake = sum(r.stake_yen for r in bet_records)
    total_payout = sum(r.payout_yen for r in bet_records)

    cumulative = 0
    peak = 0
    max_drawdown = 0
    for r in bet_records:
        cumulative += r.payout_yen - r.stake_yen
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)

    win_loss_only = [r for r in bet_records if r.result in (RESULT_WIN, RESULT_LOSS)]

    return SimulationSummary(
        n_bets=len(bet_records),
        n_skipped=n_skipped,
        n_void=n_void,
        total_stake_yen=total_stake,
        total_payout_yen=total_payout,
        net_return_yen=total_payout - total_stake,
        max_drawdown_yen=max_drawdown,
        max_win_streak=_max_streak(win_loss_only, RESULT_WIN),
        max_loss_streak=_max_streak(win_loss_only, RESULT_LOSS),
    )
