"""Per-bet-type expected-value breakdown, settled at real payouts.

`evaluate_p2` asked "does selecting on price beat selecting on
confidence" and could only ask it of 単勝, because that was the only pool
whose full odds grid had ever been loaded. This runner asks the same
question of every pool where a grid actually exists, at a fixed ¥100
stake, so the answer comes back as a table of bet types rather than a
single number.

Why a fixed ¥100 stake and not a per-race budget
------------------------------------------------

The obvious framing -- "if I had ¥10,000 per race, what would I have made
and how should it have been split" -- conflates two questions that have
to be answered in order. Staking multiplies expected value; it does not
create it. With the takeout at ~25%, every allocation of a fixed budget
across a set of bets whose EV is below 1.0 loses the same ~25%, and an
optimizer pointed at that problem will find whichever combination
happened to win in the window it was shown. The heavy tail makes this
worse rather than better: a single 3連単 hit can carry an entire
backtest.

So this measures per-bet-type EV at a stake small enough that the
arithmetic is honest, and reports `staked_yen` and `bets_per_race` so
the budget implication of each rule is visible without being optimized.
¥100 is also the largest stake at which the displayed odds can be taken
at face value: these are pari-mutuel pools, a bet is *in* the pool it is
priced against, and a ¥10,000 ticket on a thin combination moves its own
price -- worst on exactly the long shots an EV rule selects.

Which bet types can be measured, and which cannot
-------------------------------------------------

Settling a *fixed* rule needs only the winning combination's payout, and
`race_payouts` has that for all seven pools over 21 years. Selecting on
price needs the odds of every candidate, including the ones that lose,
and that grid exists for far fewer:

| pool | odds grid | settled payouts |
|---|---|---|
| 単勝 | closing, 2025-07-29.. | 21 years |
| 複勝 | closing, 2025-07-29.. | 21 years |
| 2連単 | live capture only, from 2026-08-03 | 21 years |
| 2連複 | live capture only, from 2026-08-03 | 21 years |
| 拡連複 / 3連単 / 3連複 | **none** | 21 years |

`UNPRICED_BET_TYPES` names the last row and this runner reports it
explicitly instead of quietly leaving it out of the table. A rule for
those pools could be settled but not *selected*, and a selection rule is
the only thing with a live hypothesis behind it, so a number for them
would answer a question nobody asked.

Where the probabilities come from
---------------------------------

One P1 model, extended to the combination pools by
`combination_model` rather than by fitting a separate model per pool:
every probability here is a marginal of the same coherent 30-class
exacta joint, so the pools cannot contradict each other. The conditional
second place is chosen by measurement, not assertion -- `--second-place`
selects between the Plackett-Luce conditional implied by P1 itself and
`second_place.ConditionalSecondPlaceModel`'s lane-frequency counts, and
both are scored on held-out second-place log-loss in the same run.

Settlement
----------

Uniform across pools and deliberately dumb: a bet returns
`payout_yen / 100` if its combination key appears in that race's payout
rows for that pool, and 0 otherwise. Nothing reconstructs an outcome
from finish positions, so 同着 (which pays two 単勝 combinations) and
void races need no special case -- they settle at whatever the race
actually paid.

The one exception is 特払い, a ¥70-per-¥100 consolation paid when no
ticket holds the winning combination. It is a legacy artifact -- 5,309
単勝 races in 2005 against 22 in 2025 -- and is counted as a partial
return rather than a loss, in its own `refunds` column so it never reads
as a hit.
"""

from __future__ import annotations

import argparse
import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from ..combination_model import (
    PlackettLuceSecondPlace,
    quinella_probabilities,
    top2_probabilities,
)
from ..exacta import construct_exacta_probabilities, decode_combination
from ..metrics import multiclass_log_loss
from ..second_place import ConditionalSecondPlaceModel
from .dataset import LANES, build_dataset
from .evaluate_p1 import sklearn_logistic_factory
from .evaluate_p2 import RuleResult
from .session import create_db_engine, create_session_factory

DEFAULT_EV_THRESHOLDS = (1.00, 1.05, 1.10, 1.20, 1.50)
DEFAULT_CONFIDENCE_THRESHOLD = 0.00

