"""Select on expected value, not on confidence, and settle at real payouts.

Every ROI figure this project has produced selected bets by the *model's
own probability* -- "back the argmax lane when it clears 0.70". That is a
rule for finding likely winners, and it has been measured repeatedly:
0.910 at threshold 0, rising to 0.924 at 0.80, plateauing, reversing at
0.90. It never approached 1.0000, and the reason is visible in the same
table -- the model's accuracy converts into shorter prices, so picking
winners better buys nothing.

**A rule that selects on price has never been run here.** It is a
different question: not "which boat wins" but "which boat is paying more
than it should". In a pari-mutuel with takeout `t`, backing every boat
returns `1 - t` no matter how good the predictions are; the only way out
is to bet the subset where the model's probability exceeds the market's
implied one by enough to cover `t`. `expected_value.py` has existed since
P2-T003 for exactly this and has never been pointed at real rows.

What this measures
------------------

For each race, the model gives `p_i` over the six lanes and the market
gives `o_i` (odds, stake included). A ¥100 bet on lane `i` returns
`p_i * o_i` in expectation, so `ev_i = p_i * o_i` is directly the
expected return per unit staked and `ev_i > 1` is the break-even
condition. No calibration step stands between them, which is deliberate:
a recalibration measured on this data made both log-loss and ECE worse
(tasks/HANDOFF.md, 2026-08-01), and inserting one here would make the
result a joint test of two things.

Three rules are reported so the comparison is like-for-like on the same
races:

- `confidence` -- the historical rule. Back the argmax `p_i` when it
  clears the threshold. Included so a reader can see the two rules'
  numbers side by side rather than against a figure from another run.
- `ev_best` -- back the single highest-EV lane when its EV clears the
  threshold. One bet per race at most.
- `ev_all` -- back *every* lane whose EV clears the threshold. More than
  one bet per race is possible, and this is the rule that actually
  follows from the arithmetic: if two lanes are both underpriced, both
  are worth backing.

The honest limitation
---------------------

The odds are the archived **closing** prices, stamped at the deadline
(`odds_source`'s module docstring: exactly one observation per race is
ever published). Nobody can bet at them. So a positive result here would
not be a strategy -- it would be evidence that the mispricing exists at
all, which is the necessary condition for the pre-deadline capture
running since 2026-08-01 to be worth evaluating. A *negative* result is
the more decisive direction: an edge absent at closing prices, which are
the market's best and final judgement, will not appear at the noisier
prices available earlier.

Train/test is a single split at `test_start`, not walk-forward. The odds
window is 80 days wide; carving three folds out of it would leave each
one too small to separate from noise, and the split point is the only
thing a fold structure would have varied.
"""

from __future__ import annotations

import argparse
import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.orm import Session

from .dataset import LANES, build_dataset
from .evaluate_p1 import sklearn_logistic_factory
from .session import create_db_engine, create_session_factory

DEFAULT_THRESHOLDS = (0.00, 0.50, 0.70, 0.80)
DEFAULT_EV_THRESHOLDS = (1.00, 1.05, 1.10, 1.20, 1.50)

# Closing win odds and the settled 単勝 payout for one race, per lane.
# `payout_yen` is NULL for every lane but the winner, which is what makes
# the settlement real rather than a probability-weighted estimate.
_MARKET_SQL = """
SELECT o.race_id,
       o.combination AS lane,
       o.odds,
       pay.payout_yen
  FROM odds_snapshots o
  JOIN race_results rr ON rr.race_id = o.race_id
  LEFT JOIN race_payouts pay
         ON pay.race_result_id = rr.id
        AND pay.bet_type = '単勝'
        AND pay.combination = o.combination
        AND pay.payout_yen > 0
 WHERE o.bet_type = 'win'
   AND o.is_closing
   AND o.odds > 0
"""


TRIM_TOP_PAYOUTS = 10
"""How many of the largest payouts `trimmed_roi` sets aside.

Reported next to the raw ROI because the raw figure alone is misleading
here in a way that is easy to miss: when a rule is free to back 100x
boats, a handful of hits can carry the whole result. Measured on the
first real run -- `ev_all` at threshold 1.5 returned 1.5309 over 4,472
bets, and dropping the twenty largest payouts (0.45% of the bets) took
it to 1.1071. A rule whose ROI collapses under this is reporting the
tail, not an edge.
"""


@dataclass
class RuleResult:
    rule: str
    threshold: float
    bets: int = 0
    races: int = 0
    hits: int = 0
    staked: float = 0.0
    returned: float = 0.0
    payouts: list[float] = field(default_factory=list)

    @property
    def hit_rate(self) -> float:
        return self.hits / self.bets if self.bets else 0.0

    @property
    def roi(self) -> float:
        return self.returned / self.staked if self.staked else 0.0

    @property
    def trimmed_roi(self) -> float:
        """ROI with the `TRIM_TOP_PAYOUTS` largest wins removed, stake and
        all. `nan` when there are too few bets for the trim to mean
        anything."""
        if self.bets <= TRIM_TOP_PAYOUTS:
            return float("nan")
        top = sorted(self.payouts, reverse=True)[:TRIM_TOP_PAYOUTS]
        return (self.returned - sum(top)) / (self.staked - len(top))

    def __str__(self) -> str:
        return (
            f"{self.rule:<10} thresh={self.threshold:<5} bets={self.bets:<7} "
            f"races={self.races:<6} hit={100 * self.hit_rate:5.2f}% "
            f"ROI={self.roi:.4f} trimmed={self.trimmed_roi:.4f}"
        )


