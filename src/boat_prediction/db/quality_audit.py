"""Measure the five data-quality axes against the loaded database.

`quality.score_report` decides whether ML may proceed, but it takes the
axis scores as input -- something has to measure them. Until now nothing
did: P0 was validated on synthetic fixtures, where the answer was known
in advance. This runs the checks against real loaded rows.

Each check reports how many rows it examined and how many were
defective, so a score is never a bare number: an axis score is the
weight times the mean pass rate of its checks, and every check that cost
points can be pointed at. A check that examines nothing (no odds loaded
yet, say) is skipped rather than scored 0 -- absent data is a coverage
question, not a defect, and scoring it as failure would quietly block ML
on a dataset that is merely incomplete.

The point_in_time axis carries the most weight (25) for the reason
`docs/PROJECT_PROFILE.md` puts `available_at <= prediction_at` among the
non-negotiable constraints: every other axis describes data that is
wrong, while this one describes data that is *unusable* -- a feature
that was not knowable at decision time inflates every downstream metric
and cannot be detected later by looking at the model.

Cancelled races are excluded wherever they would look like missing data.
They are a real outcome, not a gap: 11,775 of them in the 2005-2026
archive have no result by definition.
"""

from __future__ import annotations

import argparse
import datetime as dt
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..quality import AXIS_WEIGHTS, QualityReport, score_report
from .session import create_db_engine, create_session_factory


@dataclass(frozen=True)
class Check:
    """One measurement: `defects` of `examined` rows were bad."""

    axis: str
    name: str
    examined: int
    defects: int
    detail: str

    @property
    def skipped(self) -> bool:
        return self.examined == 0

    @property
    def pass_rate(self) -> float:
        return 1.0 if self.skipped else 1.0 - self.defects / self.examined

    def render(self) -> str:
        if self.skipped:
            return f"  [skip] {self.axis:<14} {self.name}: nothing to examine"
        mark = "ok  " if self.defects == 0 else "FAIL"
        return (
            f"  [{mark}] {self.axis:<14} {self.name}: "
            f"{self.defects}/{self.examined} defective "
            f"({100 * self.pass_rate:.3f}% pass) -- {self.detail}"
        )


