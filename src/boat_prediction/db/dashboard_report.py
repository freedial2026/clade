"""Build the JSON the PHP dashboard reads (`web/dashboard/`).

The dashboard template (`boatrace_php_multi_venue_dashboard_v4`, vendored
under `web/dashboard/`) expects one PHP array: venues, races, a data
catalog, and a risk block. Its own README says the production wiring is
"create a PHP file returning the same array structure and point
`DASHBOARD_DATA_FILE` at it" -- so the PHP side stays a thin, static-array
consumer, and everything DB-facing happens here, in Python, through the
same SQLAlchemy models every other report in this project uses.

Two keys are added beyond the vendor's schema: `collection_report` (row
counts per source, with date ranges -- "how much has actually been
captured") and `roi_report` (the measured backtest figures from
`evaluate_p2` and the archive, clearly labelled as closing-price
backtests, not a live ledger). Both are additive; `helpers.php`'s
`validate_dashboard_data` only checks its own required keys exist, so
extra ones pass through untouched.

This module never places a bet, real or paper, and never computes one on
the caller's behalf. `paper_betting_enabled` mirrors what the vendor
template already supports (session-only, CSRF-protected, capped at a
fixed small stake); `actual_betting_enabled` is hardcoded `False` and is
not a value this module ever sets otherwise -- automated wagering is out
of scope for the whole project (`docs/PROJECT_PROFILE.md`), not a
per-report toggle.

Decision labels shown here (`candidate` / `waiting` / `skip`) are
descriptive, not a recommendation: a `candidate` marks a race whose
expected value clears a threshold on already-public numbers, the same
arithmetic `evaluate_p2` used to find that even that rule's backtested
edge collapses under a payout-tail check. Nothing here should be read as
investment advice.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..model_registry import DEFAULT_ROLE, ModelRegistry, ModelRegistryError
from .models import (
    BeforeInfoEntry,
    OddsSnapshot,
    Race,
    RaceEntry,
    RacePrediction,
    RacerPeriodCourseStats,
    RacerPeriodStats,
    RaceSurfaceCondition,
    Venue,
)
from .predict_daily import PREVIEW_ROLE
from .session import create_db_engine, create_session_factory

JST = ZoneInfo("Asia/Tokyo")
LANES = (1, 2, 3, 4, 5, 6)

DEFAULT_REGISTRY_PATH = Path("data/models/registry.json")
DEFAULT_OUTPUT_PATH = Path("data/dashboard-snapshot.json")

# Paper-mode display only -- this project has no approved staking policy
# (docs/PROJECT_PROFILE.md: "Initial operation is paper simulation with
# fixed stakes"). These numbers exist so the vendor template has
# something to render; they are not a recommendation and are not read by
# anything that places a bet, because nothing in this project does.
PAPER_BANKROLL_YEN = 100_000
PAPER_DAILY_LIMIT_YEN = 2_000
PAPER_STAKE_UNIT_YEN = 100
PAPER_MAX_STAKE_YEN = 100

EV_CANDIDATE_THRESHOLD = 1.00
"""Expected value (model_probability * odds) at or above which a race is
labelled `candidate` rather than `skip`. `evaluate_p2`'s honest finding
applies here too: this label describes public-number arithmetic that the
market has mostly already priced in, not a discovered edge."""

VENUE_WATER_TYPE = {
    "01": "淡水", "02": "淡水", "03": "汽水", "04": "海水", "05": "淡水",
    "06": "汽水", "07": "海水", "08": "海水", "09": "海水", "10": "淡水",
    "11": "淡水", "12": "淡水", "13": "淡水", "14": "海水", "15": "海水",
    "16": "海水", "17": "海水", "18": "海水", "19": "海水", "20": "海水",
    "21": "海水", "22": "海水", "23": "海水", "24": "海水",
}
"""Cosmetic label only -- not read by anything that computes a feature or
a decision. Public, static, and does not change day to day, so it lives
here as a constant rather than a DB column `models.py` would have to
carry for a fact nothing else in the project needs."""

DATA_CATALOG = {
    "race_card": {"label": "出走表", "critical": True},
    "scheduled_deadline": {"label": "締切予定時刻", "critical": True},
    "racer_period_stats": {"label": "選手期別成績", "critical": True},
    "course_stats": {"label": "コース別成績", "critical": False},
    "motor_boat_stats": {"label": "モーター・ボート成績", "critical": True},
    "prediction_card": {"label": "出走表時点の予測", "critical": True},
    "before_info": {"label": "直前情報", "critical": True},
    "exhibition_time": {"label": "展示タイム", "critical": True},
    "exhibition_course": {"label": "展示進入", "critical": True},
    "surface_conditions": {"label": "風・波・水面情報", "critical": False},
    "parts_change": {"label": "部品交換情報", "critical": False},
    "current_odds": {"label": "現在オッズ", "critical": True},
    "prediction_preview": {"label": "直前情報反映後の予測", "critical": False},
}

# Measured 2026-08-03 (tasks/HANDOFF.md). Static: these are backtests over
# fixed windows and do not change between dashboard refreshes. Re-run
# `db.evaluate_p2` and update this dict by hand when a new evaluation is
# recorded, rather than recomputing a ~40s query on every page load.
ROI_BASELINES = [
    {"label": "単勝 1番人気ベタ", "roi": 0.7868, "n": 11050, "note": "80日窓, 締切オッズ"},
    {"label": "1号艇 単勝ベタ", "roi": 0.9032, "n": 634921, "note": "2015年以降, 全アーカイブ"},
    {
        "label": "モデル確信度で選択 (閾値0.80)",
        "roi": 0.9270,
        "n": 562,
        "note": "confidence選択の最良値。的中率は上がるが払戻が縮み、1.0に届かない",
    },
]

ROI_EV_HYPOTHESIS = {
    "label": "1号艇・EV>=1.2で選択",
    "roi": 1.3210,
    "trimmed_roi": 1.2497,
    "n": 1772,
    "hits": 709,
    "note": (
        "80日窓・締切オッズのみ・事後にオッズ帯を選定。上位10件の配当を除いても残る。"
        "実際に賭けられる価格ではなく、前向き検証は未了。戦略ではなく仮説として扱うこと。"
    ),
}


@dataclass
class SourceCount:
    label: str
    count: int = 0
    distinct_races: int = 0
    since: str | None = None
    through: str | None = None

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "count": self.count,
            "distinct_races": self.distinct_races,
            "since": self.since,
            "through": self.through,
        }


def _iso(value: dt.datetime | None) -> str | None:
    if value is None:
        return None
    value = value if value.tzinfo is not None else value.replace(tzinfo=dt.UTC)
    return value.astimezone(JST).isoformat()


def _count_odds(session: Session, bet_type: str, *, is_closing: bool, label: str) -> SourceCount:
    row = session.execute(
        select(
            func.count(OddsSnapshot.id),
            func.count(func.distinct(OddsSnapshot.race_id)),
            func.min(OddsSnapshot.observed_at),
            func.max(OddsSnapshot.observed_at),
        ).where(OddsSnapshot.bet_type == bet_type, OddsSnapshot.is_closing == is_closing)
    ).one()
    return SourceCount(
        label=label,
        count=int(row[0] or 0),
        distinct_races=int(row[1] or 0),
        since=_iso(row[2]),
        through=_iso(row[3]),
    )


def build_collection_report(session: Session) -> dict:
    """Row counts per live-capture source, so "how much has actually been
    collected" is a fact read off the database rather than a claim."""
    sources = [
        _count_odds(session, "win", is_closing=False, label="締切前オッズ (単勝)"),
        _count_odds(session, "exacta", is_closing=False, label="締切前オッズ (2連単)"),
        _count_odds(session, "quinella", is_closing=False, label="締切前オッズ (2連複)"),
    ]

    bi_row = session.execute(
        select(
            func.count(BeforeInfoEntry.id),
            func.count(func.distinct(BeforeInfoEntry.race_id)),
            func.min(BeforeInfoEntry.observed_at),
            func.max(BeforeInfoEntry.observed_at),
        )
    ).one()
    sources.append(
        SourceCount(
            label="直前情報 (実況値)",
            count=int(bi_row[0] or 0),
            distinct_races=int(bi_row[1] or 0),
            since=_iso(bi_row[2]),
            through=_iso(bi_row[3]),
        )
    )

    for model_version, count in session.execute(
        select(RacePrediction.model_version, func.count(RacePrediction.id)).group_by(
            RacePrediction.model_version
        )
    ).all():
        races, since, through = session.execute(
            select(
                func.count(func.distinct(RacePrediction.race_id)),
                func.min(RacePrediction.predicted_at),
                func.max(RacePrediction.predicted_at),
            ).where(RacePrediction.model_version == model_version)
        ).one()
        sources.append(
            SourceCount(
                label=f"予測 ({model_version})",
                count=int(count),
                distinct_races=int(races or 0),
                since=_iso(since),
                through=_iso(through),
            )
        )

    return {
        "generated_at": _iso(dt.datetime.now(dt.UTC)),
        "sources": [s.to_dict() for s in sources],
    }


