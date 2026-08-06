"""Build a leakage-safe first-place dataset out of the database.

P1's machinery (`walk_forward`, `model_comparison`, `metrics`) takes
`(X, y, dates)`; nothing produced them from real rows, which is why P1
was only ever exercised on fixtures. This is that step.

One row per race, six lanes wide. The target is the winning lane, so the
problem is the 6-class one `docs/PROJECT_PROFILE.md` sets for P1 -- not
per-boat binary, which would model six correlated outcomes as if they
were independent.

Leakage
-------

Every card feature comes from `race_entries` (the B-file), and each
row's `available_at` is checked against its race's
`scheduled_deadline_at` rather than assumed: the audit says the whole
database satisfies it today, but a dataset builder that trusts that is
one loader change away from silently training on the future. A race with
any entry available too late is dropped and counted.

The within-meeting form features below need the same discipline in a
subtler place: they are built from *other races' results*, and a result
is only available the day after its race (`loader.results_available_at`
-- K-file carries no per-race confirmation time). Grouping by
`race_date < this race's date` rather than `<=` is what keeps a same-day
earlier race's result out of a later race's features on the same day;
using `<=` would silently leak race 3's result into race 4's prediction.

Rows the target cannot describe
-------------------------------

- A dead heat (two boats on `finish_position=1`, 16 in the archive) has
  no single winning lane. Excluded, not resolved arbitrarily.
- A void race (every boat carrying a status code, none a placing, 132 in
  the archive) has no winner at all.
- A card without exactly six lanes cannot fill a fixed-width row.

Each exclusion is counted in `DatasetStats` so a shrinking dataset is
visible rather than silent.

`y_second` is carried beside `y` for the combination bets (複勝/2連単/
2連複), and is the one target here that may be `None` rather than an
exclusion: a race can produce a clean winner and a 同着 for second. The
first-place dataset must not shrink to accommodate that, so the row
stays and the second-place label is absent.

Within-meeting form
--------------------

The motor and boat are drawn once per 節 and a racer keeps them for the
whole series, so how that racer has been placing *earlier in this same
meeting* is direct evidence about the actual equipment in this race --
something the B-file's season-long `listed_*_second_rate` columns can
only reflect with a lag. This was not computable until the 節 grouping
fix (`meeting_resolution.py`): before it, 3% of 節 fragmented across a
順延 boundary, silently truncating a racer's in-series history there.

Tracked by `racer_id`, not lane number: a racer keeps the same lane
across a 節's heats, but 準優勝戦/優勝戦 reseed by standing, so following
the lane would attribute a different racer's form to this one after a
reseed.

A non-finish (any status code, not just a numeric placing) scores as the
worst outcome (0.0), not as missing: a boat that could not finish is
worse evidence than one that finished last, and treating it as absent
would selectively delete the bad news. A day this meeting was cancelled
naturally contributes nothing (no result row exists to score), with no
special-case needed.

Shrunk toward the racer's own season win rate with weight
`MEETING_FORM_SHRINKAGE_STARTS`: on 第1日 there is no in-meeting history
at all, and even by day 3 the sample is only 2-3 races, too few to trust
on its own. The shrinkage constant is therefore never absent -- a race
with zero prior starts still gets a defined feature, equal to the season
prior.

直前情報
--------

Optional, behind `include_before_info`, because it splits the data in
two: `before_info_entries` starts on 2023-05-01 and a race before that
has no block at all. A single always-on block would silently shrink the
trainable window by two and a half years, so the base feature set stays
as it was and this is a second, narrower one.

Four per-lane values, the block measured on 2026-08-01 (tasks/HANDOFF.md
-- +1.43% log-loss over the base features, winning 26 of 26 folds):

- `exhibition_time_z` -- 展示タイム, z-scored within the race. The largest
  single component by more than half, and the only *absolute* measure of
  boat speed anywhere in the schema: everything else derives from a
  finishing position, which is relative to the field and therefore blind
  to anything common to all six crews. Lower is faster; the sign is left
  raw rather than negated, since a linear model learns it either way.
- `start_st_z` -- 展示ST, z-scored within the race. The weakest component
  (+0.155%), kept because the components measured as very nearly
  additive.
- `tilt_angle` -- raw. Weakest of all and not established (22 of 26
  folds); kept for the same additivity reason, not because it is proven.
- `course_changed` -- 進入変更, the course actually taken differing from
  the lane. Fires on 8.16% of boat rows and is the second-largest
  component; nothing else in the schema can see it.

**Z-scored within the race, and that is the point of the transform.** In
a six-class problem a quantity shared by all six boats cannot change who
wins, and the absolute level of an exhibition time is dominated by
shared terms -- the venue, the day's water, how hard the field is
pushing. Removing them leaves the only discriminating part.

Availability is the fetch itself for a live capture and the race's own
deadline for the backfill (`load_beforeinfo_archive`), so the same
`available_at <= scheduled_deadline_at` check the card features get
applies unchanged and is not a special case.

A race is dropped unless all six lanes carry 展示タイム, tilt and 進入
course -- 99.998% of races with any 直前情報 do. 展示ST is the exception:
2.6% of races are missing at least one, and a lane without it takes the
field mean (z = 0) rather than costing the whole race, which is the
convention the ST-proxy measurement already used.

Series phase
------------

`race_phase.is_standing_seeded()` is appended once per race (not once per
lane): 準優勝戦/優勝戦 assign lanes by 点率 standing while 予選 does not,
so the same lane means something different in the two. A single shared
column with per-class weights lets a multinomial model learn that
difference itself (lane 1's coefficient on this column can differ from
lane 6's), rather than the difference being encoded by hand.

Scale
-----

1.15 M races x 67 float features do not fit in plain Python lists, so
`build_dataset` takes a date range and is meant to be pointed at a
window. `docs/PROJECT_PROFILE.md` puts array libraries behind a "when
justified by dataset size" gate; a recent window stays under it.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..race_phase import classify_race_phase, is_standing_seeded

LANES = (1, 2, 3, 4, 5, 6)

# Per-lane B-file card fields, in the order they occupy each lane's slice
# of the feature row. All are printed on the card before the race.
FEATURE_NAMES = (
    "national_win_rate",
    "national_second_rate",
    "local_win_rate",
    "local_second_rate",
    "motor_second_rate",
    "boat_second_rate",
    "age",
    "weight",
    "class_rank",
    "meeting_starts",
    "meeting_form_score",
)

# The 直前情報 block, appended to each lane's slice after the card fields
# when `include_before_info` is set. See the module docstring for what
# each one is and why the block is optional.
BEFORE_INFO_FEATURE_NAMES = (
    "exhibition_time_z",
    "start_st_z",
    "tilt_angle",
    "course_changed",
)

# Per-course racer ability, from `racer_period_course_stats`, appended
# after the 直前情報 block when `include_racer_stats` is set.
RACER_STATS_FEATURE_NAMES = (
    "course_win_shrunk",
    "course_starts",
)

GLOBAL_FEATURE_NAMES = ("is_standing_seeded",)

# Appended once per race (not per lane) alongside `is_standing_seeded`
# when `include_racer_stats` is set. Both are non-linear functions of the
# card, which is the only reason they are worth materialising: a
# multinomial logit already holds all six `national_win_rate` columns, so
# anything it could form as a *linear* combination of them -- "this
# racer minus the mean of the others", for one -- it already has.
RACER_STATS_GLOBAL_NAMES = (
    "field_a1_count",
    "field_win_rate_sd",
)

COURSE_BASE_WIN_RATE = {
    1: 0.5343, 2: 0.1482, 3: 0.1277, 4: 0.1127, 5: 0.0605, 6: 0.0203,
}
"""Win rate by 進入 course across all 225,111 populated
`racer_period_course_stats` rows (2026-08-03). The prior a racer's own
per-course record is shrunk toward."""

COURSE_SHRINKAGE_STARTS = {
    1: 7.7, 2: 27.0, 3: 28.0, 4: 31.0, 5: 45.6, 6: 48.0,
}
"""Empirical-Bayes shrinkage weight per course, in equivalent starts,
measured by the beta-binomial method of moments over the same rows.

