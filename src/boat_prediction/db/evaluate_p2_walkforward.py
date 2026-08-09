"""Walk-forward EV selection: choose the rule on the past, score it on the next month.

`evaluate_p2` fits once and reports every threshold on the same test set.
That is the right shape for finding out whether an effect exists, and the
wrong shape for claiming a return: the best-looking threshold there was
chosen after seeing the number it produced. The standing caveat on that
result says so ("the thresholds and the odds cap were chosen after seeing
the data"), and this is what removes it.

Method
------

**One pass for probabilities, so every one is out-of-sample.** For each
month in the odds window, the model is fit on every race before that
month and predicts it. Selecting a threshold on in-sample probabilities
would be worse than useless: the model is over-confident on its own
training rows, so the EV distribution used for selection would not match
the one the rule meets in the test month.

**Then the rule is chosen from prior months only** and applied, once, to
the next. Nothing about the test month enters the choice — not the
threshold, not the odds cap, not the lane group. Repeat monthly, so there
are several independent test periods rather than one.

Why monthly rather than a single split: the odds window runs 2025-07 to
2026-04, so a single split would train on summer and autumn and test on
winter and spring. Water temperature, wind and surface state all move
with the season -- lane 1's return was measured at 0.9181 in stable water
against 0.8667 in unstable -- so a failure would be unattributable
between overfitting and season. Adjacent months keep that small, and the
per-fold record shows whether the rule holds up repeatedly or won once.

Selection criterion
-------------------

Trimmed ROI on the training window, with a minimum bet count. Raw ROI is
tail-sensitive enough that a candidate can win the selection on a single
large payout, which is exactly the failure the trimmed figure was
introduced to catch. The minimum count stops a rule that fired eleven
times from being chosen at all.

The test month reports **both** raw and trimmed, because trimmed is a
diagnostic and raw is what a bettor would actually have received.
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from .dataset import LANES
from .dataset_cache import (
    add_cache_arguments,
    build_dataset_cached,
    cache_options,
    report_to_stderr,
)
from .evaluate_p1 import sklearn_logistic_factory
from .evaluate_p2 import TRIM_TOP_PAYOUTS, _market
from .session import create_db_engine, create_session_factory

EV_THRESHOLDS = (1.00, 1.05, 1.10, 1.20, 1.35, 1.50)
ODDS_CAPS: tuple[float | None, ...] = (None, 30.0, 50.0)
LANE_GROUPS: dict[str, tuple[int, ...]] = {
    "all": LANES,
    "lane1": (1,),
    "outside": (2, 3, 4, 5, 6),
}
MIN_TRAIN_BETS = 300
"""Below this a training ROI is too noisy to choose on. A rule that fired
a handful of times can top the table on one payout, and picking it would
be selecting noise with extra steps."""


@dataclass(frozen=True)
class Rule:
    ev_threshold: float
    odds_cap: float | None
    lane_group: str

    def __str__(self) -> str:
        cap = "none" if self.odds_cap is None else f"{self.odds_cap:g}x"
        return f"EV>={self.ev_threshold:.2f} cap={cap} lanes={self.lane_group}"


@dataclass
class Book:
    bets: int = 0
    hits: int = 0
    staked: float = 0.0
    returned: float = 0.0
    payouts: list[float] = field(default_factory=list)

    def add(self, payout: float | None, won: bool) -> None:
        self.bets += 1
        self.staked += 1.0
        if won and payout is not None:
            self.hits += 1
            self.returned += payout
            self.payouts.append(payout)

    @property
    def roi(self) -> float:
        return self.returned / self.staked if self.staked else 0.0

    @property
    def trimmed_roi(self) -> float:
        if self.bets <= TRIM_TOP_PAYOUTS:
            return float("nan")
        top = sorted(self.payouts, reverse=True)[:TRIM_TOP_PAYOUTS]
        return (self.returned - sum(top)) / (self.staked - len(top))

    @property
    def hit_rate(self) -> float:
        return self.hits / self.bets if self.bets else 0.0


@dataclass
class MonthRow:
    """One race-lane, with an out-of-sample probability and its price."""

    month: str
    probability: float
    odds: float
    payout: float | None
    lane: int
    won: bool


@dataclass
class FoldResult:
    test_month: str
    train_months: int
    chosen: Rule | None
    train_bets: int
    train_trimmed_roi: float
    book: Book

    def __str__(self) -> str:
        if self.chosen is None:
            return f"{self.test_month}  (no rule cleared the minimum bet count)"
        return (
            f"{self.test_month}  {self.chosen!s:<38}"
            f"bets={self.book.bets:<6} hit={100 * self.book.hit_rate:5.2f}% "
            f"ROI={self.book.roi:.4f} trimmed={self.book.trimmed_roi:.4f}"
        )


def _month_key(day: dt.date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def _month_bounds(month: str) -> tuple[dt.date, dt.date]:
    year, mon = (int(part) for part in month.split("-"))
    start = dt.date(year, mon, 1)
    end = dt.date(year + (mon == 12), (mon % 12) + 1, 1) - dt.timedelta(days=1)
    return start, end


def build_month_rows(
    session: Session,
    *,
    model_start: dt.date,
    months: list[str],
    include_before_info: bool,
    include_racer_stats: bool,
    log=print,
    cache_dir: Path | None = None,
    refresh_cache: bool = False,
) -> list[MonthRow]:
    """Out-of-sample probability and price for every priced race-lane.

    The model for month M is fit on `model_start .. M-1` only, so no row
    below was scored by a model that had seen it.
    """
    rows: list[MonthRow] = []
    for month in months:
        start, end = _month_bounds(month)
        train_end = start - dt.timedelta(days=1)
        if train_end < model_start:
            continue

        train = build_dataset_cached(
            session,
            start_date=model_start,
            end_date=train_end,
            include_before_info=include_before_info,
            include_racer_stats=include_racer_stats,
            cache_dir=cache_dir,
            refresh=refresh_cache,
            on_event=report_to_stderr,
        )
        if not len(train):
            continue
        model = sklearn_logistic_factory()()
        model.fit(train.X, train.y)

        test = build_dataset_cached(
            session,
            start_date=start,
            end_date=end,
            include_before_info=include_before_info,
            include_racer_stats=include_racer_stats,
            cache_dir=cache_dir,
            refresh=refresh_cache,
            on_event=report_to_stderr,
        )
        if not len(test):
            continue
        market = _market(session, test.race_ids)
        probabilities = model.predict_proba(test.X)

        priced = 0
        for race_id, probs, winner in zip(test.race_ids, probabilities, test.y):
            lanes = market.get(race_id)
            if lanes is None:
                continue
            priced += 1
            for index, lane in enumerate(LANES):
                odds, payout = lanes[lane]
                rows.append(
                    MonthRow(
                        month=month,
                        probability=float(probs[index]),
                        odds=odds,
                        payout=payout,
                        lane=lane,
                        won=lane == winner,
                    )
                )
        log(f"  {month}: train_races={len(train):,} priced_races={priced:,}")
    return rows


def _apply(rule: Rule, rows: list[MonthRow]) -> Book:
    book = Book()
    lanes = LANE_GROUPS[rule.lane_group]
    for row in rows:
        if row.lane not in lanes:
            continue
        if rule.odds_cap is not None and row.odds > rule.odds_cap:
            continue
        if row.probability * row.odds < rule.ev_threshold:
            continue
        book.add(row.payout, row.won)
    return book


def choose_rule(train_rows: list[MonthRow]) -> tuple[Rule | None, Book | None]:
    """The rule with the best trimmed training ROI, among those that bet
    often enough for the figure to mean anything."""
    best: tuple[Rule, Book] | None = None
    for ev in EV_THRESHOLDS:
        for cap in ODDS_CAPS:
            for group in LANE_GROUPS:
                rule = Rule(ev, cap, group)
                book = _apply(rule, train_rows)
                if book.bets < MIN_TRAIN_BETS:
                    continue
                score = book.trimmed_roi
                if math.isnan(score):
                    continue
                if best is None or score > best[1].trimmed_roi:
                    best = (rule, book)
    return best if best else (None, None)


def walk_forward(
    session: Session,
    *,
    model_start: dt.date,
    odds_start: dt.date,
    odds_end: dt.date,
    min_train_months: int = 3,
    include_before_info: bool = True,
    include_racer_stats: bool = True,
    log=print,
    cache_dir: Path | None = None,
    refresh_cache: bool = False,
) -> list[FoldResult]:
    months: list[str] = []
    cursor = dt.date(odds_start.year, odds_start.month, 1)
    while cursor <= odds_end:
        months.append(_month_key(cursor))
        cursor = dt.date(cursor.year + (cursor.month == 12), (cursor.month % 12) + 1, 1)

    log(f"months in the odds window: {len(months)}  ({months[0]} .. {months[-1]})")
    rows = build_month_rows(
        session,
        model_start=model_start,
        months=months,
        include_before_info=include_before_info,
        include_racer_stats=include_racer_stats,
        log=log,
        cache_dir=cache_dir,
        refresh_cache=refresh_cache,
    )
    by_month: dict[str, list[MonthRow]] = {}
    for row in rows:
        by_month.setdefault(row.month, []).append(row)
    log(f"priced race-lanes: {len(rows):,}")

    present = [m for m in months if m in by_month]
    folds: list[FoldResult] = []
    for index, month in enumerate(present):
        if index < min_train_months:
            continue
        train_rows = [r for m in present[:index] for r in by_month[m]]
        rule, train_book = choose_rule(train_rows)
        if rule is None:
            folds.append(FoldResult(month, index, None, 0, float("nan"), Book()))
            continue
        folds.append(
            FoldResult(
                test_month=month,
                train_months=index,
                chosen=rule,
                train_bets=train_book.bets,
                train_trimmed_roi=train_book.trimmed_roi,
                book=_apply(rule, by_month[month]),
            )
        )
    return folds


def render(folds: list[FoldResult]) -> str:
    lines = ["", "=== walk-forward: rule chosen on prior months, applied to the next ==="]
    total = Book()
    for fold in folds:
        lines.append(str(fold))
        total.bets += fold.book.bets
        total.hits += fold.book.hits
        total.staked += fold.book.staked
        total.returned += fold.book.returned
        total.payouts.extend(fold.book.payouts)
    scored = [f for f in folds if f.chosen is not None and f.book.bets]
    above = sum(1 for f in scored if f.book.roi > 1.0)
    lines.append("")
    lines.append(
        f"aggregate: bets={total.bets:,} hit={100 * total.hit_rate:.2f}% "
        f"ROI={total.roi:.4f} trimmed={total.trimmed_roi:.4f}"
    )
    lines.append(f"months above break-even: {above}/{len(scored)}")
    return "\n".join(lines)


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--model-start", type=dt.date.fromisoformat, default=dt.date(2023, 6, 1))
    parser.add_argument("--odds-start", type=dt.date.fromisoformat, default=dt.date(2025, 8, 1))
    parser.add_argument("--odds-end", type=dt.date.fromisoformat, default=dt.date(2026, 4, 17))
    parser.add_argument("--min-train-months", type=int, default=3)
    parser.add_argument("--no-before-info", action="store_true")
    parser.add_argument("--no-racer-stats", action="store_true")
    parser.add_argument("--json-out", type=Path, default=None)
    add_cache_arguments(parser)
    args = parser.parse_args(argv)

    engine = create_db_engine(args.database_url)
    try:
        with create_session_factory(engine)() as session:
            folds = walk_forward(
                session,
                model_start=args.model_start,
                odds_start=args.odds_start,
                odds_end=args.odds_end,
                min_train_months=args.min_train_months,
                include_before_info=not args.no_before_info,
                include_racer_stats=not args.no_racer_stats,
                **cache_options(args),
            )
    finally:
        engine.dispose()

    print(render(folds))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