def build_roi_report() -> dict:
    """Static, hand-curated backtest figures. See `ROI_BASELINES` and
    `ROI_EV_HYPOTHESIS` module constants for how to update them."""
    return {
        "generated_at": _iso(dt.datetime.now(dt.UTC)),
        "note": (
            "締切時オッズによるバックテスト結果です。実際に賭けられる価格ではなく、"
            "ライブの損益ではありません。投資助言ではありません。"
        ),
        "break_even_roi": 1.0,
        "baselines": ROI_BASELINES,
        "ev_hypothesis": ROI_EV_HYPOTHESIS,
    }


def _model_probabilities(session: Session, race_ids: list, model_version: str | None) -> dict:
    """`{race_id: {lane: probability}}` for one model version.

    Ordered by `predicted_at` and overwritten as it goes, so a later
    prediction for the same `(race, lane)` -- the morning card model
    re-running, or two preview cycles landing in the same window --
    replaces an earlier one rather than duplicating it.
    """
    if not race_ids or not model_version:
        return {}
    latest: dict = {}
    for race_id, lane, prob in session.execute(
        select(
            RacePrediction.race_id,
            RacePrediction.lane_number,
            RacePrediction.win_probability,
        )
        .where(
            RacePrediction.race_id.in_(race_ids),
            RacePrediction.model_version == model_version,
        )
        .order_by(RacePrediction.predicted_at)
    ):
        latest.setdefault(race_id, {})[int(lane)] = float(prob)
    return latest