STAKE_YEN = 100
"""The unit bet. Every `staked`/`returned` figure below is in units of
this stake, so `staked_yen` is just `bets * STAKE_YEN`."""

SPECIAL_REFUND_COMBINATION = "特払い"

UNPRICED_BET_TYPES = ("拡連複", "３連単", "３連複")
"""Pools with settled payouts but no odds grid anywhere in this database.

Reported by name in the output. Fetching a grid for them is possible
(the site renders it) but has never been run, and until it is, no
EV-selected figure for these pools can exist -- only a confidence-selected
one, which is the rule already measured and rejected for 単勝.
"""


@dataclass(frozen=True)
class BetTypeSpec:
    """How one pool maps onto the odds table and the payout table.

    `odds_bet_type` is `odds_snapshots.bet_type`; `payout_bet_type` is
    `race_payouts.bet_type`, which is written in Japanese with full-width
    digits ("２連単") because that is what the K-file carries.
    """

    key: str
    label: str
    odds_bet_type: str
    payout_bet_type: str
    candidates: int


BET_TYPE_SPECS: dict[str, BetTypeSpec] = {
    "win": BetTypeSpec("win", "単勝", "win", "単勝", 6),
    "place": BetTypeSpec("place", "複勝", "place_low", "複勝", 6),
    "exacta": BetTypeSpec("exacta", "2連単", "exacta", "２連単", 30),
    "quinella": BetTypeSpec("quinella", "2連複", "quinella", "２連複", 15),
}

DEFAULT_BET_TYPES = tuple(BET_TYPE_SPECS)

# Odds for every pool in one pass. `is_closing` separates the archived
# deadline snapshot from the live pre-deadline capture; mixing them would
# average two different measurements, so the caller picks one.
#
# Both queries bound themselves by `races.race_date` rather than by a list
# of race ids: the id list is ~40,000 long for a real window, which is
# neither portable as a bound parameter nor indexable, while the date
# range is the same restriction expressed against a column both tables
# can be joined on. Rows for a race the dataset dropped are simply never
# looked up.
_ODDS_SQL = """
SELECT o.race_id, o.bet_type, o.combination, o.odds
  FROM odds_snapshots o
  JOIN races r ON r.id = o.race_id
 WHERE o.bet_type IN :odds_bet_types
   AND o.is_closing = :is_closing
   AND o.odds > 0
   AND r.race_date >= :start_date
   AND r.race_date <= :end_date
"""

# The settled money. `payout_yen` is per ¥100 ticket.
_PAYOUT_SQL = """
SELECT rr.race_id, p.bet_type, p.combination, p.payout_yen
  FROM race_payouts p
  JOIN race_results rr ON rr.id = p.race_result_id
  JOIN races r ON r.id = rr.race_id
 WHERE p.bet_type IN :payout_bet_types
   AND r.race_date >= :start_date
   AND r.race_date <= :end_date
"""


@dataclass
class BetTypeRuleResult(RuleResult):
    """`RuleResult` plus the pool it belongs to and the 特払い column.

    Subclassed rather than re-implemented so `roi`, `hit_rate` and
    `trimmed_roi` -- including the tail-trim constant that a previous run
    showed to matter by 0.4 ROI points -- stay identical to the figures
    `evaluate_p2` already published.
    """

    bet_type: str = "win"
    refunds: int = 0

    @property
    def staked_yen(self) -> int:
        return self.bets * STAKE_YEN

    @property
    def bets_per_race(self) -> float:
        return self.bets / self.races if self.races else 0.0

    def __str__(self) -> str:
        return (
            f"{self.bet_type:<6} {self.rule:<10} thresh={self.threshold:<5} "
            f"bets={self.bets:<7} races={self.races:<6} "
            f"bets/race={self.bets_per_race:4.1f} "
            f"staked=¥{self.staked_yen:<10,} hit={100 * self.hit_rate:5.2f}% "
            f"ROI={self.roi:.4f} trimmed={self.trimmed_roi:.4f}"
            + (f" refunds={self.refunds}" if self.refunds else "")
        )


