"""Relational schema for parsed BOATRACE source data.

Implements the tables from
docs/domain/claude_boatrace_prediction_system_implementation_guide.md
§6.2/§7.2 that the currently-parsed sources (B-file race cards, K-file
results) can actually fill, using SQLAlchemy 2.0 declarative mapping.
PostgreSQL is the target (docs/PROJECT_PROFILE.md); every column type
used here is a generic SQLAlchemy type, so the same metadata also
creates on SQLite, which is what the tests run against.

Time semantics (docs/.claude/rules/08-data-database.md): every timestamp
column is `TIMESTAMP WITH TIME ZONE` holding UTC, matching
`temporal.py`'s strict-UTC rule. Source files carry Japanese local
dates/times, so the loader converts through `Asia/Tokyo` -- see
`loader.py` for how each `available_at` is derived and why.

## Deliberate deviations from the guide's §7.2 listing

Each of these is a case where following the guide literally would have
meant inventing data that no available source provides.

1. **`motors` / `boats` tables are not created.** The guide keys them
   `UNIQUE(venue_id, motor_number, service_period_start)`, and that
   service period is the whole point: motor #12 at Toda in 2007 is a
   physically different motor from motor #12 at Toda in 2020, because
   fleets are replaced. No source located so far publishes service
   periods, so any rows written now would assert an identity across 21
   years that is known to be false, and `race_entries.motor_id` would
   silently join unrelated machines. The numbers and their performance
   rates are instead kept as point-in-time values on `race_entries`
   (`listed_motor_number` etc.), which is exactly what the B-file
   states. Adding the tables later is an additive migration.

2. **`races.venue_id` is denormalized** (the guide puts venues one hop
   away via `meeting_id`). The guide's own
   `UNIQUE(race_date, venue_id, race_number)` on `races` cannot be
   expressed without it, and that unique constraint is the natural key
   from `race_id.RaceKey`, i.e. the thing the whole P0 identifier stage
   exists to enforce.

3. **`race_payouts` is new.** The K-file publishes real payouts per bet
   type and `paper_simulation.py` needs real returns rather than
   synthetic ones. The guide has no table for them.

4. **Point-in-time racer attributes live on `race_entries`, not
   `racers`.** `racers` is an identity table (registration number and
   name). A racer's class, branch, weight and win rates all change over
   a career, so reading them off a mutable dimension row would feed a
   2007 race the racer's 2026 attributes -- future knowledge, which
   rule 08 forbids. The `racers.branch`/`birth_date`/`sex` columns from
   the guide are kept but are never written by the loader.

5. **`exhibition_entries` holds only what the K-file supplies**
   (`exhibition_time_sec`), and its `available_at` is the *results*
   availability, not the pre-race exhibition time. The exhibition run
   happens before the race, but this project only learns of it from the
   post-race results file, so claiming pre-race availability would be a
   leak, and `feature_availability.py`'s gate correctly refuses these
   values for any pre-race `prediction_at`.

   This is now superseded as the *only* route to exhibition data, but
   remains correct for the K-file-derived rows: `beforeinfo_source.py`
   fetches BOATRACE's 直前情報 pages, which do serve past dates and
   carry genuinely pre-race exhibition times, tilt angles, parts
   replacements and start-exhibition courses. Those need their own
   table and loader (not yet built) with an `available_at` reflecting
   real pre-race availability -- do not backfill them into this table,
   whose availability semantics are deliberately results-time.

6. **`race_meetings.meeting_end_date` is left NULL at load time.** It is
   derivable only by looking at later days of the same series, which is
   future knowledge relative to any race in that series.

7. **Fan-file 期別 statistics get their own point-in-time pair of tables**
   (`racer_period_stats` / `racer_period_course_stats`), not columns on
   `racers`. Same principle as deviation 4: these values describe a
   racer during one half-year period, and writing them onto the identity
   row would let a 2015 race read a racer's 2026 statistics. The
   per-course breakdown is a second, narrow table rather than 6x18
   columns on the first -- see `RacerPeriodCourseStats` for why that
   shape, and why the breakdown is materialized at all.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .ids import uuid7

# Explicit names so Alembic autogenerate produces stable, droppable
# constraint names instead of backend-assigned ones.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s",
    "pk": "pk_%(table_name)s",
}

# Win rates reach 9.99, "second-or-better" rates are percentages up to
# 100.00. One width covers both, and Numeric (not Float) so a value the
# source printed as 6.85 reads back as exactly 6.85.
RATE = Numeric(5, 2)

VENUE_CODE_LENGTH = 2

# Venue code -> name, cross-checked against the `venue_name` field of all
# 24 pages fetched by `venue_data_source.py`. The site pads short names
# with an ideographic space for layout ("桐　生ボートレース場"); the padding
# is removed here for the same reason `bfile_parser.ParsedRaceCard.
# race_class` removes it -- it carries no information and would make the
# same venue compare unequal to itself.
VENUE_NAMES: dict[str, str] = {
    "01": "桐生",
    "02": "戸田",
    "03": "江戸川",
    "04": "平和島",
    "05": "多摩川",
    "06": "浜名湖",
    "07": "蒲郡",
    "08": "常滑",
    "09": "津",
    "10": "三国",
    "11": "びわこ",
    "12": "住之江",
    "13": "尼崎",
    "14": "鳴門",
    "15": "丸亀",
    "16": "児島",
    "17": "宮島",
    "18": "徳山",
    "19": "下関",
    "20": "若松",
    "21": "芦屋",
    "22": "福岡",
    "23": "唐津",
    "24": "大村",
}

# Race status values for `races.status`. `cancelled` is set from the
# K-file's 中止 marker (kfile_parser.ParsedRace.is_cancelled) and must be
# excluded from training sets -- see tasks/HANDOFF.md.
RACE_STATUS_SCHEDULED = "scheduled"
RACE_STATUS_FINISHED = "finished"
RACE_STATUS_CANCELLED = "cancelled"
RACE_STATUSES = (RACE_STATUS_SCHEDULED, RACE_STATUS_FINISHED, RACE_STATUS_CANCELLED)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid7)


class TimestampMixin:
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class DataSource(TimestampMixin, Base):
    """Registry of where data came from (guide §6.2).

    Exists so provenance is a foreign key rather than a convention.
    `inventory.py` currently decides which raw files are approved from a
    hardcoded suffix list flagged as provisional in tasks/P0-T002.md;
    this table is the registry that list was waiting for.
    """

    __tablename__ = "data_sources"

    id: Mapped[uuid.UUID] = _pk()
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    provider: Mapped[str] = mapped_column(String(200), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    official_url: Mapped[str | None] = mapped_column(Text)
    terms_url: Mapped[str | None] = mapped_column(Text)
    acquisition_method: Mapped[str | None] = mapped_column(String(100))
    update_frequency: Mapped[str | None] = mapped_column(String(100))
    license_note: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class Venue(TimestampMixin, Base):
    __tablename__ = "venues"

    id: Mapped[uuid.UUID] = _pk()
    code: Mapped[str] = mapped_column(String(VENUE_CODE_LENGTH), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    timezone: Mapped[str] = mapped_column(String(50), nullable=False, default="Asia/Tokyo")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class RaceMeeting(TimestampMixin, Base):
    """One multi-day series (節) at one venue.

    Identified by `(venue_id, meeting_start_date)`. That start date is
    exact for a 節 whose 第1日 was loaded and an estimate otherwise: it
    is a key, not a claim, because the B-file's day counter repeats on
    postponed days. Which meeting a race day belongs to is therefore
    decided by `db.meeting_resolution`, not by arithmetic on the key --
    see that module for the measurements behind the rule.
    """

    __tablename__ = "race_meetings"
    __table_args__ = (UniqueConstraint("venue_id", "meeting_start_date"),)

    id: Mapped[uuid.UUID] = _pk()
    venue_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("venues.id"), nullable=False)
    meeting_start_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    # NULL by design: knowing when a series ends requires later days of
    # that series, which is future knowledge for any race inside it.
    meeting_end_date: Mapped[dt.date | None] = mapped_column(Date)
    meeting_title: Mapped[str | None] = mapped_column(String(200))
    # No source parsed so far states the grade separately; it is
    # sometimes embedded in `meeting_title` but extracting it by pattern
    # would be a guess, so it stays NULL rather than being fabricated.
    grade: Mapped[str | None] = mapped_column(String(20))
    source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("data_sources.id"))

    venue: Mapped[Venue] = relationship()


class Racer(TimestampMixin, Base):
    """Racer identity only -- see deviation 4 in the module docstring.

    `branch`, `birth_date` and `sex` are part of the guide's table and
    are kept so a future source can fill them, but the B/K-file loader
    never writes them: everything about a racer that varies over a
    career belongs on `race_entries` as a point-in-time value.
    """

    __tablename__ = "racers"

    id: Mapped[uuid.UUID] = _pk()
    registration_number: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    branch: Mapped[str | None] = mapped_column(String(20))
    birth_date: Mapped[dt.date | None] = mapped_column(Date)
    sex: Mapped[str | None] = mapped_column(String(10))


class Race(TimestampMixin, Base):
    """One race, keyed naturally by `race_id.RaceKey`.

    `scheduled_deadline_at` is the only real time-of-day anchor any
    source provides (the B-file race header's 電話投票締切予定). Without it
    `available_at <= prediction_at` could only ever be checked at
    date granularity, which is not a leakage check at all.
    """

    __tablename__ = "races"
    __table_args__ = (
        UniqueConstraint("race_date", "venue_id", "race_number"),
        CheckConstraint("race_number BETWEEN 1 AND 12", name="race_number_range"),
    )

    id: Mapped[uuid.UUID] = _pk()
    meeting_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("race_meetings.id"))
    # Denormalized so the guide's own natural-key unique constraint above
    # is expressible -- see deviation 2 in the module docstring.
    venue_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("venues.id"), nullable=False, index=True
    )
    race_date: Mapped[dt.date] = mapped_column(Date, nullable=False, index=True)
    race_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    distance_meters: Mapped[int | None] = mapped_column(Integer)
    scheduled_deadline_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    # Never published by any parsed source; kept for a future 直前 feed.
    actual_deadline_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=RACE_STATUS_SCHEDULED)
    is_fixed_entry: Mapped[bool | None] = mapped_column(Boolean)
    # Raw B-file class label and its whitespace-stripped form. Group and
    # encode on `race_class`; the raw label fragments one class into
    # several because the file pads it for column alignment (see
    # `bfile_parser.ParsedRaceCard.race_class`).
    race_class_label: Mapped[str | None] = mapped_column(String(50))
    race_class: Mapped[str | None] = mapped_column(String(50), index=True)
    # Day-N-of-series. Denormalized from the meeting because it varies
    # per race day and is used directly as a feature (motor/boat
    # condition and racer form both drift across a series).
    series_day: Mapped[int | None] = mapped_column(SmallInteger)

    venue: Mapped[Venue] = relationship()
    meeting: Mapped[RaceMeeting | None] = relationship()
    entries: Mapped[list[RaceEntry]] = relationship(
        back_populates="race", cascade="all, delete-orphan"
    )


class RaceEntry(TimestampMixin, Base):
    """A boat's pre-race card entry: the leakage-safe feature row.

    Everything here comes from the B-file, published before the race, so
    `available_at` is a genuine pre-race timestamp and these columns are
    the legitimate inputs to a first-place model.
    """

    __tablename__ = "race_entries"
    __table_args__ = (
        UniqueConstraint("race_id", "lane_number"),
        CheckConstraint("lane_number BETWEEN 1 AND 6", name="lane_number_range"),
    )

    id: Mapped[uuid.UUID] = _pk()
    race_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("races.id", ondelete="CASCADE"), nullable=False
    )
    lane_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    # Indexed because per-racer history is the central join for feature
    # building, and PostgreSQL does not index foreign keys automatically.
    racer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("racers.id"), nullable=False, index=True
    )

    listed_class: Mapped[str | None] = mapped_column(String(10))
    listed_age: Mapped[int | None] = mapped_column(SmallInteger)
    listed_branch: Mapped[str | None] = mapped_column(String(20))
    listed_weight: Mapped[int | None] = mapped_column(SmallInteger)
    listed_national_win_rate: Mapped[float | None] = mapped_column(RATE)
    listed_national_second_rate: Mapped[float | None] = mapped_column(RATE)
    listed_local_win_rate: Mapped[float | None] = mapped_column(RATE)
    listed_local_second_rate: Mapped[float | None] = mapped_column(RATE)
    # Not printed in the B-file; a fan-file join could fill it later.
    listed_average_st: Mapped[float | None] = mapped_column(Numeric(4, 2))
    listed_motor_number: Mapped[int | None] = mapped_column(SmallInteger)
    listed_motor_second_rate: Mapped[float | None] = mapped_column(RATE)
    listed_boat_number: Mapped[int | None] = mapped_column(SmallInteger)
    listed_boat_second_rate: Mapped[float | None] = mapped_column(RATE)
    # `bfile_parser.RaceEntryCard.trailing_info_raw`: current-series
    # per-heat results plus an early-start indicator, packed with no
    # reliable internal boundary. Stored raw rather than guess-split
    # (rule 08: keep raw separate from derived).
    listed_series_form_raw: Mapped[str | None] = mapped_column(String(20))

    # NULL: the B-file archive does not record when each file was
    # published, only which race day it covers.
    source_published_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("data_sources.id"))

    race: Mapped[Race] = relationship(back_populates="entries")
    racer: Mapped[Racer] = relationship()
    # ORM-level cascade as well as the DB-level one, so replacing a day's
    # entries behaves identically on PostgreSQL and on the SQLite the
    # tests run against (SQLite ignores ON DELETE unless the connection
    # enables `PRAGMA foreign_keys`).
    exhibition: Mapped[ExhibitionEntry | None] = relationship(
        back_populates="race_entry", cascade="all, delete-orphan", uselist=False
    )


class RaceResult(TimestampMixin, Base):
    __tablename__ = "race_results"
    __table_args__ = (UniqueConstraint("race_id"),)

    id: Mapped[uuid.UUID] = _pk()
    race_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("races.id", ondelete="CASCADE"), nullable=False
    )
    # The K-file states no per-race confirmation time, so this is the
    # conservative day-level bound the loader derives, not a published
    # timestamp. See `loader.results_available_at`.
    confirmed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    # 決まり手; present in the K-file but not extracted by the current
    # parser, so never written yet.
    winning_method: Mapped[str | None] = mapped_column(String(20))
    is_refund: Mapped[bool | None] = mapped_column(Boolean)
    available_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("data_sources.id"))

    race: Mapped[Race] = relationship()
    entries: Mapped[list[RaceResultEntry]] = relationship(
        back_populates="result", cascade="all, delete-orphan"
    )
    payouts: Mapped[list[RacePayout]] = relationship(
        back_populates="result", cascade="all, delete-orphan"
    )


class RaceResultEntry(TimestampMixin, Base):
    """Per-boat outcome.

    `race_entry_id` is nullable on purpose: a K-file race can have no
    matching B-file card when that venue's card was published as
    `data_pending`. Making the link mandatory would either drop real
    results or force a fabricated entry row, so the lane number is
    stored here directly and the link is filled when it exists.
    """

    __tablename__ = "race_result_entries"
    __table_args__ = (
        UniqueConstraint("race_result_id", "lane_number"),
        CheckConstraint("lane_number BETWEEN 1 AND 6", name="lane_number_range"),
    )

    id: Mapped[uuid.UUID] = _pk()
    race_result_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("race_results.id", ondelete="CASCADE"), nullable=False
    )
    # SET NULL, not CASCADE: re-loading a B-file day replaces that day's
    # `race_entries`, and a real result must survive its card being
    # rewritten. The lane number below keeps the row interpretable in the
    # window before `loader` re-links it.
    race_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("race_entries.id", ondelete="SET NULL")
    )
    lane_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    racer_registration_number: Mapped[int | None] = mapped_column(Integer)
    actual_course: Mapped[int | None] = mapped_column(SmallInteger)
    actual_st_sec: Mapped[float | None] = mapped_column(Numeric(4, 2))
    # NULL for every non-numeric outcome (disqualification, absence,
    # false start); `status` then carries the raw code (S0/S1/K0/F/L0/L1
    # and friends, all observed across the 2005-2026 archive).
    finish_position: Mapped[int | None] = mapped_column(SmallInteger)
    status: Mapped[str | None] = mapped_column(String(10))
    race_time_raw: Mapped[str | None] = mapped_column(String(20))

    result: Mapped[RaceResult] = relationship(back_populates="entries")


class RacePayout(TimestampMixin, Base):
    """Realized payouts per bet type -- see deviation 3.

    `paper_simulation.py` settles bets against these instead of assumed
    returns. `combination` is kept as the file's own string (e.g.
    "1-3") rather than parsed into lanes, since bet types differ in
    arity and 拡連複 rows are unordered pairs.
    """

    __tablename__ = "race_payouts"
    __table_args__ = (UniqueConstraint("race_result_id", "bet_type", "combination"),)

    id: Mapped[uuid.UUID] = _pk()
    race_result_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("race_results.id", ondelete="CASCADE"), nullable=False
    )
    bet_type: Mapped[str] = mapped_column(String(20), nullable=False)
    combination: Mapped[str] = mapped_column(String(20), nullable=False)
    payout_yen: Mapped[int | None] = mapped_column(Integer)
    popularity_rank: Mapped[int | None] = mapped_column(SmallInteger)

    result: Mapped[RaceResult] = relationship(back_populates="payouts")


class ExhibitionEntry(TimestampMixin, Base):
    """Exhibition-run measurements -- see deviation 5.

    Values loaded from the K-file carry results-time `available_at`, so
    `feature_availability.py` will refuse them for a pre-race
    `prediction_at`. That is correct, not a bug: this project has no
    pre-race source for them yet.
    """

    __tablename__ = "exhibition_entries"
    __table_args__ = (UniqueConstraint("race_entry_id"),)

    id: Mapped[uuid.UUID] = _pk()
    race_entry_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("race_entries.id", ondelete="CASCADE"), nullable=False
    )
    exhibition_course: Mapped[int | None] = mapped_column(SmallInteger)
    exhibition_time_sec: Mapped[float | None] = mapped_column(Numeric(4, 2))
    exhibition_st_sec: Mapped[float | None] = mapped_column(Numeric(4, 2))
    tilt_angle: Mapped[float | None] = mapped_column(Numeric(3, 1))
    observed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("data_sources.id"))

    race_entry: Mapped[RaceEntry] = relationship(back_populates="exhibition")


class OddsSnapshot(TimestampMixin, Base):
    """Market odds observations.

    Defined now so the schema is complete, but not written by the B/K
    loader. `odds_source.py` retains exactly one observation per race
    (the closing odds), so historically `observed_at` will always equal
    the deadline -- see tasks/HANDOFF.md for why that rules out
    pre-race EV screening on historical data.
    """

    __tablename__ = "odds_snapshots"
    __table_args__ = (UniqueConstraint("race_id", "bet_type", "combination", "observed_at"),)

    id: Mapped[uuid.UUID] = _pk()
    race_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("races.id", ondelete="CASCADE"), nullable=False
    )
    bet_type: Mapped[str] = mapped_column(String(20), nullable=False)
    combination: Mapped[str] = mapped_column(String(20), nullable=False)
    odds: Mapped[float] = mapped_column(Numeric(8, 2), nullable=False)
    observed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    available_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_closing: Mapped[bool | None] = mapped_column(Boolean)
    source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("data_sources.id"))


class WeatherObservation(TimestampMixin, Base):
    """One venue's JMA daily weather summary (`jma_weather_source.py`).

    Keyed by `(venue_id, weather_date)`, not by race: JMA publishes one
    summary per station per day, shared by every race at that venue that
    day. Column set mirrors `jma_weather_source.DailyWeather` field for
    field; this class has a different name to avoid confusion with that
    source-side dataclass when both are imported together.
    """

    __tablename__ = "weather_observations"
    __table_args__ = (UniqueConstraint("venue_id", "weather_date"),)

    id: Mapped[uuid.UUID] = _pk()
    venue_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("venues.id"), nullable=False, index=True
    )
    weather_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    precipitation_total_mm: Mapped[float | None] = mapped_column(Numeric(6, 1))
    precipitation_max_1h_mm: Mapped[float | None] = mapped_column(Numeric(5, 1))
    precipitation_max_10min_mm: Mapped[float | None] = mapped_column(Numeric(5, 1))
    temperature_avg_c: Mapped[float | None] = mapped_column(Numeric(4, 1))
    temperature_max_c: Mapped[float | None] = mapped_column(Numeric(4, 1))
    temperature_min_c: Mapped[float | None] = mapped_column(Numeric(4, 1))
    humidity_avg_pct: Mapped[float | None] = mapped_column(Numeric(4, 1))
    humidity_min_pct: Mapped[float | None] = mapped_column(Numeric(4, 1))
    wind_avg_ms: Mapped[float | None] = mapped_column(Numeric(4, 1))
    wind_max_ms: Mapped[float | None] = mapped_column(Numeric(4, 1))
    wind_max_direction: Mapped[str | None] = mapped_column(String(10))
    wind_max_instant_ms: Mapped[float | None] = mapped_column(Numeric(4, 1))
    wind_max_instant_direction: Mapped[str | None] = mapped_column(String(10))
    wind_prevailing_direction: Mapped[str | None] = mapped_column(String(10))
    sunshine_hours: Mapped[float | None] = mapped_column(Numeric(4, 1))
    # See loader.weather_available_at: same day-after-midnight-JST
    # conservative bound as results_available_at, for the same reason --
    # JMA states no per-observation publication timestamp for this page.
    available_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("data_sources.id"))


class RacerPeriodStats(TimestampMixin, Base):
    """One racer's 期別 statistics for one application period.

    Source: the モーターボートファン手帳 fixed-width records parsed by
    `fan_stats_parser.py`. Point-in-time by construction -- see deviation
    7. Only the 403-character layout (2014 onward) is parseable, so no
    row exists for an application period before 2014-2.

    `period_year`/`period_number` are the **application** period, not the
    window the statistics were computed over; the two differ by about
    eight months and conflating them would be a leak. Measured across all
    25 parseable files, with no exceptions:

        number 1  <- rated 05-01..10-31, applies from period_year-01-01
        number 2  <- rated 11-01..04-30, applies from period_year-07-01

    `period_from`/`period_to` keep the rating window the file states, so
    the distinction stays visible in the data rather than living only in
    this docstring.
    """

    __tablename__ = "racer_period_stats"
    __table_args__ = (UniqueConstraint("racer_id", "period_year", "period_number"),)

    id: Mapped[uuid.UUID] = _pk()
    racer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("racers.id"), nullable=False, index=True
    )
    period_year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    period_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    period_from: Mapped[dt.date | None] = mapped_column(Date)
    period_to: Mapped[dt.date | None] = mapped_column(Date)

    racer_class: Mapped[str | None] = mapped_column(String(5))
    prev_class: Mapped[str | None] = mapped_column(String(5))
    prev2_class: Mapped[str | None] = mapped_column(String(5))
    prev3_class: Mapped[str | None] = mapped_column(String(5))
    prev_ability_index: Mapped[float | None] = mapped_column(Numeric(5, 2))
    current_ability_index: Mapped[float | None] = mapped_column(Numeric(5, 2))

    win_rate: Mapped[float | None] = mapped_column(Numeric(4, 2))
    place_rate: Mapped[float | None] = mapped_column(Numeric(5, 2))
    first_place_count: Mapped[int | None] = mapped_column(Integer)
    second_place_count: Mapped[int | None] = mapped_column(Integer)
    start_count: Mapped[int | None] = mapped_column(Integer)
    championship_appearance_count: Mapped[int | None] = mapped_column(Integer)
    championship_win_count: Mapped[int | None] = mapped_column(Integer)
    avg_start_timing: Mapped[float | None] = mapped_column(Numeric(4, 2))

    age: Mapped[int | None] = mapped_column(SmallInteger)
    height_cm: Mapped[int | None] = mapped_column(SmallInteger)
    weight_kg: Mapped[int | None] = mapped_column(SmallInteger)
    blood_type: Mapped[str | None] = mapped_column(String(4))
    branch: Mapped[str | None] = mapped_column(String(20))
    hometown: Mapped[str | None] = mapped_column(String(20))

    # Irregular finishes the file records without attributing a course.
    no_course_l0_count: Mapped[int | None] = mapped_column(Integer)
    no_course_l1_count: Mapped[int | None] = mapped_column(Integer)
    no_course_k0_count: Mapped[int | None] = mapped_column(Integer)
    no_course_k1_count: Mapped[int | None] = mapped_column(Integer)

    # See loader.fan_stats_available_at: midnight JST at the start of the
    # application period. Later than the file's actual publication (the
    # filename dates it to just after the rating window closes), which is
    # the safe direction, and it lines up with the boundary at which the
    # B-file starts printing the new class.
    available_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("data_sources.id"))

    courses: Mapped[list[RacerPeriodCourseStats]] = relationship(
        back_populates="period_stats", cascade="all, delete-orphan"
    )


class RacerPeriodCourseStats(TimestampMixin, Base):
    """One course's (1-6) statistics within a `RacerPeriodStats` row.

    A separate narrow table rather than 6 x 18 columns on the parent: the
    natural key really is `(racer, period, course)`, six rows read better
    than 108 columns, and a per-course feature lookup becomes a plain
    filter instead of dynamic column names.

    Materialized at all -- including the full finish-position breakdown,
    which an earlier note judged unlikely to be used -- because per-course
    ability was measured to be the strongest racer attribute found beyond
    overall skill (persistence 0.49 against a 0.78 control, and 0.58/0.56
    at courses 1 and 6; see tasks/HANDOFF.md, 2026-08-01). The breakdown
    is what makes 2連率/3連率 per course derivable, which is what P3's
    exacta work needs.

    Note this is keyed by **course** (進入), not by lane (枠). They differ
    whenever 進入変更 occurs, and the only pre-race observation of the
    actual course is 直前情報's start exhibition, which is not yet
    captured -- so joining these stats on lane number is an approximation
    that a caller must make knowingly.
    """

    __tablename__ = "racer_period_course_stats"
    __table_args__ = (UniqueConstraint("racer_period_stats_id", "course_number"),)

    id: Mapped[uuid.UUID] = _pk()
    racer_period_stats_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("racer_period_stats.id", ondelete="CASCADE"), nullable=False, index=True
    )
    course_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    entry_count: Mapped[int | None] = mapped_column(Integer)
    place_rate: Mapped[float | None] = mapped_column(Numeric(5, 2))
    avg_start_timing: Mapped[float | None] = mapped_column(Numeric(4, 2))
    avg_start_rank: Mapped[float | None] = mapped_column(Numeric(4, 2))

    finish_1_count: Mapped[int | None] = mapped_column(Integer)
    finish_2_count: Mapped[int | None] = mapped_column(Integer)
    finish_3_count: Mapped[int | None] = mapped_column(Integer)
    finish_4_count: Mapped[int | None] = mapped_column(Integer)
    finish_5_count: Mapped[int | None] = mapped_column(Integer)
    finish_6_count: Mapped[int | None] = mapped_column(Integer)

    f_count: Mapped[int | None] = mapped_column(Integer)
    l0_count: Mapped[int | None] = mapped_column(Integer)
    l1_count: Mapped[int | None] = mapped_column(Integer)
    k0_count: Mapped[int | None] = mapped_column(Integer)
    k1_count: Mapped[int | None] = mapped_column(Integer)
    s0_count: Mapped[int | None] = mapped_column(Integer)
    s1_count: Mapped[int | None] = mapped_column(Integer)
    s2_count: Mapped[int | None] = mapped_column(Integer)

    period_stats: Mapped[RacerPeriodStats] = relationship(back_populates="courses")


class RacePrediction(TimestampMixin, Base):
    """A model's first-place probability for one lane, recorded *before*
    the race, prospectively.

    This table exists because of what the payout-settled P2 run found
    (tasks/HANDOFF.md, 2026-08-01): the model's accuracy edge is real and
    the market has already priced it, so the only untested route to a
    positive return is selecting on *price*, which needs a probability
    and a pre-deadline quote that provably existed at the same moment.
    The odds side has been captured by cron since 2026-08-01; this is the
    other half.

    Reconstructing predictions later from stored results would not do:
    the model would be chosen with hindsight and the record would be a
    backtest wearing a forward test's clothes. Hence written live, and
    hence `model_version` on every row -- a prediction whose producer is
    unknown cannot be evaluated.

    **Probabilities, not decisions.** Nothing here records "bet" or "do
    not bet". No rule measured so far is positive-EV, so committing to
    one would freeze a policy this project does not believe in, and every
    later change of policy would invalidate the accumulated record.
    Storing the inputs instead leaves the decision rule free to change
    while the evidence keeps accruing.

    `predicted_at` is when the model ran; `features_available_at` is the
    latest `available_at` among the features it consumed. Both are kept
    so a leakage check is a query rather than an assurance -- the pair
    must satisfy `features_available_at <= predicted_at <=
    races.scheduled_deadline_at`.
    """

    __tablename__ = "race_predictions"
    __table_args__ = (
        UniqueConstraint("race_id", "lane_number", "model_version", "predicted_at"),
        CheckConstraint(
            "win_probability >= 0 AND win_probability <= 1",
            name="win_probability_is_a_probability",
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    race_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("races.id", ondelete="CASCADE"), nullable=False, index=True
    )
    lane_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    win_probability: Mapped[float] = mapped_column(Numeric(8, 6), nullable=False)
    predicted_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    features_available_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True)
    )


class BeforeInfoEntry(TimestampMixin, Base):
    """One boat's 直前情報 (`beforeinfo_source.py`), captured before the race.

    Distinct from `exhibition_entries` on purpose, and the difference is
    the whole reason this table exists. That table holds the exhibition
    time as read from the *post-race* K-file, so its `available_at` is
    results-time and `feature_availability.py` correctly refuses it for
    any pre-race prediction (deviation 5). These rows are fetched from the
    live 直前情報 page before the deadline, so their `available_at` is the
    fetch itself and they are genuinely usable as features.

    Do not merge the two. A single table would have to carry one
    availability semantics for rows that honestly have two.

    `start_exhibition_course` is the reason this matters most: 進入 can
    differ from the lane, the per-course racer statistics in
    `racer_period_course_stats` are keyed by *course*, and this is the
    only pre-race observation of which course a boat actually took.

    `parts_replaced` keeps the page's own part names, comma-joined. No
    mapping to a code list has been verified, so the raw text is stored
    rather than a guess.
    """

    __tablename__ = "before_info_entries"
    __table_args__ = (UniqueConstraint("race_id", "lane_number", "observed_at"),)

    id: Mapped[uuid.UUID] = _pk()
    race_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("races.id", ondelete="CASCADE"), nullable=False, index=True
    )
    lane_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    weight_kg: Mapped[float | None] = mapped_column(Numeric(4, 1))
    adjustment_weight_kg: Mapped[float | None] = mapped_column(Numeric(4, 1))
    exhibition_time_sec: Mapped[float | None] = mapped_column(Numeric(4, 2))
    tilt_angle: Mapped[float | None] = mapped_column(Numeric(3, 1))
    propeller_changed: Mapped[bool | None] = mapped_column(Boolean)
    parts_replaced: Mapped[str | None] = mapped_column(Text)

    start_exhibition_course: Mapped[int | None] = mapped_column(SmallInteger)
    start_exhibition_st_sec: Mapped[float | None] = mapped_column(Numeric(4, 2))
    start_exhibition_is_flying: Mapped[bool | None] = mapped_column(Boolean)

    observed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    available_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("data_sources.id"))


class RaceSurfaceCondition(TimestampMixin, Base):
    """Water-surface conditions from the same 直前情報 page.

    Race-level rather than per-boat, and kept in its own table because the
    page reports one reading shared by every boat.

    **The label is stored, not just the numbers, and that is a leakage
    control.** The page states its observation point in one of two forms:
    `"NR時点"` (observed at race N) or `"HH:MM現在"` (a wall clock). Fetched
    from the archive the second form is the *day's latest* reading and
    using it would feed an early race hours of future weather --
    `beforeinfo_source.SurfaceWeather.is_safe_for_race` rejects it for
    exactly that reason. A live capture is different: the fetch happened
    before this race's deadline, so `observed_at` proves the reading
    existed in time whatever the label says. `raw_label` and
    `reference_race_number` are preserved so a later analysis can tell a
    live row from a backfilled one and apply the right rule to each.
    """

    __tablename__ = "race_surface_conditions"
    __table_args__ = (UniqueConstraint("race_id", "observed_at"),)

    id: Mapped[uuid.UUID] = _pk()
    race_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("races.id", ondelete="CASCADE"), nullable=False, index=True
    )
    raw_label: Mapped[str | None] = mapped_column(String(40))
    reference_race_number: Mapped[int | None] = mapped_column(SmallInteger)
    air_temperature_c: Mapped[float | None] = mapped_column(Numeric(4, 1))
    water_temperature_c: Mapped[float | None] = mapped_column(Numeric(4, 1))
    wind_speed_ms: Mapped[float | None] = mapped_column(Numeric(4, 1))
    wind_direction_code: Mapped[int | None] = mapped_column(SmallInteger)
    wave_height_cm: Mapped[float | None] = mapped_column(Numeric(5, 1))
    weather_text: Mapped[str | None] = mapped_column(String(20))
    weather_icon_code: Mapped[int | None] = mapped_column(SmallInteger)

    observed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    available_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("data_sources.id"))