def _live_odds(session: Session, race_ids: list, bet_type: str) -> dict:
    """`{race_id: [(observed_at, {lane: odds})]}`, ascending by time, from
    live (non-closing) captures only -- the dashboard shows races that
    have not run, so a closing price cannot exist for them yet, and
    querying only live rows keeps that true even if it did."""
    if not race_ids:
        return {}
    by_race: dict = {}
    for race_id, lane, odds, observed_at in session.execute(
        select(
            OddsSnapshot.race_id,
            OddsSnapshot.combination,
            OddsSnapshot.odds,
            OddsSnapshot.observed_at,
        )
        .where(
            OddsSnapshot.race_id.in_(race_ids),
            OddsSnapshot.bet_type == bet_type,
            OddsSnapshot.is_closing.is_(False),
        )
        .order_by(OddsSnapshot.observed_at)
    ):
        # `combination` is a string ("1".."6") because it also holds
        # multi-boat combinations like "1-2" for exacta/quinella; cast to
        # int here so a caller can key off it with the same int lanes
        # `LANES` and the model's probability dict use.
        by_race.setdefault(race_id, {}).setdefault(observed_at, {})[int(lane)] = float(odds)
    return {
        race_id: sorted(readings.items(), key=lambda item: item[0])
        for race_id, readings in by_race.items()
    }


def _before_info_coverage(session: Session, race_ids: list) -> dict:
    """`{race_id: {'extime': n, 'st': n, 'tilt': n, 'course': n,
    'changed_lanes': set, 'parts_seen': bool}}` for the 直前情報 block."""
    if not race_ids:
        return {}
    coverage: dict = {}
    for row in session.execute(
        select(
            BeforeInfoEntry.race_id,
            BeforeInfoEntry.lane_number,
            BeforeInfoEntry.exhibition_time_sec,
            BeforeInfoEntry.start_exhibition_st_sec,
            BeforeInfoEntry.tilt_angle,
            BeforeInfoEntry.start_exhibition_course,
            BeforeInfoEntry.propeller_changed,
            BeforeInfoEntry.parts_replaced,
        ).where(BeforeInfoEntry.race_id.in_(race_ids))
    ):
        entry = coverage.setdefault(
            row.race_id,
            {"extime": 0, "st": 0, "tilt": 0, "course": 0, "changed_lanes": set(), "parts_seen": False},
        )
        if row.exhibition_time_sec is not None:
            entry["extime"] += 1
        if row.start_exhibition_st_sec is not None:
            entry["st"] += 1
        if row.tilt_angle is not None:
            entry["tilt"] += 1
        if row.start_exhibition_course is not None:
            entry["course"] += 1
            if int(row.start_exhibition_course) != int(row.lane_number):
                entry["changed_lanes"].add(int(row.lane_number))
        if row.propeller_changed or row.parts_replaced:
            entry["parts_seen"] = True
    return coverage


