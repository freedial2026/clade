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

Every feature comes from `race_entries`, which is the B-file race card,
and each row's `available_at` is checked against its race's
`scheduled_deadline_at` rather than assumed: the audit says the whole
database satisfies it today, but a dataset builder that trusts that is
one loader change away from silently training on the future. A race with
any entry available too late is dropped and counted.

Rows the target cannot describe
-------------------------------

- A dead heat (two boats on `finish_position=1`, 16 in the archive) has
  no single winning lane. Excluded, not resolved arbitrarily.
- A void race (every boat carrying a status code, none a placing, 132 in
  the archive) has no winner at all.
- A card without exactly six lanes cannot fill a fixed-width row.

Each exclusion is counted in `DatasetStats` so a shrinking dataset is
visible rather than silent.

Scale
-----

1.15 M races x 54 float features do not fit in plain Python lists, so
`build_dataset` takes a date range and is meant to be pointed at a
window. `docs/PROJECT_PROFILE.md` puts array libraries behind a "when
justified by dataset size" gate; a recent window stays under it.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.orm import Session

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
)

# A1 > A2 > B1 > B2 is an ordered grade, so it is encoded as an ordinal
# rather than one-hot: the order is the information.
_CLASS_RANK = {"A1": 4.0, "A2": 3.0, "B1": 2.0, "B2": 1.0}


@dataclass
class DatasetStats:
    races_considered: int = 0
    races_used: int = 0
    dropped_not_six_lanes: int = 0
    dropped_no_single_winner: int = 0
    dropped_missing_feature: int = 0
    dropped_late_feature: int = 0
    excluded_dates: list[dt.date] = field(default_factory=list)

    def __str__(self) -> str:
        return (
            f"races_considered={self.races_considered} races_used={self.races_used} "
            f"dropped_not_six_lanes={self.dropped_not_six_lanes} "
            f"dropped_no_single_winner={self.dropped_no_single_winner} "
            f"dropped_missing_feature={self.dropped_missing_feature} "
            f"dropped_late_feature={self.dropped_late_feature}"
        )


@dataclass
class Dataset:
    X: list[list[float]]
    y: list[int]
    dates: list[dt.date]
    feature_names: list[str]
    stats: DatasetStats

    def __len__(self) -> int:
        return len(self.y)


def feature_columns() -> list[str]:
    return [f"lane{lane}_{name}" for lane in LANES for name in FEATURE_NAMES]


_ROW_SQL = """
SELECT r.id AS race_id,
       r.race_date,
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
       CASE WHEN e.available_at > r.scheduled_deadline_at THEN 1 ELSE 0 END AS too_late,
       (SELECT count(*) FROM race_result_entries re
         JOIN race_results res ON res.id = re.race_result_id
        WHERE res.race_id = r.id AND re.finish_position = 1) AS winner_count,
       (SELECT min(re.lane_number) FROM race_result_entries re
         JOIN race_results res ON res.id = re.race_result_id
        WHERE res.race_id = r.id AND re.finish_position = 1) AS winner_lane
  FROM races r
  JOIN race_entries e ON e.race_id = r.id
 WHERE r.status = 'finished'
   AND r.race_date >= :start_date
   AND r.race_date <= :end_date
   AND r.scheduled_deadline_at IS NOT NULL
 ORDER BY r.race_date, r.id, e.lane_number
"""


def _lane_features(row) -> list[float] | None:
    """One lane's slice, or None if anything it needs is missing.

    Missing values are not imputed. A mean or zero would be indis-
    tinguishable from a real reading to every model downstream, and the
    audit shows the card fields are essentially always present -- so a
    gap here is unusual enough to be worth dropping and counting rather
    than papering over.
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
    return [float(v) for v in values] + [rank]


def build_dataset(
    session: Session, *, start_date: dt.date, end_date: dt.date
) -> Dataset:
    """Pull `[start_date, end_date]` into `(X, y, dates)`.

    Rows come back ordered by race date, which is what
    `walk_forward.generate_monthly_folds` expects to see.
    """
    if end_date < start_date:
        raise ValueError(f"end_date {end_date} precedes start_date {start_date}")

    stats = DatasetStats()
    X: list[list[float]] = []
    y: list[int] = []
    dates: list[dt.date] = []

    current_race = None
    lanes: dict[int, list[float] | None] = {}
    race_date = None
    winner_count = 0
    winner_lane = None
    too_late = False

    def flush() -> None:
        nonlocal lanes, race_date, winner_count, winner_lane, too_late
        if current_race is None:
            return
        stats.races_considered += 1
        if set(lanes) != set(LANES):
            stats.dropped_not_six_lanes += 1
        elif too_late:
            stats.dropped_late_feature += 1
        elif winner_count != 1 or winner_lane is None:
            stats.dropped_no_single_winner += 1
        elif any(lanes[lane] is None for lane in LANES):
            stats.dropped_missing_feature += 1
        else:
            row: list[float] = []
            for lane in LANES:
                row.extend(lanes[lane])
            X.append(row)
            y.append(int(winner_lane))
            dates.append(race_date)
            stats.races_used += 1
        lanes = {}
        too_late = False

    for row in session.execute(
        text(_ROW_SQL), {"start_date": start_date, "end_date": end_date}
    ):
        if row.race_id != current_race:
            flush()
            current_race = row.race_id
            race_date = row.race_date
            winner_count = int(row.winner_count or 0)
            winner_lane = row.winner_lane
        if row.too_late:
            too_late = True
        lanes[int(row.lane_number)] = _lane_features(row)
    flush()

    # SQLite hands DATE back as text where PostgreSQL gives a date, and
    # walk_forward compares these with `<`, which would silently do the
    # wrong thing on mixed types.
    dates = [dt.date.fromisoformat(d) if isinstance(d, str) else d for d in dates]

    return Dataset(X=X, y=y, dates=dates, feature_names=feature_columns(), stats=stats)