@dataclass
class SecondPlaceComparison:
    """Held-out second-place log-loss for the two conditionals.

    Present so `--second-place` is a measured choice rather than a
    preference. Scored only on races that produced a single second place;
    `n` says how many that was.
    """

    n: int = 0
    plackett_luce: float = float("nan")
    lane_frequency: float = float("nan")

    def __str__(self) -> str:
        return (
            f"second-place log-loss over n={self.n}: "
            f"plackett_luce={self.plackett_luce:.5f} "
            f"lane_frequency={self.lane_frequency:.5f}"
        )


@dataclass
class BetTypeEvaluation:
    train_races: int = 0
    test_races: int = 0
    priced_races: dict[str, int] = field(default_factory=dict)
    rules: list[BetTypeRuleResult] = field(default_factory=list)
    second_place: SecondPlaceComparison = field(default_factory=SecondPlaceComparison)
    unpriced: tuple[str, ...] = UNPRICED_BET_TYPES

    def __str__(self) -> str:
        priced = " ".join(f"{k}={v}" for k, v in sorted(self.priced_races.items()))
        lines = [
            f"train_races={self.train_races} test_races={self.test_races}",
            f"priced_races: {priced}",
            str(self.second_place),
            "no odds grid, not evaluated: " + ", ".join(self.unpriced),
            "",
        ]
        lines.extend(str(r) for r in self.rules)
        return "\n".join(lines)


def _combination_key(bet_type_key: str, candidate) -> str:
    """The string `race_payouts.combination` uses for this candidate.

    Single lane for 単勝/複勝, `"i-j"` for the two-boat pools -- ordered
    for 2連単, low-high for 2連複, matching how the K-file writes them.
    """
    if bet_type_key in ("win", "place"):
        return str(candidate)
    return f"{candidate[0]}-{candidate[1]}"


def _candidates(bet_type_key: str, probs: dict[int, float], joint: dict[int, float]):
    """`{combination_key: probability}` for one pool, all of it summed out
    of the same exacta joint so the pools stay mutually consistent."""
    if bet_type_key == "win":
        return {str(lane): probs[lane] for lane in LANES}
    if bet_type_key == "place":
        return {str(lane): p for lane, p in top2_probabilities(joint).items()}
    if bet_type_key == "exacta":
        return {
            _combination_key("exacta", decode_combination(code)): p for code, p in joint.items()
        }
    if bet_type_key == "quinella":
        return {
            _combination_key("quinella", pair): p
            for pair, p in quinella_probabilities(joint).items()
        }
    raise ValueError(f"unknown bet type: {bet_type_key!r}")


def _load_market(session: Session, specs, *, is_closing: bool, start_date, end_date):
    """`{race_id: {bet_type_key: {combination: odds}}}`."""
    wanted = {spec.odds_bet_type: spec.key for spec in specs}
    statement = text(_ODDS_SQL).bindparams(bindparam("odds_bet_types", expanding=True))
    market: dict = {}
    rows = session.execute(
        statement,
        {
            "odds_bet_types": list(wanted),
            "is_closing": is_closing,
            "start_date": start_date,
            "end_date": end_date,
        },
    )
    for row in rows:
        key = wanted[row.bet_type]
        market.setdefault(row.race_id, {}).setdefault(key, {})[row.combination] = float(row.odds)
    return market


def _load_payouts(session: Session, specs, *, start_date, end_date):
    """`{race_id: {bet_type_key: {combination: return_per_unit_stake}}}`.

    Includes the 特払い row under its own key so the settlement step can
    tell a consolation from a win.
    """
    wanted = {spec.payout_bet_type: spec.key for spec in specs}
    statement = text(_PAYOUT_SQL).bindparams(bindparam("payout_bet_types", expanding=True))
    payouts: dict = {}
    rows = session.execute(
        statement,
        {
            "payout_bet_types": list(wanted),
            "start_date": start_date,
            "end_date": end_date,
        },
    )
    for row in rows:
        key = wanted[row.bet_type]
        payouts.setdefault(row.race_id, {}).setdefault(key, {})[row.combination] = (
            float(row.payout_yen) / 100.0
        )
    return payouts