def _surface_condition_races(session: Session, race_ids: list) -> set:
    if not race_ids:
        return set()
    return set(
        session.scalars(
            select(RaceSurfaceCondition.race_id).where(
                RaceSurfaceCondition.race_id.in_(race_ids)
            )
        )
    )


def _racer_ids_by_race(session: Session, race_ids: list) -> dict:
    if not race_ids:
        return {}
    result: dict = {}
    for race_id, racer_id in session.execute(
        select(RaceEntry.race_id, RaceEntry.racer_id).where(RaceEntry.race_id.in_(race_ids))
    ):
        result.setdefault(race_id, set()).add(racer_id)
    return result


def _racers_with_period_stats(session: Session, racer_ids: set) -> set:
    """Racer ids with at least one `RacerPeriodStats` row -- a coarse
    presence check, not the per-period leakage-safe join `dataset.py`
    does for training. Fine here: this drives a descriptive "is this data
    source populated at all" flag on a dashboard, not a feature fed to a
    model."""
    if not racer_ids:
        return set()
    return set(
        session.scalars(
            select(RacerPeriodStats.racer_id)
            .where(RacerPeriodStats.racer_id.in_(racer_ids))
            .distinct()
        )
    )


def _racers_with_course_stats(session: Session, racer_ids: set) -> set:
    """Racer ids with at least one `RacerPeriodCourseStats` row, joined
    through its parent -- that table is keyed by
    `racer_period_stats_id`, not `racer_id` directly."""
    if not racer_ids:
        return set()
    return set(
        session.scalars(
            select(RacerPeriodStats.racer_id)
            .join(
                RacerPeriodCourseStats,
                RacerPeriodCourseStats.racer_period_stats_id == RacerPeriodStats.id,
            )
            .where(RacerPeriodStats.racer_id.in_(racer_ids))
            .distinct()
        )
    )


def _active_version_or_none(registry: ModelRegistry, role: str) -> str | None:
    """A role with nothing activated yet is a display gap on the
    dashboard (`data_availability['prediction_card']` etc. simply read
    False), not a reason to fail the whole report."""
    try:
        return registry.get_active(role).version_id
    except ModelRegistryError:
        return None


