"""Why a betting rule made money -- decomposed against counterfactuals.

`evaluate_bet_types` reports what a rule returned. It cannot say *why*,
and the difference matters: an ROI above 1.0 can come from the model
ranking boats correctly, from the rule drifting into an odds band the
market misprices on its own, from ten lucky payouts, or -- the failure
mode this module was written to catch -- from selecting on a price that
is only knowable after betting has closed.

Every analysis here is run over one candidate-level table rather than an
aggregate, because every one of those explanations produces the *same*
aggregate and a different table:

| question | analysis |
|---|---|
| does the rule reproduce the published figure? | `rule_results` |
| is the return just the odds band? | `blind_counterfactual(by_combination=False)` |
| is it the band plus the lane/combination? | `blind_counterfactual(by_combination=True)` |
| could any plausible probability vector do it? | `permutation_null` |
| is it ten payouts? | `tail_concentration` |
| does it hold month to month? | `monthly_stability` |
| is the price we select on the price we get? | `quote_realization`, `noise_sensitivity` |

Selection price vs settlement price
-----------------------------------

This is the distinction the module exists for. The archived closing
snapshot *is* the settlement price -- measured on the runtime host, a
単勝 winner's payout is 0.9955 of its archived closing odds, median
exactly 1.0000 (the shortfall is 同着, which splits the pool). So an EV
rule scored against closing odds is selecting on `p x (the amount this
ticket will actually pay)`, which is not available at the moment a bet
must be placed.

`market_round` exists to score the same rule against a price that *was*
available: `"t-60"`, `"t-10"` and `"t-2"` read the live pre-deadline
capture and keep the latest quote inside that window. Comparing rounds
against `"closing"` is the only honest test of whether an edge survives
execution, and `noise_sensitivity` estimates the same thing from the
closing data alone by blurring the selection price -- an optimistic
bound, since it models price movement as noise and therefore leaves out
the late informed money that moves prices *toward* the eventual winner.

Probabilities come from `evaluate_bet_types._candidates` and payouts from
its `_load_payouts`, imported deliberately rather than re-implemented: a
reproduction check against a figure that module published is worthless if
the two disagree about how a candidate's probability or settlement is
built.
"""

from __future__ import annotations

import argparse
import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from sqlalchemy import DateTime, bindparam, text
from sqlalchemy.orm import Session

from ..combination_model import (
    PlackettLuceSecondPlace,
    construct_trifecta_probabilities,
)
from ..exacta import construct_exacta_probabilities
from .dataset_cache import (
    add_cache_arguments,
    build_dataset_cached,
    cache_options,
    report_to_stderr,
)
from .evaluate_bet_types import (
    BET_TYPE_SPECS,
    SPECIAL_REFUND_COMBINATION,
    _candidates,
    _load_payouts,
)
from .evaluate_p1 import sklearn_logistic_factory
from .evaluate_p2 import TRIM_TOP_PAYOUTS
from .session import create_db_engine, create_session_factory

LANES = (1, 2, 3, 4, 5, 6)

DEFAULT_BET_TYPES = ("win", "place", "wide")
DEFAULT_THRESHOLDS = (1.0, 1.2, 1.5)

PRICE_BANDS = (1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 10.0, 20.0, 50.0)
"""Upper edges are open; a candidate priced 3.0 lands in `[3.0, 4.0)`.
Chosen to be finer where the pools are dense (under 10x) and coarse in
the tail, so a band's blind return is an average over enough tickets to
mean something."""

MARKET_ROUNDS = ("closing", "t-60", "t-10", "t-2")

_ROUND_MAX_LEAD_MINUTES = {"t-2": 5.0, "t-10": 20.0, "t-60": 90.0}
"""Upper bound on `deadline - available_at` for a quote to count as that
round. `capture_odds` aims at 60/10/2 minutes out but runs on a two-minute
cron, so the observed lead scatters around the target and a round has to
be a window rather than an instant."""

_LIVE_ODDS_SQL = """
SELECT o.race_id, o.bet_type, o.combination, o.odds, o.available_at,
       r.scheduled_deadline_at
  FROM odds_snapshots o
  JOIN races r ON r.id = o.race_id
 WHERE o.bet_type IN :odds_bet_types
   AND o.is_closing = false
   AND o.odds > 0
   AND r.race_date >= :start_date
   AND r.race_date <= :end_date
"""

_CLOSING_ODDS_SQL = """
SELECT o.race_id, o.bet_type, o.combination, o.odds
  FROM odds_snapshots o
  JOIN races r ON r.id = o.race_id
 WHERE o.bet_type IN :odds_bet_types
   AND o.is_closing = true
   AND o.odds > 0
   AND r.race_date >= :start_date
   AND r.race_date <= :end_date
"""