Not a tuned constant -- it falls out of how much of the observed spread
in per-course rates is real. A `(racer, period, course)` row averages
**16.8 starts**, so at course 1 (53.4% base) 68.0% of the spread is
signal and k is small, while at course 6 (2.0% base) only 26.2% is and
k is nearly three times the sample size. Without this a racer with 0
wins from 5 starts hands the model a literal zero.

The measurement also showed shrinkage barely moves a *correlation*
(0.6760 → 0.6717 at course 1), because correlation is scale-free and the
start counts cluster tightly. It is the probability *level* this
protects, which is what a model consumes.
"""

# A1 > A2 > B1 > B2 is an ordered grade, so it is encoded as an ordinal
# rather than one-hot: the order is the information.
_CLASS_RANK = {"A1": 4.0, "A2": 3.0, "B1": 2.0, "B2": 1.0}

MEETING_FORM_SHRINKAGE_STARTS = 3.0
"""Weight, in equivalent within-meeting starts, given to the season win
rate prior. Chosen because a 節 is at most 9 racing days
(quality_audit's `meetings_span_a_plausible_series`), so a racer is
rarely more than a handful of starts into it -- 3 keeps the season prior
dominant on 第1日-第3日 and lets in-meeting evidence take over by roughly
the semifinal, without it ever being an arbitrary large number tuned to
a single window."""

MEETING_WINDOW_MARGIN_DAYS = 10
"""How far before `start_date` the within-meeting-form query looks for a
節's earlier days. A 節 spans at most 9 racing days (see above), so 10
covers any meeting that started before the requested window without
scanning the full archive."""


@dataclass
class DatasetStats:
    races_considered: int = 0
    races_used: int = 0
    dropped_not_six_lanes: int = 0
    dropped_no_single_winner: int = 0
    dropped_missing_feature: int = 0
    dropped_late_feature: int = 0
    dropped_missing_before_info: int = 0
    excluded_dates: list[dt.date] = field(default_factory=list)

    def __str__(self) -> str:
        return (
            f"races_considered={self.races_considered} races_used={self.races_used} "
            f"dropped_not_six_lanes={self.dropped_not_six_lanes} "
            f"dropped_no_single_winner={self.dropped_no_single_winner} "
            f"dropped_missing_feature={self.dropped_missing_feature} "
            f"dropped_late_feature={self.dropped_late_feature} "
            f"dropped_missing_before_info={self.dropped_missing_before_info}"
        )


@dataclass
class Dataset:
    X: list[list[float]]
    y: list[int]
    dates: list[dt.date]
    phases: list[str]
    feature_names: list[str]
    stats: DatasetStats
    race_ids: list = field(default_factory=list)
    """Row-aligned with `X`. Carried so an evaluation can settle a
    prediction at that race's real payout, or join it to the market --
    neither of which is possible from `(X, y, dates)` alone, which is why
    every ROI figure before `evaluate_p2` came from a throwaway script."""

    y_second: list[int | None] = field(default_factory=list)
    """Second-place lane, row-aligned with `y`, or None where the race
    produced no single second (a dead heat for 2nd, or a race that
    settled a winner but no runner-up).

    Deliberately nullable rather than an exclusion criterion: `y` is the
    P1 target and dropping a race here would silently shrink the
    first-place dataset to suit a downstream model. A consumer that needs
    a clean pair filters on its own -- `combination_model` does -- and the
    P1 numbers already published stay reproducible."""

    def __len__(self) -> int:
        return len(self.y)


def feature_columns(
    *, include_before_info: bool = False, include_racer_stats: bool = False
) -> list[str]:
    names = FEATURE_NAMES
    if include_before_info:
        names += BEFORE_INFO_FEATURE_NAMES
    if include_racer_stats:
        names += RACER_STATS_FEATURE_NAMES
    columns = [f"lane{lane}_{name}" for lane in LANES for name in names]
    columns.extend(GLOBAL_FEATURE_NAMES)
    if include_racer_stats:
        columns.extend(RACER_STATS_GLOBAL_NAMES)
    return columns


_MEETING_CTE = """
WITH meeting_window AS (
    SELECT e.race_id AS mw_race_id,
           e.lane_number AS mw_lane_number,
           r.meeting_id AS mw_meeting_id,
           e.racer_id AS mw_racer_id,
           r.race_date AS mw_race_date,
           CASE
               WHEN rre.finish_position IS NOT NULL THEN (7.0 - rre.finish_position) / 6.0
               WHEN rre.id IS NOT NULL THEN 0.0
               ELSE NULL
           END AS score
      FROM race_entries e
      JOIN races r ON r.id = e.race_id
      LEFT JOIN race_results res ON res.race_id = r.id
      LEFT JOIN race_result_entries rre
             ON rre.race_result_id = res.id AND rre.lane_number = e.lane_number
     WHERE r.meeting_id IS NOT NULL
       AND r.race_date >= :meeting_window_start
       AND r.race_date <= :end_date
),
meeting_form AS (
    SELECT mw_race_id,
           mw_lane_number,
           COUNT(score) OVER w AS prior_starts,
           AVG(score) OVER w AS prior_avg_score
      FROM meeting_window
    WINDOW w AS (
        PARTITION BY mw_meeting_id, mw_racer_id
        ORDER BY mw_race_date
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
    )
)
"""

# Bounded by the same window the meeting CTE already binds rather than a
# parameter of its own: it is 10 days wider than needed, which costs a few
# unmatched rows and saves a bind that both call sites would have to keep
# in step.
#
# `bi_rank` exists because the table's key is
# `(race_id, lane_number, observed_at)` -- a second capture for the same
# race is possible in the schema even though `load_before_info` refuses
# to write one. Without it a duplicate would multiply rows and the lane
# loop would silently keep whichever arrived last; with it the earliest
# observation wins, deterministically.
_BEFORE_INFO_CTE = """,
before_info AS (
    SELECT bi.race_id AS bi_race_id,
           bi.lane_number AS bi_lane_number,
           bi.exhibition_time_sec AS bi_exhibition_time,
           bi.start_exhibition_st_sec AS bi_start_st,
           bi.tilt_angle AS bi_tilt,
           bi.start_exhibition_course AS bi_course,
           CASE
               WHEN bi.available_at > br.scheduled_deadline_at THEN 1 ELSE 0
           END AS bi_too_late,
           ROW_NUMBER() OVER (
               PARTITION BY bi.race_id, bi.lane_number ORDER BY bi.observed_at
           ) AS bi_rank
      FROM before_info_entries bi
      JOIN races br ON br.id = bi.race_id
     WHERE br.race_date >= :meeting_window_start
       AND br.race_date <= :end_date
)
"""

_BEFORE_INFO_COLUMNS = """,
       bi.bi_exhibition_time,
       bi.bi_start_st,
       bi.bi_tilt,
       bi.bi_course,
       bi.bi_too_late
"""

_BEFORE_INFO_JOIN = """
  LEFT JOIN before_info bi
         ON bi.bi_race_id = r.id
        AND bi.bi_lane_number = e.lane_number
        AND bi.bi_rank = 1
"""

# Per-course racer statistics, joined point-in-time.
#
# `racer_period_stats.available_at` is the application period's start, so
# the row usable for a race is the latest one already available at its
# deadline. That is expressed as a range join rather than a LATERAL
# `ORDER BY ... LIMIT 1` per entry: `LEAD` over 40k period rows costs
# nothing, while the LATERAL form would run once per entry row.
#
# **Joined on the course actually taken, not the lane.** The stats are
# keyed by 進入; the card only gives 枠. They differ on 8.16% of boats,
# and non-randomly -- 進入変更 happens exactly where course aptitude is
# being contested. `start_exhibition_course` from 直前情報 is the only
# pre-race observation of it, so this falls back to the lane only when
# 直前情報 is absent.
_RACER_STATS_CTE = """,
racer_periods AS (
    SELECT ps.id AS rp_id,
           ps.racer_id AS rp_racer_id,
           ps.available_at AS rp_from,
           LEAD(ps.available_at) OVER (
               PARTITION BY ps.racer_id ORDER BY ps.available_at
           ) AS rp_until
      FROM racer_period_stats ps
),
entry_course AS (
    SELECT e.race_id AS ec_race_id,
           e.lane_number AS ec_lane_number,
           e.racer_id AS ec_racer_id,
           r2.scheduled_deadline_at AS ec_deadline,
           COALESCE((
               SELECT bi2.start_exhibition_course
                 FROM before_info_entries bi2
                WHERE bi2.race_id = e.race_id
                  AND bi2.lane_number = e.lane_number
                  AND bi2.available_at <= r2.scheduled_deadline_at
                  AND bi2.start_exhibition_course IS NOT NULL
                ORDER BY bi2.observed_at
                LIMIT 1
           ), e.lane_number) AS ec_course
      FROM race_entries e
      JOIN races r2 ON r2.id = e.race_id
     WHERE r2.race_date >= :meeting_window_start
       AND r2.race_date <= :end_date
),
racer_course AS (
    SELECT ec.ec_race_id AS rc_race_id,
           ec.ec_lane_number AS rc_lane_number,
           ec.ec_course AS rc_course,
           cs.entry_count AS rc_entries,
           cs.finish_1_count AS rc_wins
      FROM entry_course ec
      LEFT JOIN racer_periods rp
             ON rp.rp_racer_id = ec.ec_racer_id
            AND rp.rp_from <= ec.ec_deadline
            AND (rp.rp_until IS NULL OR rp.rp_until > ec.ec_deadline)
      LEFT JOIN racer_period_course_stats cs
             ON cs.racer_period_stats_id = rp.rp_id
            AND cs.course_number = ec.ec_course
)
"""

_RACER_STATS_COLUMNS = """,
       rc.rc_course,
       rc.rc_entries,
       rc.rc_wins
"""

_RACER_STATS_JOIN = """
  LEFT JOIN racer_course rc
         ON rc.rc_race_id = r.id
        AND rc.rc_lane_number = e.lane_number
"""

_FEATURE_COLUMNS = """
SELECT r.id AS race_id,
       r.race_date,
       r.race_class,
       e.lane_number,
       e.listed_national_win_rate,
       e.listed_national_second_rate,
       e.listed_local_win_rate,
       e.listed_local_second_rate,
       e.listed_motor_second_rate,
       e.listed_boat_second_rate,
       e.listed_age,
       e.listed_weight,
       e.listed_class,
       mf.prior_starts,
       mf.prior_avg_score,
       CASE WHEN e.available_at > r.scheduled_deadline_at THEN 1 ELSE 0 END AS too_late
"""

_TARGET_COLUMNS = """,
       (SELECT count(*) FROM race_result_entries re
         JOIN race_results res ON res.id = re.race_result_id
        WHERE res.race_id = r.id AND re.finish_position = 1) AS winner_count,
       (SELECT min(re.lane_number) FROM race_result_entries re
         JOIN race_results res ON res.id = re.race_result_id
        WHERE res.race_id = r.id AND re.finish_position = 1) AS winner_lane,
       (SELECT count(*) FROM race_result_entries re
         JOIN race_results res ON res.id = re.race_result_id
        WHERE res.race_id = r.id AND re.finish_position = 2) AS second_count,
       (SELECT min(re.lane_number) FROM race_result_entries re
         JOIN race_results res ON res.id = re.race_result_id
        WHERE res.race_id = r.id AND re.finish_position = 2) AS second_lane
"""

_FROM = """
  FROM races r
  JOIN race_entries e ON e.race_id = r.id
  LEFT JOIN meeting_form mf ON mf.mw_race_id = r.id AND mf.mw_lane_number = e.lane_number
"""

# Training: finished races over a date range, with the target.
#
# Prediction: one date's races, no target and no `status` filter, because
# the whole point is that these races have not run. Everything else --
# the within-meeting form window, the 直前情報 block, the lane feature
# computation, the available_at check against the deadline -- is built
# from the *same* fragments as training, so the two cannot drift. That
# mattered enough to be worth the composition: a feature computed one way
# at fit time and another way at predict time is a defect that produces
# no error, only wrong numbers. `include_before_info` is passed through
# to both for the same reason, rather than each assembling its own.


def _row_sql(*, include_before_info: bool, include_racer_stats: bool = False) -> str:
    return (
        _MEETING_CTE
        + (_BEFORE_INFO_CTE if include_before_info else "")
        + (_RACER_STATS_CTE if include_racer_stats else "")
        + _FEATURE_COLUMNS
        + (_BEFORE_INFO_COLUMNS if include_before_info else "")
        + (_RACER_STATS_COLUMNS if include_racer_stats else "")
        + _TARGET_COLUMNS
        + _FROM
        + (_BEFORE_INFO_JOIN if include_before_info else "")
        + (_RACER_STATS_JOIN if include_racer_stats else "")
        + """
 WHERE r.status = 'finished'
   AND r.race_date >= :start_date
   AND r.race_date <= :end_date
   AND r.scheduled_deadline_at IS NOT NULL
 ORDER BY r.race_date, r.id, e.lane_number
"""
    )


def _predict_sql(*, include_before_info: bool, include_racer_stats: bool = False) -> str:
    return (
        _MEETING_CTE
        + (_BEFORE_INFO_CTE if include_before_info else "")
        + (_RACER_STATS_CTE if include_racer_stats else "")
        + _FEATURE_COLUMNS
        + (_BEFORE_INFO_COLUMNS if include_before_info else "")
        + (_RACER_STATS_COLUMNS if include_racer_stats else "")
        + _FROM
        + (_BEFORE_INFO_JOIN if include_before_info else "")
        + (_RACER_STATS_JOIN if include_racer_stats else "")
        + """
 WHERE r.race_date = :race_date
   AND r.scheduled_deadline_at IS NOT NULL
   AND r.status <> 'cancelled'
 ORDER BY r.id, e.lane_number
"""
    )


def _lane_features(row) -> list[float] | None:
    """One lane's slice, or None if anything it needs is missing.

    Season fields are not imputed: a mean or zero would be
    indistinguishable from a real reading to every model downstream, and
    the audit shows the card fields are essentially always present -- so
    a gap here is unusual enough to be worth dropping and counting rather
    than papering over. The within-meeting features never trigger a drop
    here: shrinkage always defines them, even at zero prior starts.
    """
    values = [
        row.listed_national_win_rate,
        row.listed_national_second_rate,
        row.listed_local_win_rate,
        row.listed_local_second_rate,
        row.listed_motor_second_rate,
        row.listed_boat_second_rate,
        row.listed_age,
        row.listed_weight,
    ]
    if any(v is None for v in values):
        return None
    rank = _CLASS_RANK.get((row.listed_class or "").strip())
    if rank is None:
        return None

    prior_starts = float(row.prior_starts or 0)
    season_prior = float(row.listed_national_win_rate) / 10.0
    if prior_starts > 0 and row.prior_avg_score is not None:
        form_score = (
            float(row.prior_avg_score) * prior_starts
            + season_prior * MEETING_FORM_SHRINKAGE_STARTS
        ) / (prior_starts + MEETING_FORM_SHRINKAGE_STARTS)
    else:
        form_score = season_prior

    return [float(v) for v in values] + [rank, prior_starts, form_score]


def _z_scores(values: list[float | None]) -> list[float]:
    """Z-score across the lanes that have a value; a lane without one
    takes the field mean (0.0).

    Population sd over six values, not the sample sd -- these six *are*
    the population the transform is removing shared terms from, and there
    is no wider group being estimated.

    A field where every boat recorded the same value has nothing to
    discriminate, so a zero spread returns zeros rather than dividing by
    it. Same for a field with fewer than two readings.
    """
    present = [v for v in values if v is not None]
    if len(present) < 2:
        return [0.0] * len(values)
    mean = sum(present) / len(present)
    sd = (sum((v - mean) ** 2 for v in present) / len(present)) ** 0.5
    if sd <= 0:
        return [0.0] * len(values)
    return [0.0 if v is None else (v - mean) / sd for v in values]


def _racer_stats_lane(row) -> list[float]:
    """One lane's per-course ability, empirical-Bayes shrunk.

    Never returns None and never drops a race. A racer with no usable
    period row -- one who debuted after the last fan file, or raced
    before the parseable 2014 layout -- has zero starts, which the
    shrinkage resolves to exactly the course's base rate. That is the
    honest answer for "no evidence", and it is a real number rather than
    a gap, so the feature is always defined.

    `course_starts` rides alongside so the model can discount a rate
    backed by five starts against one backed by fifty; without it the
    shrunk value alone hides how much evidence is behind it.
    """
    course = int(row.rc_course) if row.rc_course is not None else None
    base = COURSE_BASE_WIN_RATE.get(course)
    if base is None:
        # A course outside 1-6 should not exist; treat it as no evidence
        # rather than guessing which prior applies.
        return [0.0, 0.0]
    k = COURSE_SHRINKAGE_STARTS[course]
    starts = float(row.rc_entries or 0)
    wins = float(row.rc_wins or 0)
    return [(wins + k * base) / (starts + k), starts]


def _racer_stats_globals(lane_rows: dict[int, object]) -> list[float]:
    """The two per-race values, both deliberately non-linear.

    A multinomial logit already carries all six `national_win_rate`
    columns, so any *linear* combination of them -- a racer's gap to the
    mean of the others, the field's total or average strength -- is
    information it already has. Only the shapes it cannot form are worth
    a column: a count over a categorical (`field_a1_count`) and a spread
    (`field_win_rate_sd`, which needs squares).

    Both were measured on 2026-08-03: an all-A1 race is the worst
    composition to back the top racer in (return 0.6723 against 0.8432
    for an all-B one), and the within-race spread moves the favourite's
    hit rate 23.01% → 24.85% across terciles.
    """
    rates = [
        float(r.listed_national_win_rate)
        for r in lane_rows.values()
        if r.listed_national_win_rate is not None
    ]
    a1 = sum(
        1 for r in lane_rows.values() if (r.listed_class or "").strip() == "A1"
    )
    if len(rates) < 2:
        return [float(a1), 0.0]
    mean = sum(rates) / len(rates)
    sd = (sum((v - mean) ** 2 for v in rates) / (len(rates) - 1)) ** 0.5
    return [float(a1), sd]


def _before_info_block(raw_by_lane: dict[int, object]) -> dict[int, list[float]] | None:
    """One race's 直前情報 block per lane, or None if the race lacks it.

    All six lanes must carry 展示タイム, tilt and 進入 course; the race is
    dropped otherwise rather than imputed, on the same reasoning as the
    card fields -- an invented value is indistinguishable from a real
    reading to everything downstream. 展示ST is the documented exception
    (see the module docstring): missing on 2.6% of races, and a lane
    without one takes the field mean instead of costing the race.
    """
    if set(raw_by_lane) != set(LANES):
        return None

    times: list[float] = []
    tilts: list[float] = []
    courses: list[int] = []
    starts: list[float | None] = []
    for lane in LANES:
        row = raw_by_lane[lane]
        if row.bi_exhibition_time is None or row.bi_tilt is None or row.bi_course is None:
            return None
        times.append(float(row.bi_exhibition_time))
        tilts.append(float(row.bi_tilt))
        courses.append(int(row.bi_course))
        starts.append(None if row.bi_start_st is None else float(row.bi_start_st))

    time_z = _z_scores(times)
    start_z = _z_scores(starts)
    return {
        lane: [
            time_z[index],
            start_z[index],
            tilts[index],
            1.0 if courses[index] != lane else 0.0,
        ]
        for index, lane in enumerate(LANES)
    }


def build_dataset(
    session: Session,
    *,
    start_date: dt.date,
    end_date: dt.date,
    include_before_info: bool = False,
    include_racer_stats: bool = False,
) -> Dataset:
    """Pull `[start_date, end_date]` into `(X, y, dates, phases)`.

    Rows come back ordered by race date, which is what
    `walk_forward.generate_monthly_folds` expects to see.

    `include_before_info` appends the 直前情報 block to each lane and
    drops any race without it. Nothing before 2023-05-01 has it, so a
    window that predates the backfill returns an empty dataset rather
    than a quietly card-only one -- `dropped_missing_before_info` is what
    says which happened.
    """
    if end_date < start_date:
        raise ValueError(f"end_date {end_date} precedes start_date {start_date}")

    stats = DatasetStats()
    X: list[list[float]] = []
    y: list[int] = []
    dates: list[dt.date] = []
    phases: list[str] = []
    race_ids: list = []
    y_second: list[int | None] = []

    current_race = None
    lanes: dict[int, list[float] | None] = {}
    before_info: dict[int, object] = {}
    lane_rows: dict[int, object] = {}
    race_date = None
    race_phase = "unknown"
    seeded = False
    winner_count = 0
    winner_lane = None
    second_count = 0
    second_lane = None
    too_late = False

    def flush() -> None:
        nonlocal lanes, before_info, lane_rows, race_date, race_phase, seeded
        nonlocal winner_count, winner_lane, second_count, second_lane, too_late
        if current_race is None:
            return
        stats.races_considered += 1
        block = _before_info_block(before_info) if include_before_info else {}
        if set(lanes) != set(LANES):
            stats.dropped_not_six_lanes += 1
        elif too_late:
            stats.dropped_late_feature += 1
        elif winner_count != 1 or winner_lane is None:
            stats.dropped_no_single_winner += 1
        elif any(lanes[lane] is None for lane in LANES):
            stats.dropped_missing_feature += 1
        elif block is None:
            stats.dropped_missing_before_info += 1
        else:
            row: list[float] = []
            for lane in LANES:
                row.extend(lanes[lane])
                row.extend(block.get(lane, ()))
                if include_racer_stats:
                    row.extend(_racer_stats_lane(lane_rows[lane]))
            row.append(1.0 if seeded else 0.0)
            if include_racer_stats:
                row.extend(_racer_stats_globals(lane_rows))
            X.append(row)
            y.append(int(winner_lane))
            dates.append(race_date)
            phases.append(race_phase)
            race_ids.append(current_race)
            # A second place is only usable when exactly one boat holds
            # it; 同着 for 2nd is real (the archive has them) and there is
            # no non-arbitrary way to pick one, so it is recorded as
            # absent rather than resolved.
            y_second.append(
                int(second_lane) if second_count == 1 and second_lane is not None else None
            )
            stats.races_used += 1
        lanes = {}
        before_info = {}
        lane_rows = {}
        too_late = False

    meeting_window_start = start_date - dt.timedelta(days=MEETING_WINDOW_MARGIN_DAYS)
    for row in session.execute(
        text(
            _row_sql(
                include_before_info=include_before_info,
                include_racer_stats=include_racer_stats,
            )
        ),
        {
            "start_date": start_date,
            "end_date": end_date,
            "meeting_window_start": meeting_window_start,
        },
    ):
        if row.race_id != current_race:
            flush()
            current_race = row.race_id
            race_date = row.race_date
            race_phase = classify_race_phase(row.race_class)
            seeded = is_standing_seeded(row.race_class)
            winner_count = int(row.winner_count or 0)
            winner_lane = row.winner_lane
            second_count = int(row.second_count or 0)
            second_lane = row.second_lane
        if row.too_late:
            too_late = True
        lane_number = int(row.lane_number)
        lanes[lane_number] = _lane_features(row)
        lane_rows[lane_number] = row
        if include_before_info:
            if row.bi_too_late:
                too_late = True
            if row.bi_exhibition_time is not None:
                before_info[lane_number] = row
    flush()

    # SQLite hands DATE back as text where PostgreSQL gives a date, and
    # walk_forward compares these with `<`, which would silently do the
    # wrong thing on mixed types.
    dates = [dt.date.fromisoformat(d) if isinstance(d, str) else d for d in dates]

    return Dataset(
        X=X,
        y=y,
        dates=dates,
        phases=phases,
        feature_names=feature_columns(
            include_before_info=include_before_info,
            include_racer_stats=include_racer_stats,
        ),
        stats=stats,
        race_ids=race_ids,
        y_second=y_second,
    )


@dataclass
class PredictionStats:
    races_considered: int = 0
    races_used: int = 0
    dropped_not_six_lanes: int = 0
    dropped_missing_feature: int = 0
    dropped_late_feature: int = 0
    dropped_missing_before_info: int = 0

    def __str__(self) -> str:
        return (
            f"races_considered={self.races_considered} races_used={self.races_used} "
            f"dropped_not_six_lanes={self.dropped_not_six_lanes} "
            f"dropped_missing_feature={self.dropped_missing_feature} "
            f"dropped_late_feature={self.dropped_late_feature} "
            f"dropped_missing_before_info={self.dropped_missing_before_info}"
        )


@dataclass
class PredictionRows:
    race_ids: list
    X: list[list[float]]
    feature_names: list[str]
    stats: PredictionStats

    def __len__(self) -> int:
        return len(self.race_ids)


def build_prediction_rows(
    session: Session,
    *,
    race_date: dt.date,
    include_before_info: bool = False,
    include_racer_stats: bool = False,
) -> PredictionRows:
    """Feature rows for one date's races, for races that have not run.

    Same features, same within-meeting window and same `available_at`
    check as `build_dataset` -- they are built from the same SQL
    fragments, so a feature cannot be computed one way at fit time and
    another at predict time. `include_before_info` must match the model
    being applied, which is why `predict_daily` takes it from the
    registry entry rather than from its own flag.

    Today's own earlier races contribute nothing to within-meeting form
    even though they appear in the window: they have no result rows yet,
    so their score is NULL and both `COUNT(score)` and `AVG(score)`
    ignore them. That is the same conservative bound
    `loader.results_available_at` sets, arrived at without a special case.

    With `include_before_info`, a race whose 直前情報 has not been
    published (or not yet captured) is dropped and counted, not predicted
    from a partial block. That is what makes "only the races whose 直前情報
    is in" a property of the data rather than a filter the caller has to
    remember to apply.
    """
    stats = PredictionStats()
    race_ids: list = []
    X: list[list[float]] = []

    current_race = None
    lanes: dict[int, list[float] | None] = {}
    before_info: dict[int, object] = {}
    lane_rows: dict[int, object] = {}
    seeded = False
    too_late = False

    def flush() -> None:
        nonlocal lanes, before_info, lane_rows, too_late
        if current_race is None:
            return
        stats.races_considered += 1
        block = _before_info_block(before_info) if include_before_info else {}
        if set(lanes) != set(LANES):
            stats.dropped_not_six_lanes += 1
        elif too_late:
            stats.dropped_late_feature += 1
        elif any(lanes[lane] is None for lane in LANES):
            stats.dropped_missing_feature += 1
        elif block is None:
            stats.dropped_missing_before_info += 1
        else:
            row: list[float] = []
            for lane in LANES:
                row.extend(lanes[lane])
                row.extend(block.get(lane, ()))
                if include_racer_stats:
                    row.extend(_racer_stats_lane(lane_rows[lane]))
            row.append(1.0 if seeded else 0.0)
            if include_racer_stats:
                row.extend(_racer_stats_globals(lane_rows))
            X.append(row)
            race_ids.append(current_race)
            stats.races_used += 1
        lanes = {}
        before_info = {}
        lane_rows = {}
        too_late = False

    meeting_window_start = race_date - dt.timedelta(days=MEETING_WINDOW_MARGIN_DAYS)
    for row in session.execute(
        text(
            _predict_sql(
                include_before_info=include_before_info,
                include_racer_stats=include_racer_stats,
            )
        ),
        {
            "race_date": race_date,
            "end_date": race_date,
            "meeting_window_start": meeting_window_start,
        },
    ):
        # SQLite returns the UUID as hex text through raw SQL where
        # PostgreSQL returns a uuid object; these ids are written back as a
        # foreign key, so they have to be the real type -- and the
        # conversion has to happen before the comparison below, or every
        # row looks like a new race.
        race_id = uuid.UUID(row.race_id) if isinstance(row.race_id, str) else row.race_id
        if race_id != current_race:
            flush()
            current_race = race_id
            seeded = is_standing_seeded(row.race_class)
        if row.too_late:
            too_late = True
        lane_number = int(row.lane_number)
        lanes[lane_number] = _lane_features(row)
        lane_rows[lane_number] = row
        if include_before_info:
            if row.bi_too_late:
                too_late = True
            if row.bi_exhibition_time is not None:
                before_info[lane_number] = row
    flush()

    return PredictionRows(
        race_ids=race_ids,
        X=X,
        feature_names=feature_columns(
            include_before_info=include_before_info,
            include_racer_stats=include_racer_stats,
        ),
        stats=stats,
    )