def build_dashboard(
    session: Session,
    *,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
    now: dt.datetime | None = None,
    horizon_hours: int = 6,
) -> dict:
    """Assemble the full array the PHP template expects, plus the two
    additional report sections.

    Only races within `horizon_hours` of their deadline (and not yet
    past it) are shown, mirroring what `capture_odds`/`predict_daily`
    would actually be acting on right now -- a dashboard showing
    tomorrow's races as "waiting" would just be noise.
    """
    now = now or dt.datetime.now(dt.UTC)
    now = now if now.tzinfo is not None else now.replace(tzinfo=dt.UTC)
    today_jst = now.astimezone(JST).date()
    horizon = now + dt.timedelta(hours=horizon_hours)

    rows = session.execute(
        select(Race, Venue.code, Venue.name)
        .join(Venue, Venue.id == Race.venue_id)
        .where(
            Race.race_date == today_jst,
            Race.status != "cancelled",
            Race.scheduled_deadline_at.is_not(None),
            Race.scheduled_deadline_at > now,
            Race.scheduled_deadline_at <= horizon,
        )
        .order_by(Race.scheduled_deadline_at)
    ).all()

    races_out = []
    venue_rows: dict = {}
    race_objs = [r for r, _code, _name in rows]
    race_ids = [r.id for r in race_objs]

    registry = ModelRegistry(registry_path)
    default_model_version = _active_version_or_none(registry, DEFAULT_ROLE)
    preview_model_version = _active_version_or_none(registry, PREVIEW_ROLE)

    card_probs = _model_probabilities(session, race_ids, default_model_version)
    preview_probs = _model_probabilities(session, race_ids, preview_model_version)
    win_odds_by_race = _live_odds(session, race_ids, "win")
    bi_coverage = _before_info_coverage(session, race_ids)
    surface_races = _surface_condition_races(session, race_ids)
    racers_by_race = _racer_ids_by_race(session, race_ids)
    all_racer_ids: set = set()
    for ids in racers_by_race.values():
        all_racer_ids |= ids
    racers_with_period = _racers_with_period_stats(session, all_racer_ids)
    racers_with_course = _racers_with_course_stats(session, all_racer_ids)

    entries_by_race: dict = {}
    for entry in session.scalars(
        select(RaceEntry).where(RaceEntry.race_id.in_(race_ids))
    ):
        entries_by_race.setdefault(entry.race_id, {})[entry.lane_number] = entry

    for race, venue_code, venue_name in rows:
        entries = entries_by_race.get(race.id, {})
        six_lanes = set(entries) == set(LANES)
        motor_boat_ok = six_lanes and all(
            entries[lane].listed_motor_second_rate is not None
            and entries[lane].listed_boat_second_rate is not None
            for lane in LANES
        )
        racer_ids = racers_by_race.get(race.id, set())
        period_stats_ok = bool(racer_ids) and racer_ids <= racers_with_period
        course_stats_ok = bool(racer_ids) and bool(racer_ids & racers_with_course)

        bi = bi_coverage.get(race.id)
        before_info_ok = bi is not None and bi["extime"] == 6 and bi["tilt"] == 6
        exhibition_time_ok = bi is not None and bi["extime"] == 6
        exhibition_course_ok = bi is not None and bi["course"] == 6
        parts_change_ok = bi is not None and bi["parts_seen"]
        changed_lanes = bi["changed_lanes"] if bi else set()

        latest_odds_reading = None
        odds_history_vals: list[float] = []
        readings = win_odds_by_race.get(race.id, [])
        if readings:
            latest_odds_reading = readings[-1][1]
            # Lane-1 odds over time, for the sparkline -- one number per
            # reading is enough for a trend indicator.
            odds_history_vals = [r[1].get(1) for r in readings[-5:] if 1 in r[1]]

        card_probs_for_race = card_probs.get(race.id, {})
        preview_probs_for_race = preview_probs.get(race.id, {})
        active_probs = preview_probs_for_race or card_probs_for_race
        prediction_source = "preview" if preview_probs_for_race else "card"

        current_odds_ok = latest_odds_reading is not None
        best_lane = None
        model_probability = None
        expected_return = None
        if active_probs and latest_odds_reading:
            best_lane = max(active_probs, key=lambda lane: active_probs[lane])
            model_probability = active_probs[best_lane]
            odds_for_best = latest_odds_reading.get(best_lane)
            if odds_for_best is not None:
                expected_return = round(model_probability * odds_for_best * 100)

        availability = {
            "race_card": six_lanes,
            "scheduled_deadline": race.scheduled_deadline_at is not None,
            "racer_period_stats": period_stats_ok,
            "course_stats": course_stats_ok,
            "motor_boat_stats": motor_boat_ok,
            "prediction_card": bool(card_probs_for_race),
            "before_info": before_info_ok,
            "exhibition_time": exhibition_time_ok,
            "exhibition_course": exhibition_course_ok,
            "surface_conditions": race.id in surface_races and race.race_number > 1,
            "parts_change": parts_change_ok,
            "current_odds": current_odds_ok,
            "prediction_preview": bool(preview_probs_for_race),
        }
        critical_missing = [
            code
            for code, defn in DATA_CATALOG.items()
            if defn["critical"] and not availability.get(code, False)
        ]

        if critical_missing:
            status, label = "waiting", "情報待ち"
        elif expected_return is not None and expected_return >= EV_CANDIDATE_THRESHOLD * 100:
            status, label = "candidate", "検証候補"
        else:
            status, label = "skip", "見送り"

        reasons = []
        if changed_lanes:
            reasons.append(
                {
                    "tone": "positive" if best_lane in changed_lanes else "neutral",
                    "title": "進入変更あり",
                    "detail": f"{sorted(changed_lanes)}号艇が枠なりと異なる",
                }
            )
        if (
            len(odds_history_vals) >= 2
            and odds_history_vals[0]
            and odds_history_vals[-1]
            and odds_history_vals[-1] > odds_history_vals[0]
        ):
            reasons.append(
                {
                    "tone": "positive",
                    "title": "1号艇の価格が上昇",
                    "detail": f"{odds_history_vals[0]:.1f}倍から{odds_history_vals[-1]:.1f}倍",
                }
            )
        if critical_missing:
            missing_labels = [DATA_CATALOG[c]["label"] for c in critical_missing]
            reasons.append(
                {"tone": "risk", "title": "重要データが不足", "detail": "・".join(missing_labels)}
            )
        elif expected_return is not None and expected_return < 100:
            reasons.append(
                {"tone": "risk", "title": "期待払戻が100円未満", "detail": f"現在{expected_return}円"}
            )

        bet_options = []
        if best_lane is not None:
            bet_options.append(
                {"bet_type_code": "win", "bet_type_label": "単勝", "combination": str(best_lane)}
            )

        races_out.append(
            {
                "race_id": str(race.id),
                "venue_code": venue_code,
                "venue_name": venue_name,
                "race_number": race.race_number,
                "scheduled_deadline_at": race.scheduled_deadline_at.astimezone(JST).isoformat(),
                "decision_status": status,
                "decision_label": label,
                "recommended_bet": bet_options[0]
                if bet_options
                else {"bet_type_code": "", "bet_type_label": "—", "combination": "—"},
                "available_bet_options": bet_options,
                "max_stake_yen": PAPER_MAX_STAKE_YEN if status == "candidate" else 0,
                "current_odds": (latest_odds_reading or {}).get(best_lane) if best_lane else None,
                "odds_5_minutes_ago": odds_history_vals[0] if len(odds_history_vals) >= 2 else None,
                "expected_return_per_100_yen": expected_return,
                "model_probability": model_probability,
                "uncertainty_adjusted_probability": model_probability,
                "prediction_source": prediction_source,
                "data_availability": availability,
                "decision_reasons": reasons,
                "odds_history": odds_history_vals,
            }
        )

        venue_entry = venue_rows.setdefault(
            venue_code,
            {
                "venue_code": venue_code,
                "venue_name": venue_name,
                "water_type_label": VENUE_WATER_TYPE.get(venue_code, "—"),
                "remaining_race_count": 0,
                "candidate_count": 0,
                "waiting_count": 0,
                "next_race_id": None,
                "_next_deadline": None,
            },
        )
        venue_entry["remaining_race_count"] += 1
        if status == "candidate":
            venue_entry["candidate_count"] += 1
        elif status == "waiting":
            venue_entry["waiting_count"] += 1
        deadline = race.scheduled_deadline_at
        if venue_entry["_next_deadline"] is None or deadline < venue_entry["_next_deadline"]:
            venue_entry["_next_deadline"] = deadline
            venue_entry["next_race_id"] = str(race.id)

    venues_out = []
    for entry in venue_rows.values():
        entry.pop("_next_deadline")
        venues_out.append(entry)

    return {
        "site": {
            "page_title": "Race Decision Desk — 複数会場ダッシュボード",
            "meta_description": (
                "複数会場の締切、買い目候補、使用上限、オッズ変化、必要データの取得状況を"
                "一画面で比較する紙上投票ダッシュボード。"
            ),
            "brand_name": "Race Decision Desk",
            "brand_subtitle": "Multi-venue decision support",
            "operating_mode_label": "紙上投票モード",
            "model_version": default_model_version or "(未登録)",
            "policy_version": "paper-only-v2",
            "last_updated_at": now.astimezone(JST).isoformat(),
        },
        "risk": {
            "bankroll_yen": PAPER_BANKROLL_YEN,
            "spent_today_yen": 0,
            "daily_limit_yen": PAPER_DAILY_LIMIT_YEN,
            "minimum_stake_yen": PAPER_STAKE_UNIT_YEN,
            "stake_unit_yen": PAPER_STAKE_UNIT_YEN,
            "actual_betting_enabled": False,
            "paper_betting_enabled": True,
        },
        "data_catalog": DATA_CATALOG,
        "venues": venues_out,
        "races": races_out,
        "collection_report": build_collection_report(session),
        "roi_report": build_roi_report(),
    }


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--horizon-hours", type=int, default=6)
    args = parser.parse_args(argv)

    engine = create_db_engine(args.database_url)
    try:
        with create_session_factory(engine)() as session:
            dashboard = build_dashboard(
                session, registry_path=args.registry, horizon_hours=args.horizon_hours
            )
    finally:
        engine.dispose()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(dashboard, handle, ensure_ascii=False, indent=2, default=str)

    print(
        f"wrote {args.output}: races={len(dashboard['races'])} "
        f"venues={len(dashboard['venues'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
