"""Build the JSON `web/dashboard/results.php` reads.

`dashboard_report.py` only shows races that have not yet closed -- races
past their deadline drop out of `build_dashboard`'s query entirely, so
there is nowhere to see whether a prediction was right. This module
answers that: for each race in a recent window, it pairs the model's
pre-race probability (from `race_predictions`, written live -- see that
table's docstring for why it cannot be reconstructed after the fact)
with the actual outcome (`race_results` / `race_result_entries`) and
reports whether the model's top pick actually won.

**Hit rate, not ROI.** This reports whether the model's highest-probability
lane matches the actual winner. It says nothing about payouts, stakes, or
expected value -- that is `dashboard_report.build_roi_report`'s job, built
from `evaluate_p2`'s payout-settled backtest. Conflating the two would be
misleading: a model can have a good hit rate and a poor return (favourites
win often and pay little) or the reverse.

`build_cron_report` answers a different, adjacent question: what is the
live-capture cron pipeline (`.21`'s crontab, not tracked in this repo)
actually collecting right now. It pairs a static, hand-maintained catalog
of the crontab's own jobs with `dashboard_report.build_collection_report`'s
real row counts, so the page shows measured facts, not a description that
could have silently drifted from what cron actually runs.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..model_registry import DEFAULT_ROLE, ModelRegistry, ModelRegistryError
from .dashboard_report import (
    DEFAULT_REGISTRY_PATH,
    _model_probabilities,
    build_collection_report,
)
from .models import (
    RACE_STATUS_CANCELLED,
    LiveRaceResult,
    Race,
    RaceResult,
    RaceResultEntry,
    Venue,
)
from .predict_daily import PREVIEW_ROLE
from .session import create_db_engine, create_session_factory

JST = ZoneInfo("Asia/Tokyo")

DEFAULT_OUTPUT_PATH = Path("data/results-report-snapshot.json")
DEFAULT_DAYS = 8

CRON_JOBS = [
    {
        "schedule": "06:30 毎日",
        "module": "ingest_daily card",
        "label": "当日の出走表 (カード) 取得",
        "kind": None,
    },
    {
        "schedule": "06:45 毎日",
        "module": "predict_daily",
        "label": "出走表時点モデルの予測記録",
        "kind": "card_prediction",
    },
    {
        "schedule": "08-21時, 2分毎",
        "module": "capture_odds --with-exacta",
        "label": "締切前オッズ (単勝・2連単・2連複)",
        "kind": "odds",
    },
    {
        "schedule": "08-21時, 奇数分毎",
        "module": "capture_beforeinfo",
        "label": "直前情報 (展示タイム・チルト・進入変更)",
        "kind": "beforeinfo",
    },
    {
        "schedule": "08-21時, 奇数分毎",
        "module": "predict_daily --role preview",
        "label": "直前情報反映後モデルの予測記録",
        "kind": "preview_prediction",
    },
    {
        "schedule": "02:00 毎日",
        "module": "ingest_daily results",
        "label": "前日レース結果の取得・確定",
        "kind": None,
    },
]
"""Transcribed from the live crontab on `.21` (`.claude/rules/11-runtime-host.md`
/ `tasks/CURRENT.md`). Public, static, and not read from any table -- like
`dashboard_report.VENUE_WATER_TYPE`, it lives here as a constant rather than
forcing a DB round-trip for a fact nothing else in the project needs.
Update by hand if the crontab changes."""


def _iso(value: dt.datetime | None) -> str | None:
    if value is None:
        return None
    value = value if value.tzinfo is not None else value.replace(tzinfo=dt.UTC)
    return value.astimezone(JST).isoformat()


def _active_version_or_none(registry: ModelRegistry, role: str) -> str | None:
    try:
        return registry.get_active(role).version_id
    except ModelRegistryError:
        return None


def _actual_results(session: Session, race_ids: list) -> dict:
    """`{race_id: {lane: finish_position or None}}` for races with a
    result. A race absent from this dict has no result yet at all.

    Two sources, and the order matters. The K-file (`race_results`) is
    authoritative but arrives at 02:00 the next day, so on its own this
    report showed every one of today's races as `pending` until the
    following morning -- a prediction could not be checked against its own
    race on the day it was made. `live_race_results` is captured from the
    official page minutes after each race and fills that gap.

    The K-file wins wherever both exist: it is the archive, and the live
    capture is there for timing rather than for truth.
    """
    if not race_ids:
        return {}
    by_race: dict = {}

    live = session.execute(
        select(
            LiveRaceResult.race_id,
            LiveRaceResult.lane_number,
            LiveRaceResult.finish_position,
        ).where(LiveRaceResult.race_id.in_(race_ids))
    ).all()
    for race_id, lane, finish_position in live:
        by_race.setdefault(race_id, {})[int(lane)] = (
            int(finish_position) if finish_position is not None else None
        )

    rows = session.execute(
        select(
            RaceResult.race_id,
            RaceResultEntry.lane_number,
            RaceResultEntry.finish_position,
        ).join(RaceResultEntry, RaceResultEntry.race_result_id == RaceResult.id)
        .where(RaceResult.race_id.in_(race_ids))
    ).all()
    archived: dict = {}
    for race_id, lane, finish_position in rows:
        archived.setdefault(race_id, {})[int(lane)] = (
            int(finish_position) if finish_position is not None else None
        )
    by_race.update(archived)
    return by_race


def _race_state(race: Race, entries: dict | None, *, now: dt.datetime) -> str:
    if race.status == RACE_STATUS_CANCELLED:
        return "cancelled"
    if entries is None:
        deadline = race.scheduled_deadline_at
        # SQLite (used in tests) does not round-trip tzinfo on
        # DateTime(timezone=True); assume UTC on read-back, matching
        # `_iso` above and `dashboard_report._iso`.
        if deadline is not None and deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=dt.UTC)
        return "pending" if deadline and deadline < now else "upcoming"
    winners = [lane for lane, pos in entries.items() if pos == 1]
    return "finished" if winners else "void"


def _build_race_row(
    race: Race,
    venue_code: str,
    venue_name: str,
    *,
    card_probs: dict,
    preview_probs: dict,
    result_entries: dict | None,
    now: dt.datetime,
) -> dict:
    state = _race_state(race, result_entries, now=now)
    winner_lanes = (
        sorted(lane for lane, pos in result_entries.items() if pos == 1)
        if result_entries
        else []
    )

    def _prediction(probs: dict) -> dict:
        if not probs:
            return {"top_lane": None, "probability": None}
        top_lane = max(probs, key=lambda lane: probs[lane])
        return {"top_lane": top_lane, "probability": probs[top_lane]}

    card_pred = _prediction(card_probs)
    preview_pred = _prediction(preview_probs)

    def _hit(pred: dict) -> bool | None:
        if state != "finished" or pred["top_lane"] is None:
            return None
        return pred["top_lane"] in winner_lanes

    return {
        "race_id": str(race.id),
        "venue_code": venue_code,
        "venue_name": venue_name,
        "race_number": race.race_number,
        "scheduled_deadline_at": _iso(race.scheduled_deadline_at),
        "race_state": state,
        "winner_lanes": winner_lanes,
        "card_prediction": card_pred,
        "preview_prediction": preview_pred,
        "card_hit": _hit(card_pred),
        "preview_hit": _hit(preview_pred),
    }


def _summarize(races: list) -> dict:
    summary = {
        "races_total": len(races),
        "finished": sum(1 for r in races if r["race_state"] == "finished"),
        "cancelled": sum(1 for r in races if r["race_state"] == "cancelled"),
        "void": sum(1 for r in races if r["race_state"] == "void"),
        "pending": sum(1 for r in races if r["race_state"] == "pending"),
        "upcoming": sum(1 for r in races if r["race_state"] == "upcoming"),
    }
    for key in ("card", "preview"):
        hits = [r[f"{key}_hit"] for r in races if r[f"{key}_hit"] is not None]
        decided = len(hits)
        made = sum(1 for h in hits if h)
        summary[f"{key}_decided"] = decided
        summary[f"{key}_hits"] = made
        summary[f"{key}_hit_rate"] = round(made / decided, 4) if decided else None
    return summary


def build_results_report(
    session: Session,
    *,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
    now: dt.datetime | None = None,
    days: int = DEFAULT_DAYS,
) -> dict:
    """Recent-window prediction-vs-result report, grouped by race date
    (newest first). `days` includes today, so `days=8` covers today and
    the previous seven days."""
    now = now or dt.datetime.now(dt.UTC)
    now = now if now.tzinfo is not None else now.replace(tzinfo=dt.UTC)
    today_jst = now.astimezone(JST).date()
    date_list = [today_jst - dt.timedelta(days=offset) for offset in range(days)]

    rows = session.execute(
        select(Race, Venue.code, Venue.name)
        .join(Venue, Venue.id == Race.venue_id)
        .where(Race.race_date.in_(date_list))
        .order_by(Race.race_date.desc(), Venue.code, Race.race_number)
    ).all()

    race_objs = [r for r, _code, _name in rows]
    race_ids = [r.id for r in race_objs]

    registry = ModelRegistry(registry_path)
    default_model_version = _active_version_or_none(registry, DEFAULT_ROLE)
    preview_model_version = _active_version_or_none(registry, PREVIEW_ROLE)

    card_probs_by_race = _model_probabilities(session, race_ids, default_model_version)
    preview_probs_by_race = _model_probabilities(session, race_ids, preview_model_version)
    results_by_race = _actual_results(session, race_ids)

    races_by_date: dict = {d.isoformat(): [] for d in date_list}
    for race, venue_code, venue_name in rows:
        key = race.race_date.isoformat()
        races_by_date[key].append(
            _build_race_row(
                race,
                venue_code,
                venue_name,
                card_probs=card_probs_by_race.get(race.id, {}),
                preview_probs=preview_probs_by_race.get(race.id, {}),
                result_entries=results_by_race.get(race.id),
                now=now,
            )
        )

    dates = [d.isoformat() for d in date_list]
    summary_by_date = {d: _summarize(races_by_date[d]) for d in dates}
    all_races = [race for d in dates for race in races_by_date[d]]

    return {
        "generated_at": _iso(now),
        "dates": dates,
        "default_date": dates[0] if dates else None,
        "card_model_version": default_model_version,
        "preview_model_version": preview_model_version,
        "overall_summary": _summarize(all_races),
        "summary_by_date": summary_by_date,
        "races_by_date": races_by_date,
        "cron_report": build_cron_report(session, registry_path=registry_path),
    }


def _merge_sources(matches: list) -> dict | None:
    """Combine same-job sources that `build_collection_report` reports
    separately (capture_odds writes three bet types in one run). `count`
    sums (each row is independent); `distinct_races` takes the max rather
    than summing, since the same race can appear under more than one bet
    type; `since`/`through` widen to the full span across the matches."""
    if not matches:
        return None
    sinces = [m["since"] for m in matches if m["since"]]
    throughs = [m["through"] for m in matches if m["through"]]
    return {
        "count": sum(m["count"] for m in matches),
        "distinct_races": max(m["distinct_races"] for m in matches),
        "since": min(sinces) if sinces else None,
        "through": max(throughs) if throughs else None,
    }


def build_cron_report(session: Session, *, registry_path: Path = DEFAULT_REGISTRY_PATH) -> dict:
    """Cross-reference the static crontab catalog (`CRON_JOBS`) with the
    real row counts `dashboard_report.build_collection_report` already
    computes, so the page shows what cron is *actually* collecting, not
    just what it is supposed to.

    The two `predict_daily` jobs both write rows labelled `"予測 (<model
    version>)"` -- matching by a shared prefix would silently attribute
    the card model's count to the preview job (or vice versa) whenever
    both have run, so each is matched by its role's *exact* active
    version label instead."""
    collection = build_collection_report(session)
    sources = collection["sources"]

    registry = ModelRegistry(registry_path)
    default_model_version = _active_version_or_none(registry, DEFAULT_ROLE)
    preview_model_version = _active_version_or_none(registry, PREVIEW_ROLE)

    def _matches(kind: str | None) -> list:
        if kind == "odds":
            return [s for s in sources if s["label"].startswith("締切前オッズ")]
        if kind == "beforeinfo":
            return [s for s in sources if s["label"].startswith("直前情報")]
        if kind == "card_prediction" and default_model_version:
            return [s for s in sources if s["label"] == f"予測 ({default_model_version})"]
        if kind == "preview_prediction" and preview_model_version:
            return [s for s in sources if s["label"] == f"予測 ({preview_model_version})"]
        return []

    jobs = []
    for job in CRON_JOBS:
        merged = _merge_sources(_matches(job["kind"]))
        jobs.append(
            {
                "schedule": job["schedule"],
                "module": job["module"],
                "label": job["label"],
                "count": merged["count"] if merged else None,
                "distinct_races": merged["distinct_races"] if merged else None,
                "since": merged["since"] if merged else None,
                "through": merged["through"] if merged else None,
            }
        )

    return {"generated_at": collection["generated_at"], "jobs": jobs}


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    args = parser.parse_args(argv)

    engine = create_db_engine(args.database_url)
    try:
        with create_session_factory(engine)() as session:
            report = build_results_report(session, registry_path=args.registry, days=args.days)
    finally:
        engine.dispose()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, default=str)

    print(
        f"wrote {args.output}: dates={len(report['dates'])} "
        f"races={report['overall_summary']['races_total']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