@dataclass
class EvaluationP2Result:
    train_races: int = 0
    test_races: int = 0
    priced_races: int = 0
    rules: list[RuleResult] = field(default_factory=list)

    def __str__(self) -> str:
        head = (
            f"train_races={self.train_races} test_races={self.test_races} "
            f"priced_races={self.priced_races}"
        )
        return "\n".join([head, *(str(r) for r in self.rules)])


def _market(session: Session, race_ids: list) -> dict:
    """`{race_id: {lane: (odds, payout_or_None)}}` for the races that have
    closing odds. A race with no odds row is absent rather than empty, so
    the caller counts it as unpriced instead of betting blind on it."""
    if not race_ids:
        return {}
    wanted = set(race_ids)
    market: dict = {}
    for row in session.execute(text(_MARKET_SQL)):
        race_id = row.race_id
        if race_id not in wanted:
            continue
        market.setdefault(race_id, {})[int(row.lane)] = (
            float(row.odds),
            None if row.payout_yen is None else float(row.payout_yen) / 100.0,
        )
    return {rid: lanes for rid, lanes in market.items() if len(lanes) == len(LANES)}


def _settle(rule: RuleResult, lane: int, lanes: dict, winner: int) -> None:
    """Record one ¥1 bet on `lane` and whatever it actually returned.

    A losing bet still adds to `staked`, which is the whole point -- an
    ROI that only counted the races it won would be a different and much
    prettier number.
    """
    rule.bets += 1
    rule.staked += 1.0
    payout = lanes[lane][1]
    if lane == winner and payout is not None:
        rule.hits += 1
        rule.returned += payout
        rule.payouts.append(payout)


def evaluate_p2(
    session: Session,
    *,
    train_start: dt.date,
    train_end: dt.date,
    test_start: dt.date,
    test_end: dt.date,
    thresholds: tuple[float, ...] = DEFAULT_THRESHOLDS,
    ev_thresholds: tuple[float, ...] = DEFAULT_EV_THRESHOLDS,
    include_before_info: bool = False,
) -> EvaluationP2Result:
    if train_end >= test_start:
        raise ValueError(
            f"train_end {train_end} must precede test_start {test_start}; "
            "overlapping windows would score the model on its own training rows"
        )

    train = build_dataset(
        session,
        start_date=train_start,
        end_date=train_end,
        include_before_info=include_before_info,
    )
    if not len(train):
        raise ValueError(f"no usable training races between {train_start} and {train_end}")

    model = sklearn_logistic_factory()()
    model.fit(train.X, train.y)

    test = build_dataset(
        session,
        start_date=test_start,
        end_date=test_end,
        include_before_info=include_before_info,
    )
    result = EvaluationP2Result(train_races=len(train), test_races=len(test))
    if not len(test):
        return result

    market = _market(session, test.race_ids)
    # `sklearn_logistic_factory` already wraps the estimator in
    # `_AlignedProba`, so these columns are in lane order 1..6 rather than
    # in whatever order training happened to see the classes. Reading them
    # positionally off a bare estimator would raise nothing and score
    # every lane after a gap against the wrong probability.
    probabilities = model.predict_proba(test.X)

    rules = {
        ("confidence", t): RuleResult("confidence", t) for t in thresholds
    }
    rules.update({("ev_best", t): RuleResult("ev_best", t) for t in ev_thresholds})
    rules.update({("ev_all", t): RuleResult("ev_all", t) for t in ev_thresholds})

    for race_id, probs, winner in zip(test.race_ids, probabilities, test.y):
        lanes = market.get(race_id)
        if lanes is None:
            continue
        result.priced_races += 1
        evs = {lane: probs[i] * lanes[lane][0] for i, lane in enumerate(LANES)}
        best_p_lane = max(LANES, key=lambda lane: probs[LANES.index(lane)])
        best_p = probs[LANES.index(best_p_lane)]
        best_ev_lane = max(LANES, key=lambda lane: evs[lane])

        for threshold in thresholds:
            rule = rules[("confidence", threshold)]
            if best_p >= threshold:
                rule.races += 1
                _settle(rule, best_p_lane, lanes, winner)

        for threshold in ev_thresholds:
            rule = rules[("ev_best", threshold)]
            if evs[best_ev_lane] >= threshold:
                rule.races += 1
                _settle(rule, best_ev_lane, lanes, winner)

            rule = rules[("ev_all", threshold)]
            selected = [lane for lane in LANES if evs[lane] >= threshold]
            if selected:
                rule.races += 1
                for lane in selected:
                    _settle(rule, lane, lanes, winner)

    result.rules = list(rules.values())
    return result


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--train-start", type=dt.date.fromisoformat, required=True)
    parser.add_argument("--train-end", type=dt.date.fromisoformat, required=True)
    parser.add_argument("--test-start", type=dt.date.fromisoformat, required=True)
    parser.add_argument("--test-end", type=dt.date.fromisoformat, required=True)
    parser.add_argument("--with-before-info", action="store_true")
    args = parser.parse_args(argv)

    engine = create_db_engine(args.database_url)
    try:
        with create_session_factory(engine)() as session:
            result = evaluate_p2(
                session,
                train_start=args.train_start,
                train_end=args.train_end,
                test_start=args.test_start,
                test_end=args.test_end,
                include_before_info=args.with_before_info,
            )
    finally:
        engine.dispose()
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