_RACE_SQL = """
SELECT r.id, r.race_date, r.venue_id
  FROM races r
 WHERE r.race_date >= :start_date
   AND r.race_date <= :end_date
"""


class AttributionError(ValueError):
    """Raised for a request this module cannot answer, as opposed to one
    whose answer is "no edge"."""


@dataclass
class CandidateTable:
    """One pool's whole test window, as aligned `(races, candidates)`
    matrices.

    Matrices rather than a row list because every analysis below is a
    different reduction over the same three of them, and the permutation
    null in particular needs to replace one race's probability *vector*
    with another's -- which is a row shuffle here and a regrouping in any
    row-oriented layout.

    `odds` holds 0.0 where the pool carried no quote for that candidate,
    and `complete` marks the races where every candidate had one. An EV
    rule must select only inside `complete`: a partial grid lets the rule
    pick from a biased subset of the pool, which reads as skill.
    """

    bet_type: str
    combinations: tuple[str, ...]
    race_ids: list = field(default_factory=list)
    race_dates: list = field(default_factory=list)
    venue_ids: list = field(default_factory=list)
    prob: np.ndarray = field(default_factory=lambda: np.zeros((0, 0)))
    odds: np.ndarray = field(default_factory=lambda: np.zeros((0, 0)))
    ret: np.ndarray = field(default_factory=lambda: np.zeros((0, 0)))
    hit: np.ndarray = field(default_factory=lambda: np.zeros((0, 0), dtype=bool))
    refund: np.ndarray = field(default_factory=lambda: np.zeros((0, 0), dtype=bool))
    complete: np.ndarray = field(default_factory=lambda: np.zeros((0,), dtype=bool))

    @property
    def races(self) -> int:
        return len(self.race_ids)

    @property
    def ev(self) -> np.ndarray:
        return self.prob * self.odds


@dataclass
class Selection:
    """The bets one rule made: parallel row/column indices into a table."""

    rule: str
    threshold: float
    rows: np.ndarray
    cols: np.ndarray

    def __len__(self) -> int:
        return len(self.rows)


def select(table: CandidateTable, rule: str, threshold: float = 0.0) -> Selection:
    """Apply one rule to a table.

    `confidence` needs no price and so runs on every race; `ev_best` and
    `ev_all` run only on races with a complete grid, matching
    `evaluate_bet_types`.
    """
    if table.races == 0:
        empty = np.zeros(0, dtype=int)
        return Selection(rule, threshold, empty, empty)
    if rule == "confidence":
        rows = np.arange(table.races)
        return Selection(rule, threshold, rows, table.prob.argmax(axis=1))

    ev = table.ev
    if rule == "ev_best":
        cols = ev.argmax(axis=1)
        rows = np.arange(table.races)
        keep = table.complete & (ev[rows, cols] >= threshold)
        return Selection(rule, threshold, rows[keep], cols[keep])
    if rule == "ev_all":
        keep = (ev >= threshold) & table.complete[:, None]
        rows, cols = np.nonzero(keep)
        return Selection(rule, threshold, rows, cols)
    raise AttributionError(f"unknown rule: {rule!r}")


def _returns(table: CandidateTable, selection: Selection) -> np.ndarray:
    return table.ret[selection.rows, selection.cols]


@dataclass
class RuleResult:
    bet_type: str
    rule: str
    threshold: float
    bets: int
    races: int
    hits: int
    roi: float
    trimmed_roi: float
    median_odds: float

    @property
    def hit_rate(self) -> float:
        return self.hits / self.bets if self.bets else 0.0

    def __str__(self) -> str:
        return (
            f"{self.bet_type:<6} {self.rule:<10} thresh={self.threshold:<5} "
            f"bets={self.bets:<7} hit={100 * self.hit_rate:5.2f}% "
            f"ROI={self.roi:.4f} trimmed={self.trimmed_roi:.4f} "
            f"median_odds={self.median_odds:.2f}"
        )