def _settle(rule: BetTypeRuleResult, combination: str, race_payouts: dict) -> None:
    """Record one unit bet and what it actually returned."""
    rule.bets += 1
    rule.staked += 1.0
    payout = race_payouts.get(combination)
    if payout is not None:
        rule.hits += 1
        rule.returned += payout
        rule.payouts.append(payout)
        return
    refund = race_payouts.get(SPECIAL_REFUND_COMBINATION)
    if refund is not None:
        # Not a hit -- nobody held the winning combination, so every
        # ticket in the pool gets the same consolation.
        rule.refunds += 1
        rule.returned += refund


def _score_second_place(
    probabilities, test, conditional_model: ConditionalSecondPlaceModel
) -> SecondPlaceComparison:
    """Held-out log-loss for both conditionals on the races that produced
    a single second place."""
    pl_rows: list[list[float]] = []
    lf_rows: list[list[float]] = []
    actual: list[int] = []
    for probs_row, winner, second in zip(probabilities, test.y, test.y_second):
        if second is None:
            continue
        probs = {lane: float(probs_row[i]) for i, lane in enumerate(LANES)}
        pl = PlackettLuceSecondPlace(probs).predict(winner)
        lf = conditional_model.predict(winner)
        pl_rows.append([pl[lane] for lane in LANES])
        lf_rows.append([lf[lane] for lane in LANES])
        actual.append(int(second))
    if not actual:
        return SecondPlaceComparison()
    return SecondPlaceComparison(
        n=len(actual),
        plackett_luce=multiclass_log_loss(actual, pl_rows, list(LANES)),
        lane_frequency=multiclass_log_loss(actual, lf_rows, list(LANES)),
    )