# Each entry is (axis, name, detail, examined_sql, defects_sql). Keeping
# the two counts as separate scalar queries rather than one FILTER query
# keeps every check readable on its own and lets a reviewer run either
# half by hand against the database.
_CHECKS: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "completeness",
        "races_have_six_entries",
        "a race card lists exactly six boats",
        "SELECT count(*) FROM races WHERE status <> 'cancelled'",
        """SELECT count(*) FROM races r WHERE r.status <> 'cancelled'
           AND (SELECT count(*) FROM race_entries e WHERE e.race_id = r.id) <> 6""",
    ),
    (
        "completeness",
        "finished_races_have_a_result",
        "a race that ran produced a result row",
        "SELECT count(*) FROM races WHERE status = 'finished'",
        """SELECT count(*) FROM races r WHERE r.status = 'finished'
           AND NOT EXISTS (SELECT 1 FROM race_results x WHERE x.race_id = r.id)""",
    ),
    (
        "completeness",
        "entries_have_a_national_win_rate",
        "the strongest single B-file feature is present",
        "SELECT count(*) FROM race_entries",
        "SELECT count(*) FROM race_entries WHERE listed_national_win_rate IS NULL",
    ),
    (
        "completeness",
        "races_have_a_deadline",
        "without it no leakage check is possible below day granularity",
        "SELECT count(*) FROM races WHERE status <> 'cancelled'",
        """SELECT count(*) FROM races
           WHERE status <> 'cancelled' AND scheduled_deadline_at IS NULL""",
    ),
    (
        "uniqueness",
        "one_entry_per_lane",
        "no lane appears twice on one card",
        "SELECT count(*) FROM race_entries",
        """SELECT coalesce(sum(n - 1), 0) FROM (
             SELECT count(*) AS n FROM race_entries GROUP BY race_id, lane_number
           ) d WHERE n > 1""",
    ),
    (
        "uniqueness",
        "one_result_per_race",
        "no race carries two result rows",
        "SELECT count(*) FROM race_results",
        """SELECT coalesce(sum(n - 1), 0) FROM (
             SELECT count(*) AS n FROM race_results GROUP BY race_id
           ) d WHERE n > 1""",
    ),
    (
        "uniqueness",
        "one_result_entry_per_lane",
        "no lane appears twice in one result",
        "SELECT count(*) FROM race_result_entries",
        """SELECT coalesce(sum(n - 1), 0) FROM (
             SELECT count(*) AS n FROM race_result_entries
             GROUP BY race_result_id, lane_number
           ) d WHERE n > 1""",
    ),
    (
        "validity",
        "lane_numbers_in_range",
        "lane is 1-6",
        "SELECT count(*) FROM race_entries",
        "SELECT count(*) FROM race_entries WHERE lane_number NOT BETWEEN 1 AND 6",
    ),
    (
        "validity",
        "finish_positions_in_range",
        "a numeric finish is 1-6; a non-finish is NULL with a status code",
        "SELECT count(*) FROM race_result_entries",
        """SELECT count(*) FROM race_result_entries
           WHERE (finish_position IS NOT NULL AND finish_position NOT BETWEEN 1 AND 6)
              OR (finish_position IS NULL AND status IS NULL)""",
    ),
    (
        "validity",
        "win_rates_in_range",
        "a national win rate is a 0-10 score",
        "SELECT count(*) FROM race_entries WHERE listed_national_win_rate IS NOT NULL",
        """SELECT count(*) FROM race_entries
           WHERE listed_national_win_rate IS NOT NULL
             AND listed_national_win_rate NOT BETWEEN 0 AND 10""",
    ),
    (
        "validity",
        "odds_are_quotable",
        (
            "odds include the stake, so 1.00 is the floor; a stored 0 would "
            "become an infinite implied probability, not a missing one"
        ),
        "SELECT count(*) FROM odds_snapshots",
        "SELECT count(*) FROM odds_snapshots WHERE odds < 1.0",
    ),
    (
        "consistency",
        "a_race_that_produced_placings_has_a_first",
        (
            "not 'exactly one winner': 同着 is real (16 races have two boats "
            "on finish_position 1) and so is a void race where every boat "
            "carries a status code and none a placing (132 races, mostly F). "
            "The defect is placings that skip first place"
        ),
        """SELECT count(*) FROM race_results res
           JOIN races r ON r.id = res.race_id
           WHERE r.status = 'finished'
             AND EXISTS (SELECT 1 FROM race_result_entries e
                         WHERE e.race_result_id = res.id
                           AND e.finish_position IS NOT NULL)""",
        """SELECT count(*) FROM race_results res
           JOIN races r ON r.id = res.race_id
           WHERE r.status = 'finished'
             AND EXISTS (SELECT 1 FROM race_result_entries e
                         WHERE e.race_result_id = res.id
                           AND e.finish_position IS NOT NULL)
             AND NOT EXISTS (SELECT 1 FROM race_result_entries e
                             WHERE e.race_result_id = res.id
                               AND e.finish_position = 1)""",
    ),
    (
        "consistency",
        "result_lanes_match_the_card",
        "every lane in a result was on that race's card",
        """SELECT count(*) FROM race_result_entries e
           JOIN race_results res ON res.id = e.race_result_id
           WHERE EXISTS (SELECT 1 FROM race_entries c WHERE c.race_id = res.race_id)""",
        """SELECT count(*) FROM race_result_entries e
           JOIN race_results res ON res.id = e.race_result_id
           WHERE EXISTS (SELECT 1 FROM race_entries c WHERE c.race_id = res.race_id)
             AND NOT EXISTS (
               SELECT 1 FROM race_entries c
               WHERE c.race_id = res.race_id AND c.lane_number = e.lane_number)""",
    ),
    (
        "consistency",
        "races_belong_to_their_venue_meeting",
        "a race and its meeting agree on the venue",
        "SELECT count(*) FROM races WHERE meeting_id IS NOT NULL",
        """SELECT count(*) FROM races r JOIN race_meetings m ON m.id = r.meeting_id
           WHERE m.venue_id <> r.venue_id""",
    ),
    (
        "consistency",
        "meetings_span_a_plausible_series",
        "a 節 runs at most 9 racing days; more means two were merged",
        "SELECT count(*) FROM race_meetings",
        """SELECT count(*) FROM (
             SELECT m.id FROM race_meetings m JOIN races r ON r.meeting_id = m.id
             GROUP BY m.id HAVING count(DISTINCT r.race_date) > 9
           ) d""",
    ),
    (
        "point_in_time",
        "card_features_precede_the_deadline",
        "a pre-race feature was knowable before betting closed",
        """SELECT count(*) FROM race_entries e JOIN races r ON r.id = e.race_id
           WHERE r.scheduled_deadline_at IS NOT NULL""",
        """SELECT count(*) FROM race_entries e JOIN races r ON r.id = e.race_id
           WHERE r.scheduled_deadline_at IS NOT NULL
             AND e.available_at > r.scheduled_deadline_at""",
    ),
    (
        "point_in_time",
        "results_do_not_precede_their_race",
        "a result is never available before the race day ends",
        "SELECT count(*) FROM race_results res JOIN races r ON r.id = res.race_id",
        """SELECT count(*) FROM race_results res JOIN races r ON r.id = res.race_id
           WHERE res.available_at <= r.scheduled_deadline_at""",
    ),
    (
        "point_in_time",
        "odds_available_at_matches_observed_at",
        "an odds reading is available exactly when it was observed",
        "SELECT count(*) FROM odds_snapshots",
        "SELECT count(*) FROM odds_snapshots WHERE available_at <> observed_at",
    ),
    (
        "point_in_time",
        "weather_is_not_available_before_its_day",
        (
            "a daily summary never predates the day it summarizes "
            "(the loader puts it at the following midnight JST; this is the "
            "portable half of that, and the direction leakage would take)"
        ),
        "SELECT count(*) FROM weather_observations",
        "SELECT count(*) FROM weather_observations WHERE available_at < weather_date",
    ),
)