def summarize(table: CandidateTable, selection: Selection) -> RuleResult:
    """ROI, hit rate and the tail-trimmed ROI, defined exactly as
    `evaluate_p2.RuleResult` defines them so the figures are comparable."""
    ret = _returns(table, selection)
    hit = table.hit[selection.rows, selection.cols]
    odds = table.odds[selection.rows, selection.cols]
    bets = len(ret)
    trimmed = float("nan")
    if bets > TRIM_TOP_PAYOUTS:
        top = np.sort(ret[hit])[::-1][:TRIM_TOP_PAYOUTS]
        trimmed = float((ret.sum() - top.sum()) / (bets - len(top)))
    return RuleResult(
        bet_type=table.bet_type,
        rule=selection.rule,
        threshold=selection.threshold,
        bets=bets,
        races=len(np.unique(selection.rows)) if bets else 0,
        hits=int(hit.sum()),
        roi=float(ret.mean()) if bets else 0.0,
        trimmed_roi=trimmed,
        median_odds=float(np.median(odds)) if bets else 0.0,
    )


def _band_index(odds: np.ndarray) -> np.ndarray:
    return np.digitize(odds, PRICE_BANDS)


def band_label(index: int) -> str:
    if index <= 0:
        return "<1.0"
    if index >= len(PRICE_BANDS):
        return f"{PRICE_BANDS[-1]:.0f}+"
    return f"{PRICE_BANDS[index - 1]:g}-{PRICE_BANDS[index]:g}"


@dataclass
class BandRow:
    band: str
    candidates: int
    hit_rate: float
    blind_roi: float

    def __str__(self) -> str:
        return (
            f"  {self.band:<8} candidates={self.candidates:<7} "
            f"hit={100 * self.hit_rate:5.2f}% blind_ROI={self.blind_roi:.4f}"
        )


def price_bands(table: CandidateTable) -> list[BandRow]:
    """What backing *every* priced candidate in a band would have paid.

    The market's own shape, with no model in it -- the reference any
    claim of model skill has to beat.
    """
    mask = table.complete[:, None] & (table.odds > 0)
    if not mask.any():
        return []
    bands = _band_index(table.odds[mask])
    ret = table.ret[mask]
    hit = table.hit[mask]
    rows = []
    for index in sorted(set(bands.tolist())):
        cell = bands == index
        rows.append(
            BandRow(
                band=band_label(index),
                candidates=int(cell.sum()),
                hit_rate=float(hit[cell].mean()),
                blind_roi=float(ret[cell].mean()),
            )
        )
    return rows


@dataclass
class Counterfactual:
    rule: str
    bets: int
    covered: int
    rule_roi: float
    blind_roi: float
    controls: str

    @property
    def lift(self) -> float:
        return self.rule_roi / self.blind_roi if self.blind_roi else float("nan")

    def __str__(self) -> str:
        return (
            f"  {self.rule:<16} bets={self.bets:<7} covered={self.covered:<7} "
            f"rule_ROI={self.rule_roi:.4f} blind_ROI={self.blind_roi:.4f} "
            f"lift={self.lift:.4f}  [{self.controls}]"
        )


def blind_counterfactual(
    table: CandidateTable, selection: Selection, *, by_combination: bool = False
) -> Counterfactual:
    """The rule's ROI against the same bets bought blind.

    "Blind" means: for each bet, the average return of *every* candidate
    in the window sharing its price band (and, with `by_combination`, its
    lane or combination too), then averaged over the bets. It answers
    "would buying at these prices, in these lanes, at random have done as
    well" -- the question that reframed the 2026-08-04 walk-forward
    result, where most of an apparent edge turned out to be the band.

    A bet whose cell has no other members is dropped rather than imputed;
    `covered` reports how many bets the comparison actually spans.
    """
    ret = _returns(table, selection)
    if not len(ret):
        return Counterfactual(
            f"{selection.rule}@{selection.threshold}", 0, 0, 0.0, 0.0, "band"
        )

    mask = table.complete[:, None] & (table.odds > 0)
    pool_bands = _band_index(table.odds)
    universe_keys = pool_bands[mask]
    universe_ret = table.ret[mask]
    if by_combination:
        columns = np.broadcast_to(np.arange(table.odds.shape[1]), table.odds.shape)
        universe_keys = universe_keys * table.odds.shape[1] + columns[mask]

    if not len(universe_keys):
        return Counterfactual(
            rule=f"{selection.rule}@{selection.threshold}",
            bets=len(ret),
            covered=0,
            rule_roi=float(ret.mean()),
            blind_roi=float("nan"),
            controls="band+combination" if by_combination else "band",
        )

    order = np.argsort(universe_keys, kind="stable")
    sorted_keys = universe_keys[order]
    sorted_ret = universe_ret[order]
    unique_keys, starts = np.unique(sorted_keys, return_index=True)
    sums = np.add.reduceat(sorted_ret, starts)
    counts = np.diff(np.append(starts, len(sorted_ret)))
    means = sums / counts

    bet_keys = pool_bands[selection.rows, selection.cols]
    if by_combination:
        bet_keys = bet_keys * table.odds.shape[1] + selection.cols
    position = np.searchsorted(unique_keys, bet_keys)
    position = np.clip(position, 0, len(unique_keys) - 1)
    covered = unique_keys[position] == bet_keys
    if not covered.any():
        blind = float("nan")
    else:
        blind = float(means[position[covered]].mean())
    return Counterfactual(
        rule=f"{selection.rule}@{selection.threshold}",
        bets=len(ret),
        covered=int(covered.sum()),
        rule_roi=float(ret.mean()),
        blind_roi=blind,
        controls="band+combination" if by_combination else "band",
    )