def evaluate_bet_types(
    session: Session,
    *,
    train_start: dt.date,
    train_end: dt.date,
    test_start: dt.date,
    test_end: dt.date,
    bet_types: tuple[str, ...] = DEFAULT_BET_TYPES,
    ev_thresholds: tuple[float, ...] = DEFAULT_EV_THRESHOLDS,
    is_closing: bool = True,
    second_place: str = "plackett_luce",
    include_before_info: bool = False,
    include_racer_stats: bool = False,
) -> BetTypeEvaluation:
    if train_end >= test_start:
        raise ValueError(
            f"train_end {train_end} must precede test_start {test_start}; "
            "overlapping windows would score the model on its own training rows"
        )
    unknown = [b for b in bet_types if b not in BET_TYPE_SPECS]
    if unknown:
        raise ValueError(f"unknown bet types {unknown}; known: {sorted(BET_TYPE_SPECS)}")
    if second_place not in ("plackett_luce", "lane_frequency"):
        raise ValueError(f"unknown second-place conditional: {second_place!r}")

    train = build_dataset(
        session,
        start_date=train_start,
        end_date=train_end,
        include_before_info=include_before_info,
        include_racer_stats=include_racer_stats,
    )
    if not len(train):
        raise ValueError(f"no usable training races between {train_start} and {train_end}")

    model = sklearn_logistic_factory()()
    model.fit(train.X, train.y)

    # The lane-frequency conditional is fitted on the training window
    # only, for the same reason the classifier is: it is a model, and
    # scoring it on rows it counted would not be a held-out number.
    observations = [
        (int(w), int(s)) for w, s in zip(train.y, train.y_second) if s is not None
    ]
    conditional_model = ConditionalSecondPlaceModel().fit(observations)

    test = build_dataset(
        session,
        start_date=test_start,
        end_date=test_end,
        include_before_info=include_before_info,
        include_racer_stats=include_racer_stats,
    )
    result = BetTypeEvaluation(train_races=len(train), test_races=len(test))
    if not len(test):
        return result

    # `sklearn_logistic_factory` wraps the estimator so these columns are
    # in lane order 1..6 regardless of which classes training saw.
    probabilities = model.predict_proba(test.X)
    result.second_place = _score_second_place(probabilities, test, conditional_model)

    specs = [BET_TYPE_SPECS[b] for b in bet_types]
    market = _load_market(
        session, specs, is_closing=is_closing, start_date=test_start, end_date=test_end
    )
    payouts = _load_payouts(session, specs, start_date=test_start, end_date=test_end)

    rules: dict[tuple, BetTypeRuleResult] = {}
    for spec in specs:
        rules[(spec.key, "confidence", DEFAULT_CONFIDENCE_THRESHOLD)] = BetTypeRuleResult(
            "confidence", DEFAULT_CONFIDENCE_THRESHOLD, bet_type=spec.key
        )
        for threshold in ev_thresholds:
            rules[(spec.key, "ev_best", threshold)] = BetTypeRuleResult(
                "ev_best", threshold, bet_type=spec.key
            )
            rules[(spec.key, "ev_all", threshold)] = BetTypeRuleResult(
                "ev_all", threshold, bet_type=spec.key
            )
    result.priced_races = {spec.key: 0 for spec in specs}

    for race_id, probs_row in zip(test.race_ids, probabilities):
        race_market = market.get(race_id)
        race_payouts = payouts.get(race_id)
        if not race_market or not race_payouts:
            continue
        probs = {lane: float(probs_row[i]) for i, lane in enumerate(LANES)}
        conditional = (
            PlackettLuceSecondPlace(probs)
            if second_place == "plackett_luce"
            else conditional_model
        )
        joint = construct_exacta_probabilities(probs, conditional)

        for spec in specs:
            odds = race_market.get(spec.key)
            settled = race_payouts.get(spec.key)
            # A partial grid would let a rule pick from a biased subset of
            # the pool, so an incomplete race is skipped rather than
            # evaluated on what happened to be captured.
            if not odds or settled is None or len(odds) < spec.candidates:
                continue
            result.priced_races[spec.key] += 1
            candidate_probs = _candidates(spec.key, probs, joint)
            evs = {c: candidate_probs[c] * o for c, o in odds.items() if c in candidate_probs}
            if not evs:
                continue

            best_p = max(evs, key=lambda c: candidate_probs[c])
            rule = rules[(spec.key, "confidence", DEFAULT_CONFIDENCE_THRESHOLD)]
            rule.races += 1
            _settle(rule, best_p, settled)

            best_ev = max(evs, key=lambda c: evs[c])
            for threshold in ev_thresholds:
                rule = rules[(spec.key, "ev_best", threshold)]
                if evs[best_ev] >= threshold:
                    rule.races += 1
                    _settle(rule, best_ev, settled)

                rule = rules[(spec.key, "ev_all", threshold)]
                selected = [c for c, ev in evs.items() if ev >= threshold]
                if selected:
                    rule.races += 1
                    for combination in selected:
                        _settle(rule, combination, settled)

    result.rules = list(rules.values())
    return result


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--train-start", type=dt.date.fromisoformat, required=True)
    parser.add_argument("--train-end", type=dt.date.fromisoformat, required=True)
    parser.add_argument("--test-start", type=dt.date.fromisoformat, required=True)
    parser.add_argument("--test-end", type=dt.date.fromisoformat, required=True)
    parser.add_argument(
        "--bet-types",
        default=",".join(DEFAULT_BET_TYPES),
        help=f"comma-separated subset of {sorted(BET_TYPE_SPECS)}",
    )
    parser.add_argument(
        "--odds",
        choices=("closing", "live"),
        default="closing",
        help=(
            "closing = the archived deadline snapshot (単勝/複勝 only, but years of it); "
            "live = the pre-deadline capture (all four pools, but only since 2026-08)"
        ),
    )
    parser.add_argument(
        "--second-place",
        choices=("plackett_luce", "lane_frequency"),
        default="plackett_luce",
        help="conditional used to build the exacta joint; both are scored either way",
    )
    parser.add_argument("--with-before-info", action="store_true")
    parser.add_argument("--with-racer-stats", action="store_true")
    args = parser.parse_args(argv)

    engine = create_db_engine(args.database_url)
    try:
        with create_session_factory(engine)() as session:
            result = evaluate_bet_types(
                session,
                train_start=args.train_start,
                train_end=args.train_end,
                test_start=args.test_start,
                test_end=args.test_end,
                bet_types=tuple(b.strip() for b in args.bet_types.split(",") if b.strip()),
                is_closing=args.odds == "closing",
                second_place=args.second_place,
                include_before_info=args.with_before_info,
                include_racer_stats=args.with_racer_stats,
            )
    finally:
        engine.dispose()
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