def run_checks(session: Session) -> list[Check]:
    """Run every check and return its measurement."""
    checks = []
    for axis, name, detail, examined_sql, defects_sql in _CHECKS:
        examined = session.scalar(text(examined_sql)) or 0
        defects = (session.scalar(text(defects_sql)) or 0) if examined else 0
        checks.append(
            Check(axis=axis, name=name, examined=examined, defects=int(defects), detail=detail)
        )
    return checks


def score_axes(checks: list[Check]) -> dict[str, float]:
    """Axis score = its weight x the mean pass rate of its checks.

    The mean, not a row-weighted average: a check over 7 million entry
    rows would otherwise drown out one over 17,852 meetings, and a
    systematic defect in the smaller table is not less serious for being
    smaller. Skipped checks do not count toward the mean; an axis with
    nothing to measure scores full marks and says so in the report.
    """
    scores = {}
    for axis, weight in AXIS_WEIGHTS.items():
        rates = [c.pass_rate for c in checks if c.axis == axis and not c.skipped]
        scores[axis] = float(weight) * (sum(rates) / len(rates) if rates else 1.0)
    return scores


def audit(session: Session) -> tuple[QualityReport, list[Check]]:
    checks = run_checks(session)
    return score_report(score_axes(checks)), checks


def render(report: QualityReport, checks: list[Check]) -> str:
    lines = [f"data quality audit  {dt.datetime.now(dt.UTC).isoformat(timespec='seconds')}", ""]
    for axis in AXIS_WEIGHTS:
        lines.append(f"{axis} ({report.axis_scores[axis]:.2f} / {AXIS_WEIGHTS[axis]})")
        lines.extend(c.render() for c in checks if c.axis == axis)
        lines.append("")
    lines.append(f"total={report.total_score:.2f}  verdict={report.verdict}")
    return "\n".join(lines)


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=None)
    parser.add_argument(
        "--fail-under",
        type=float,
        default=None,
        help="exit 1 when the total score is below this",
    )
    args = parser.parse_args(argv)

    engine = create_db_engine(args.database_url)
    try:
        with create_session_factory(engine)() as session:
            report, checks = audit(session)
    finally:
        engine.dispose()

    print(render(report, checks))
    if args.fail_under is not None and report.total_score < args.fail_under:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