@dataclass
class PermutationNull:
    rule: str
    real_bets: int
    real_roi: float
    null_roi_mean: float
    null_roi_max: float
    null_roi_sd: float
    permutations: int

    @property
    def z(self) -> float:
        if not self.null_roi_sd:
            return float("nan")
        return (self.real_roi - self.null_roi_mean) / self.null_roi_sd

    def __str__(self) -> str:
        return (
            f"  {self.rule:<16} real_ROI={self.real_roi:.4f} "
            f"null_mean={self.null_roi_mean:.4f} null_max={self.null_roi_max:.4f} "
            f"null_sd={self.null_roi_sd:.4f} z={self.z:.1f} (n={self.permutations})"
        )


def permutation_null(
    table: CandidateTable,
    *,
    rule: str = "ev_best",
    threshold: float = 1.5,
    permutations: int = 40,
    seed: int = 0,
) -> PermutationNull:
    """Re-run the rule with each race's probability vector replaced by
    another race's, leaving every price and payout where it is.

    This is the null the aggregate cannot distinguish itself from: if an
    EV rule profits because of the *shape* of the odds grid rather than
    because the probabilities are about these boats, a shuffled model
    will profit too.
    """
    real = summarize(table, select(table, rule, threshold))
    rng = np.random.default_rng(seed)
    original = table.prob
    rois = []
    try:
        for _ in range(permutations):
            table.prob = original[rng.permutation(table.races)]
            rois.append(summarize(table, select(table, rule, threshold)).roi)
    finally:
        table.prob = original
    null = np.asarray(rois, dtype=float)
    return PermutationNull(
        rule=f"{rule}@{threshold}",
        real_bets=real.bets,
        real_roi=real.roi,
        null_roi_mean=float(null.mean()),
        null_roi_max=float(null.max()),
        null_roi_sd=float(null.std()),
        permutations=permutations,
    )


@dataclass
class TailConcentration:
    rule: str
    bets: int
    roi: float
    top1_percent_share: float
    top10_share: float
    roi_low: float
    roi_high: float
    prob_above_breakeven: float

    def __str__(self) -> str:
        return (
            f"  {self.rule:<16} bets={self.bets:<7} ROI={self.roi:.4f} "
            f"top1%_of_bets={100 * self.top1_percent_share:5.2f}% of return, "
            f"top10={100 * self.top10_share:5.2f}%  "
            f"95%CI=[{self.roi_low:.4f}, {self.roi_high:.4f}] "
            f"P(ROI>1)={self.prob_above_breakeven:.3f}"
        )


def tail_concentration(
    table: CandidateTable,
    selection: Selection,
    *,
    resamples: int = 2000,
    seed: int = 0,
) -> TailConcentration:
    """How much of the return rides on the largest payouts, and how wide
    the sampling error is.

    The bootstrap is over bets rather than races, which understates
    dependence for `ev_all` (several bets on one race share an outcome).
    Read it as a floor on the interval, not a confidence statement.
    """
    ret = _returns(table, selection)
    label = f"{selection.rule}@{selection.threshold}"
    if len(ret) < 2 or ret.sum() <= 0:
        return TailConcentration(label, len(ret), 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    descending = np.sort(ret)[::-1]
    top_n = max(1, len(ret) // 100)
    rng = np.random.default_rng(seed)
    boot = rng.choice(ret, size=(resamples, len(ret)), replace=True).mean(axis=1)
    return TailConcentration(
        rule=label,
        bets=len(ret),
        roi=float(ret.mean()),
        top1_percent_share=float(descending[:top_n].sum() / ret.sum()),
        top10_share=float(descending[:10].sum() / ret.sum()),
        roi_low=float(np.percentile(boot, 2.5)),
        roi_high=float(np.percentile(boot, 97.5)),
        prob_above_breakeven=float((boot > 1.0).mean()),
    )


@dataclass
class MonthRow:
    month: str
    bets: int
    hit_rate: float
    roi: float

    def __str__(self) -> str:
        return (
            f"  {self.month}  bets={self.bets:<6} hit={100 * self.hit_rate:5.2f}% "
            f"ROI={self.roi:.4f}"
        )


def monthly_stability(table: CandidateTable, selection: Selection) -> list[MonthRow]:
    """Per-calendar-month ROI. A rule whose whole edge lives in one month
    is a regime, not an edge."""
    if not len(selection):
        return []
    ret = _returns(table, selection)
    hit = table.hit[selection.rows, selection.cols]
    months = np.array(
        [f"{table.race_dates[row].year:04d}-{table.race_dates[row].month:02d}" for row in selection.rows]
    )
    rows = []
    for month in sorted(set(months.tolist())):
        cell = months == month
        rows.append(
            MonthRow(
                month=month,
                bets=int(cell.sum()),
                hit_rate=float(hit[cell].mean()),
                roi=float(ret[cell].mean()),
            )
        )
    return rows


@dataclass
class QuoteRealization:
    bet_type: str
    winners: int
    mean_ratio: float
    median_ratio: float

    def __str__(self) -> str:
        return (
            f"  {self.bet_type:<6} winners={self.winners:<7} "
            f"paid/quoted mean={self.mean_ratio:.4f} median={self.median_ratio:.4f}"
        )


def quote_realization(table: CandidateTable) -> QuoteRealization:
    """Realized payout divided by the odds the rule selected on, over
    winning tickets.

    Three separate things show up here, and they have to be told apart:
    1.0 means the quote is the settlement price (単勝's archived closing
    odds); above 1.0 means the stored quote is the low end of a quoted
    *range* (複勝 and 拡連複, ~1.19x and ~1.07x on the runtime host);
    below 1.0 on a live round means the price moved against the ticket
    between the quote and the bell.
    """
    winners = table.hit & (table.odds > 0)
    if not winners.any():
        return QuoteRealization(table.bet_type, 0, float("nan"), float("nan"))
    ratio = table.ret[winners] / table.odds[winners]
    return QuoteRealization(
        bet_type=table.bet_type,
        winners=int(winners.sum()),
        mean_ratio=float(ratio.mean()),
        median_ratio=float(np.median(ratio)),
    )


@dataclass
class NoiseSensitivity:
    rule: str
    sigma: float
    real_roi: float
    noisy_roi_mean: float
    noisy_roi_sd: float
    noisy_bets_mean: float
    draws: int

    def __str__(self) -> str:
        return (
            f"  {self.rule:<16} sigma={self.sigma:.2f} real_ROI={self.real_roi:.4f} "
            f"noisy_ROI={self.noisy_roi_mean:.4f} (sd {self.noisy_roi_sd:.4f}) "
            f"noisy_bets={self.noisy_bets_mean:.0f} (n={self.draws})"
        )


def noise_sensitivity(
    table: CandidateTable,
    *,
    rule: str = "ev_best",
    threshold: float = 1.5,
    sigma: float = 0.6,
    draws: int = 20,
    seed: int = 0,
) -> NoiseSensitivity:
    """Re-run the rule when the *selection* price is the settlement price
    blurred by lognormal noise, settling at the true payout regardless.

    The bridge between a closing-odds backtest and a live one, without
    waiting for months of live capture: `sigma` is the standard deviation
    of `log(quoted / paid)` measured on the live rounds. It is an
    optimistic bound -- real price movement is not independent of the
    outcome, since late money moves toward the boat that goes on to win,
    so the live figure should land below this one.
    """
    real = summarize(table, select(table, rule, threshold))
    rng = np.random.default_rng(seed)
    original = table.odds
    rois: list[float] = []
    bets: list[int] = []
    try:
        for _ in range(draws):
            table.odds = original * np.exp(rng.normal(0.0, sigma, size=original.shape))
            noisy = select(table, rule, threshold)
            # Selection uses the blurred price; settlement does not.
            table.odds = original
            result = summarize(table, noisy)
            rois.append(result.roi)
            bets.append(result.bets)
    finally:
        table.odds = original
    return NoiseSensitivity(
        rule=f"{rule}@{threshold}",
        sigma=sigma,
        real_roi=real.roi,
        noisy_roi_mean=float(np.mean(rois)),
        noisy_roi_sd=float(np.std(rois)),
        noisy_bets_mean=float(np.mean(bets)),
        draws=draws,
    )


def _load_market(
    session: Session,
    specs,
    *,
    market_round: str,
    start_date: dt.date,
    end_date: dt.date,
) -> dict:
    """`{race_id: {bet_type_key: {combination: odds}}}` for one round.

    For a live round the latest quote inside the round's window wins, so
    a race captured twice in the same window contributes the reading a
    bettor would have seen last.
    """
    if market_round not in MARKET_ROUNDS:
        raise AttributionError(
            f"unknown market round {market_round!r}; known: {list(MARKET_ROUNDS)}"
        )
    wanted = {spec.odds_bet_type: spec.key for spec in specs}
    if market_round == "closing":
        statement = text(_CLOSING_ODDS_SQL)
    else:
        # The two timestamps have to be declared: a raw `text()` query gets
        # no result processing, and SQLite hands back the strings it stored,
        # so the lead-time arithmetic below would work on PostgreSQL and
        # raise on the test fixtures.
        statement = text(_LIVE_ODDS_SQL).columns(
            available_at=DateTime(timezone=True),
            scheduled_deadline_at=DateTime(timezone=True),
        )
    statement = statement.bindparams(bindparam("odds_bet_types", expanding=True))
    rows = session.execute(
        statement,
        {
            "odds_bet_types": list(wanted),
            "start_date": start_date,
            "end_date": end_date,
        },
    )
    market: dict = {}
    seen_at: dict = {}
    max_lead = _ROUND_MAX_LEAD_MINUTES.get(market_round)
    lower_bounds = sorted(_ROUND_MAX_LEAD_MINUTES.values())
    min_lead = 0.0
    if max_lead is not None:
        earlier = [bound for bound in lower_bounds if bound < max_lead]
        min_lead = earlier[-1] if earlier else 0.0
    for row in rows:
        key = wanted[row.bet_type]
        if market_round != "closing":
            lead = (row.scheduled_deadline_at - row.available_at).total_seconds() / 60.0
            if lead < min_lead or lead > max_lead or lead < 0:
                continue
            previous = seen_at.get((row.race_id, key, row.combination))
            if previous is not None and previous >= row.available_at:
                continue
            seen_at[(row.race_id, key, row.combination)] = row.available_at
        market.setdefault(row.race_id, {}).setdefault(key, {})[row.combination] = float(
            row.odds
        )
    return market


def build_candidate_tables(
    session: Session,
    *,
    train_start: dt.date,
    train_end: dt.date,
    test_start: dt.date,
    test_end: dt.date,
    bet_types: tuple[str, ...] = DEFAULT_BET_TYPES,
    market_round: str = "closing",
    include_before_info: bool = False,
    include_racer_stats: bool = False,
    cache_dir: Path | None = None,
    refresh_cache: bool = False,
) -> dict[str, CandidateTable]:
    """Fit the P1 model on the train window and lay the test window out as
    one matrix set per pool."""
    if train_end >= test_start:
        raise AttributionError(
            f"train_end {train_end} must precede test_start {test_start}; "
            "overlapping windows would score the model on its own training rows"
        )
    unknown = [b for b in bet_types if b not in BET_TYPE_SPECS]
    if unknown:
        raise AttributionError(f"unknown bet types {unknown}; known: {sorted(BET_TYPE_SPECS)}")

    train = build_dataset_cached(
        session,
        start_date=train_start,
        end_date=train_end,
        include_before_info=include_before_info,
        include_racer_stats=include_racer_stats,
        cache_dir=cache_dir,
        refresh=refresh_cache,
        on_event=report_to_stderr,
    )
    if not len(train):
        raise AttributionError(f"no usable training races between {train_start} and {train_end}")
    model = sklearn_logistic_factory()()
    model.fit(train.X, train.y)

    test = build_dataset_cached(
        session,
        start_date=test_start,
        end_date=test_end,
        include_before_info=include_before_info,
        include_racer_stats=include_racer_stats,
        cache_dir=cache_dir,
        refresh=refresh_cache,
        on_event=report_to_stderr,
    )
    specs = [BET_TYPE_SPECS[b] for b in bet_types]
    tables = {spec.key: CandidateTable(spec.key, ()) for spec in specs}
    if not len(test):
        return tables

    probabilities = model.predict_proba(test.X)
    market = _load_market(
        session, specs, market_round=market_round, start_date=test_start, end_date=test_end
    )
    payouts = _load_payouts(session, specs, start_date=test_start, end_date=test_end)
    meta = {
        row.id: row
        for row in session.execute(
            text(_RACE_SQL), {"start_date": test_start, "end_date": test_end}
        )
    }
    needs_trifecta = any(spec.key in ("trifecta", "sanrenpuku", "wide") for spec in specs)

    collected: dict[str, dict] = {
        spec.key: {"ids": [], "dates": [], "venues": [], "prob": [], "odds": [], "ret": [],
                   "hit": [], "refund": [], "complete": [], "order": None}
        for spec in specs
    }
    for race_id, probs_row in zip(test.race_ids, probabilities):
        race_payouts = payouts.get(race_id)
        if not race_payouts:
            continue
        race_market = market.get(race_id) or {}
        info = meta.get(race_id)
        probs = {lane: float(probs_row[i]) for i, lane in enumerate(LANES)}
        joint = construct_exacta_probabilities(probs, PlackettLuceSecondPlace(probs))
        trifecta_joint = (
            construct_trifecta_probabilities(joint, probs) if needs_trifecta else None
        )
        for spec in specs:
            settled = race_payouts.get(spec.key)
            if settled is None:
                continue
            candidate_probs = _candidates(spec.key, probs, joint, trifecta_joint)
            bucket = collected[spec.key]
            if bucket["order"] is None:
                bucket["order"] = tuple(sorted(candidate_probs))
            order = bucket["order"]
            odds = race_market.get(spec.key) or {}
            refund = settled.get(SPECIAL_REFUND_COMBINATION)
            returns, hits, refunds = [], [], []
            for combination in order:
                payout = settled.get(combination)
                if payout is not None:
                    returns.append(payout)
                    hits.append(True)
                    refunds.append(False)
                elif refund is not None:
                    returns.append(refund)
                    hits.append(False)
                    refunds.append(True)
                else:
                    returns.append(0.0)
                    hits.append(False)
                    refunds.append(False)
            bucket["ids"].append(race_id)
            bucket["dates"].append(info.race_date if info else test_start)
            bucket["venues"].append(info.venue_id if info else None)
            bucket["prob"].append([candidate_probs[c] for c in order])
            bucket["odds"].append([odds.get(c, 0.0) for c in order])
            bucket["ret"].append(returns)
            bucket["hit"].append(hits)
            bucket["refund"].append(refunds)
            bucket["complete"].append(len(odds) >= spec.candidates)

    for spec in specs:
        bucket = collected[spec.key]
        table = tables[spec.key]
        if not bucket["ids"]:
            continue
        table.combinations = bucket["order"]
        table.race_ids = bucket["ids"]
        table.race_dates = bucket["dates"]
        table.venue_ids = bucket["venues"]
        table.prob = np.asarray(bucket["prob"], dtype=float)
        table.odds = np.asarray(bucket["odds"], dtype=float)
        table.ret = np.asarray(bucket["ret"], dtype=float)
        table.hit = np.asarray(bucket["hit"], dtype=bool)
        table.refund = np.asarray(bucket["refund"], dtype=bool)
        table.complete = np.asarray(bucket["complete"], dtype=bool)
    return tables


@dataclass
class PoolAttribution:
    bet_type: str
    races: int
    priced_races: int
    rules: list[RuleResult] = field(default_factory=list)
    bands: list[BandRow] = field(default_factory=list)
    counterfactuals: list[Counterfactual] = field(default_factory=list)
    nulls: list[PermutationNull] = field(default_factory=list)
    tails: list[TailConcentration] = field(default_factory=list)
    months: list[MonthRow] = field(default_factory=list)
    realization: QuoteRealization | None = None
    noise: list[NoiseSensitivity] = field(default_factory=list)

    def __str__(self) -> str:
        lines = [
            f"== {self.bet_type} == races={self.races} priced_races={self.priced_races}",
            " rules:",
            *[f"  {r}" for r in self.rules],
            " price bands (no model):",
            *[str(b) for b in self.bands],
            " counterfactuals:",
            *[str(c) for c in self.counterfactuals],
            " permutation null:",
            *[str(n) for n in self.nulls],
            " tail concentration:",
            *[str(t) for t in self.tails],
            " monthly:",
            *[str(m) for m in self.months],
        ]
        if self.realization is not None:
            lines += [" quote realization:", str(self.realization)]
        if self.noise:
            lines += [" selection-price noise sensitivity:", *[str(n) for n in self.noise]]
        return "\n".join(lines)


@dataclass
class AttributionReport:
    market_round: str
    train_start: dt.date
    train_end: dt.date
    test_start: dt.date
    test_end: dt.date
    pools: list[PoolAttribution] = field(default_factory=list)

    def __str__(self) -> str:
        head = (
            f"market_round={self.market_round} "
            f"train={self.train_start}..{self.train_end} "
            f"test={self.test_start}..{self.test_end}"
        )
        return "\n\n".join([head, *[str(p) for p in self.pools]])


def attribute(
    session: Session,
    *,
    train_start: dt.date,
    train_end: dt.date,
    test_start: dt.date,
    test_end: dt.date,
    bet_types: tuple[str, ...] = DEFAULT_BET_TYPES,
    thresholds: tuple[float, ...] = DEFAULT_THRESHOLDS,
    market_round: str = "closing",
    permutations: int = 40,
    noise_sigma: float = 0.6,
    noise_draws: int = 20,
    seed: int = 0,
    include_before_info: bool = False,
    include_racer_stats: bool = False,
    cache_dir: Path | None = None,
    refresh_cache: bool = False,
) -> AttributionReport:
    tables = build_candidate_tables(
        session,
        train_start=train_start,
        train_end=train_end,
        test_start=test_start,
        test_end=test_end,
        bet_types=bet_types,
        market_round=market_round,
        include_before_info=include_before_info,
        include_racer_stats=include_racer_stats,
        cache_dir=cache_dir,
        refresh_cache=refresh_cache,
    )
    report = AttributionReport(
        market_round=market_round,
        train_start=train_start,
        train_end=train_end,
        test_start=test_start,
        test_end=test_end,
    )
    for bet_type in bet_types:
        table = tables[bet_type]
        pool = PoolAttribution(
            bet_type=bet_type,
            races=table.races,
            priced_races=int(table.complete.sum()) if table.races else 0,
        )
        if table.races:
            pool.rules.append(summarize(table, select(table, "confidence")))
            for threshold in thresholds:
                for rule in ("ev_best", "ev_all"):
                    selection = select(table, rule, threshold)
                    pool.rules.append(summarize(table, selection))
                    if rule == "ev_best" and len(selection):
                        pool.counterfactuals.append(
                            blind_counterfactual(table, selection, by_combination=False)
                        )
                        pool.counterfactuals.append(
                            blind_counterfactual(table, selection, by_combination=True)
                        )
                        pool.tails.append(tail_concentration(table, selection, seed=seed))
            pool.bands = price_bands(table)
            pool.realization = quote_realization(table)
            top = thresholds[-1]
            if permutations:
                pool.nulls.append(
                    permutation_null(
                        table, threshold=top, permutations=permutations, seed=seed
                    )
                )
            if noise_draws and market_round == "closing":
                pool.noise.append(
                    noise_sensitivity(
                        table,
                        threshold=top,
                        sigma=noise_sigma,
                        draws=noise_draws,
                        seed=seed,
                    )
                )
            pool.months = monthly_stability(table, select(table, "ev_best", top))
        report.pools.append(pool)
    return report


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--train-start", type=dt.date.fromisoformat, required=True)
    parser.add_argument("--train-end", type=dt.date.fromisoformat, required=True)
    parser.add_argument("--test-start", type=dt.date.fromisoformat, required=True)
    parser.add_argument("--test-end", type=dt.date.fromisoformat, required=True)
    parser.add_argument("--bet-types", default=",".join(DEFAULT_BET_TYPES))
    parser.add_argument(
        "--market-round",
        choices=MARKET_ROUNDS,
        default="closing",
        help=(
            "which price the rule selects on: closing = the archived deadline "
            "snapshot, which is also the settlement price and therefore not "
            "available at bet time; t-60/t-10/t-2 = the live pre-deadline capture"
        ),
    )
    parser.add_argument("--thresholds", default="1.0,1.2,1.5")
    parser.add_argument("--permutations", type=int, default=40)
    parser.add_argument(
        "--noise-sigma",
        type=float,
        default=0.6,
        help="sd of log(quoted/paid); 0.66 for 単勝 and 0.56 for 複勝 on the live rounds",
    )
    parser.add_argument("--noise-draws", type=int, default=20)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--with-before-info", action="store_true")
    parser.add_argument("--with-racer-stats", action="store_true")
    add_cache_arguments(parser)
    args = parser.parse_args(argv)

    engine = create_db_engine(args.database_url)
    try:
        with create_session_factory(engine)() as session:
            report = attribute(
                session,
                train_start=args.train_start,
                train_end=args.train_end,
                test_start=args.test_start,
                test_end=args.test_end,
                bet_types=tuple(b.strip() for b in args.bet_types.split(",") if b.strip()),
                thresholds=tuple(
                    float(t) for t in args.thresholds.split(",") if t.strip()
                ),
                market_round=args.market_round,
                permutations=args.permutations,
                noise_sigma=args.noise_sigma,
                noise_draws=args.noise_draws,
                seed=args.seed,
                include_before_info=args.with_before_info,
                include_racer_stats=args.with_racer_stats,
                **cache_options(args),
            )
    finally:
        engine.dispose()
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
