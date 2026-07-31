"""Load parsed B-file and K-file records into the relational schema.

This is the piece that turns `bfile_parser.ParsedVenueDayCard` /
`kfile_parser.ParsedVenueDay` trees into `races` / `race_entries` /
`race_results` rows, and it is where the temporal semantics are decided
-- see `card_available_at` and `results_available_at`, which are the two
functions any leakage argument about this project ultimately rests on.

Both loaders are idempotent per `(race_date, venue_code)`: re-running a
day updates the same natural-key rows and fully replaces that day's
entries rather than accumulating duplicates, so a partial or
interrupted archive load is fixed by re-running it.

The two card-less venue cases found by validating the parsers across the
whole 2005-2026 archive (tasks/HANDOFF.md) are handled explicitly rather
than counted as missing data: `data_pending` and `is_cancelled` venues
are skipped and reported, and a card-less venue with *neither* flag is
raised as `LoaderError`, because that combination is the signature of a
parse defect.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..bfile_parser import ParsedVenueDayCard
from ..jma_weather_source import DailyWeather
from ..kfile_parser import ParsedVenueDay
from ..odds_source import RaceOdds
from ..race_id import VALID_VENUE_CODES
from ..temporal import to_utc
from .meeting_resolution import continues_meeting, resolve_new_meeting_start
from .models import (
    RACE_STATUS_CANCELLED,
    RACE_STATUS_FINISHED,
    RACE_STATUS_SCHEDULED,
    VENUE_NAMES,
    DataSource,
    ExhibitionEntry,
    OddsSnapshot,
    Race,
    RaceEntry,
    RaceMeeting,
    RacePayout,
    Racer,
    RaceResult,
    RaceResultEntry,
    Venue,
    WeatherObservation,
)

JST = ZoneInfo("Asia/Tokyo")

SOURCE_B_FILE = "boatrace_b_file"
SOURCE_K_FILE = "boatrace_k_file"
SOURCE_ODDS = "boatrace_odds"
SOURCE_JMA_WEATHER = "jma_weather"

_FIXED_ENTRY_MARKER = "進入固定"


class LoaderError(ValueError):
    """Raised when input cannot be loaded without inventing or losing data."""


@dataclass
class LoadStats:
    """What a load actually did, so a bulk run can be audited rather than
    trusted. Counted, not logged, because the archive run processes
    ~7,800 files and per-file logging would be unreadable."""

    races: int = 0
    entries: int = 0
    results: int = 0
    result_entries: int = 0
    payouts: int = 0
    venues_data_pending: int = 0
    venues_cancelled: int = 0
    races_cancelled: int = 0

    def merge(self, other: LoadStats) -> LoadStats:
        return LoadStats(
            races=self.races + other.races,
            entries=self.entries + other.entries,
            results=self.results + other.results,
            result_entries=self.result_entries + other.result_entries,
            payouts=self.payouts + other.payouts,
            venues_data_pending=self.venues_data_pending + other.venues_data_pending,
            venues_cancelled=self.venues_cancelled + other.venues_cancelled,
            races_cancelled=self.races_cancelled + other.races_cancelled,
        )


# --------------------------------------------------------------------------
# Temporal semantics
# --------------------------------------------------------------------------


def card_available_at(race_date: dt.date) -> dt.datetime:
    """When a B-file race card is treated as available: midnight JST on
    the race day, in UTC.

    The archive records which race day a file covers but not when it was
    published, and cards are in fact published the day before. Midnight
    of the race day is therefore *later* than the true publication time,
    which is the safe direction to be wrong in: a feature can only ever
    be considered available later than it really was, never earlier. It
    is also early enough that every pre-deadline `prediction_at` on the
    race day still sees the card.
    """
    return to_utc(dt.datetime.combine(race_date, dt.time(0, 0), tzinfo=JST))


def results_available_at(race_date: dt.date) -> dt.datetime:
    """When K-file results are treated as available: midnight JST on the
    day *after* the race, in UTC.

    Individual results are published within minutes of each race in
    reality, but the K-file carries no per-race confirmation timestamp,
    so the only bound derivable from the data is the day boundary. Taking
    the later bound guarantees no result can leak into a same-day
    prediction. The cost is that genuinely legal same-day features (using
    race 1's result when predicting race 12) are unavailable; recovering
    those needs per-race confirmation times this project does not have.
    """
    return to_utc(dt.datetime.combine(race_date + dt.timedelta(days=1), dt.time(0, 0), tzinfo=JST))


def weather_available_at(weather_date: dt.date) -> dt.datetime:
    """When a JMA daily weather summary is treated as available: midnight
    JST on the day *after* the observation date, in UTC.

    Same reasoning and same bound as `results_available_at`: the fetched
    page carries no per-observation publication timestamp, only which
    calendar day it summarizes, so the day boundary is the only bound
    derivable from the data -- and the conservative (later) direction is
    the safe one for a leakage check.
    """
    return to_utc(dt.datetime.combine(weather_date + dt.timedelta(days=1), dt.time(0, 0), tzinfo=JST))


def scheduled_deadline_at(race_date: dt.date, deadline_time: str) -> dt.datetime:
    """Combine the race date with the B-file header's 締切予定 "HH:MM"."""
    hour_raw, _, minute_raw = deadline_time.partition(":")
    try:
        clock = dt.time(int(hour_raw), int(minute_raw))
    except ValueError as exc:
        raise LoaderError(f"unparsable deadline time: {deadline_time!r}") from exc
    return to_utc(dt.datetime.combine(race_date, clock, tzinfo=JST))


# --------------------------------------------------------------------------
# Reference data
# --------------------------------------------------------------------------

_DATA_SOURCE_SEED = (
    {
        "code": SOURCE_B_FILE,
        "name": "BOATRACE 番組表 (B-file)",
        "provider": "一般財団法人BOATRACE振興会",
        "source_type": "official_download",
        "official_url": "https://www1.mbrace.or.jp/od2/B/",
        "terms_url": "https://www1.mbrace.or.jp/",
        "acquisition_method": "scheduled_download",
        "update_frequency": "daily",
        "license_note": "Official downloadable file; robots.txt has no disallow rules. "
        "Rate-limited fetch, not redistributed in this repository.",
    },
    {
        "code": SOURCE_K_FILE,
        "name": "BOATRACE 競走成績 (K-file)",
        "provider": "一般財団法人BOATRACE振興会",
        "source_type": "official_download",
        "official_url": "https://www1.mbrace.or.jp/od2/K/",
        "terms_url": "https://www1.mbrace.or.jp/",
        "acquisition_method": "scheduled_download",
        "update_frequency": "daily",
        "license_note": "Official downloadable file; robots.txt has no disallow rules. "
        "Rate-limited fetch, not redistributed in this repository.",
    },
    {
        "code": SOURCE_ODDS,
        "name": "BOATRACE 締切時オッズ (win/place)",
        "provider": "一般財団法人BOATRACE振興会",
        "source_type": "official_website",
        "official_url": "https://www.boatrace.jp/owpc/pc/race/oddstf",
        "terms_url": "https://www.boatrace.jp/owpc/pc/extra/policy.html",
        "acquisition_method": "scraped_with_approval",
        "update_frequency": "per_race",
        "license_note": "owpc/pc/extra/policy.html prohibits large-volume access and "
        "redistribution; fetched only after explicit in-session approval, "
        "rate-limited, not redistributed in this repository.",
    },
    {
        "code": SOURCE_JMA_WEATHER,
        "name": "気象庁 過去の気象データ (daily, per venue's nearest station)",
        "provider": "気象庁",
        "source_type": "official_website",
        "official_url": "https://www.data.jma.go.jp/stats/etrn/",
        "terms_url": "https://www.jma.go.jp/jma/kishou/info/coment.html",
        "acquisition_method": "scraped_open_data",
        "update_frequency": "daily",
        "license_note": "公共データ利用規約（第1.0版）: reuse including commercial use "
        "allowed with attribution. Rate-limited fetch, not redistributed in "
        "this repository.",
    },
)


def ensure_reference_data(session: Session) -> None:
    """Seed `venues` and `data_sources`. Idempotent: safe to call before
    every load rather than requiring a separate bootstrap step."""
    existing_venues = {code for (code,) in session.execute(select(Venue.code))}
    for code, name in sorted(VENUE_NAMES.items()):
        if code not in existing_venues:
            session.add(Venue(code=code, name=name))

    existing_sources = {code for (code,) in session.execute(select(DataSource.code))}
    for seed in _DATA_SOURCE_SEED:
        if seed["code"] not in existing_sources:
            session.add(DataSource(**seed))
    session.flush()


def _venue(session: Session, venue_code: str) -> Venue:
    if venue_code not in VALID_VENUE_CODES:
        raise LoaderError(f"unknown venue_code: {venue_code!r}")
    venue = session.scalar(select(Venue).where(Venue.code == venue_code))
    if venue is None:
        raise LoaderError(f"venue {venue_code} missing; call ensure_reference_data() first")
    return venue


def _source_id(session: Session, code: str):
    return session.scalar(select(DataSource.id).where(DataSource.code == code))


def _resolve_racers(session: Session, cards: list[tuple[int, str]]) -> dict[int, Racer]:
    """Get-or-create `racers` for the (registration_number, name) pairs in
    one venue-day, in two queries rather than one per entry."""
    numbers = {number for number, _ in cards}
    if not numbers:
        return {}
    found = {
        racer.registration_number: racer
        for racer in session.scalars(
            select(Racer).where(Racer.registration_number.in_(numbers))
        )
    }
    for number, name in cards:
        if number not in found:
            racer = Racer(registration_number=number, name=name)
            session.add(racer)
            found[number] = racer
    session.flush()
    return found


def _previous_meeting(
    session: Session, venue: Venue, race_date: dt.date
) -> tuple[RaceMeeting, dt.date] | None:
    """The venue's most recently raced meeting before `race_date`, with
    the date of its last loaded race day."""
    row = session.execute(
        select(RaceMeeting, func.max(Race.race_date).label("last_race_date"))
        .join(Race, Race.meeting_id == RaceMeeting.id)
        .where(RaceMeeting.venue_id == venue.id, Race.race_date < race_date)
        .group_by(RaceMeeting.id)
        .order_by(func.max(Race.race_date).desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    meeting, last_race_date = row
    if isinstance(last_race_date, str):  # SQLite returns DATE as text
        last_race_date = dt.date.fromisoformat(last_race_date)
    return meeting, last_race_date


def _taken_meeting_starts(session: Session, venue: Venue) -> set[dt.date]:
    return set(
        session.scalars(
            select(RaceMeeting.meeting_start_date).where(RaceMeeting.venue_id == venue.id)
        )
    )


def _get_or_create_meeting(
    session: Session, venue: Venue, race_date: dt.date, card: ParsedVenueDayCard
) -> RaceMeeting | None:
    """Attach one race day to its 節.

    Not `race_date - (series_day - 1)`: see `meeting_resolution` for why
    that arithmetic fragments 3% of 節.
    """
    if card.series_day is None:
        return None

    # Re-loading a day keeps the meeting its races already point at, so a
    # re-run never re-groups days that a previous load (or a
    # `rebuild_meetings` pass) already resolved.
    existing = session.scalar(
        select(RaceMeeting)
        .join(Race, Race.meeting_id == RaceMeeting.id)
        .where(Race.venue_id == venue.id, Race.race_date == race_date)
        .limit(1)
    )
    meeting = existing
    if meeting is None:
        previous = _previous_meeting(session, venue, race_date)
        if previous is not None and continues_meeting(
            card.series_day, race_date, previous[1]
        ):
            meeting = previous[0]

    if meeting is None:
        meeting = RaceMeeting(
            venue_id=venue.id,
            meeting_start_date=resolve_new_meeting_start(
                race_date, card.series_day, _taken_meeting_starts(session, venue)
            ),
            meeting_title=card.meeting_title,
            source_id=_source_id(session, SOURCE_B_FILE),
        )
        session.add(meeting)
        session.flush()
    elif meeting.meeting_title is None and card.meeting_title is not None:
        meeting.meeting_title = card.meeting_title
    return meeting


def _get_or_create_race(
    session: Session, venue: Venue, race_date: dt.date, race_number: int
) -> tuple[Race, bool]:
    race = session.scalar(
        select(Race).where(
            Race.race_date == race_date,
            Race.venue_id == venue.id,
            Race.race_number == race_number,
        )
    )
    if race is not None:
        return race, False
    race = Race(
        venue_id=venue.id,
        race_date=race_date,
        race_number=race_number,
        status=RACE_STATUS_SCHEDULED,
    )
    session.add(race)
    session.flush()
    return race, True


def _clear_race_entries(session: Session, race: Race) -> None:
    """Remove a race's existing `race_entries` before re-inserting them.

    Deletes through the ORM rather than with a `DELETE` statement so the
    configured cascades actually run on every backend: SQLite ignores
    `ON DELETE` clauses unless the connection turns on
    `PRAGMA foreign_keys`, so relying on the DB alone would leave orphan
    `exhibition_entries` in tests while working in production -- the
    worst possible split. Result rows are detached first (their FK is
    SET NULL by design) and re-linked afterwards by
    `_relink_result_entries`.
    """
    entry_ids = select(RaceEntry.id).where(RaceEntry.race_id == race.id)
    session.execute(
        update(RaceResultEntry)
        .where(RaceResultEntry.race_entry_id.in_(entry_ids))
        .values(race_entry_id=None)
    )
    for entry in session.scalars(select(RaceEntry).where(RaceEntry.race_id == race.id)):
        session.delete(entry)
    session.flush()


def _clear_race_result(session: Session, race: Race) -> None:
    """Remove a race's existing result, cascading to its result entries
    and payouts (ORM delete, for the reason in `_clear_race_entries`)."""
    existing = session.scalar(select(RaceResult).where(RaceResult.race_id == race.id))
    if existing is not None:
        session.delete(existing)
        session.flush()


def _relink_result_entries(session: Session, race: Race) -> None:
    """Point this race's `race_result_entries` at its current
    `race_entries` by lane.

    Needed because loading in either order must converge: results loaded
    before the card have nothing to link to, and re-loading a card
    replaces the entry rows the results were linked to (the FK is
    SET NULL for exactly that reason).
    """
    result_id = session.scalar(select(RaceResult.id).where(RaceResult.race_id == race.id))
    if result_id is None:
        return
    entry_by_lane = {
        lane: entry_id
        for entry_id, lane in session.execute(
            select(RaceEntry.id, RaceEntry.lane_number).where(RaceEntry.race_id == race.id)
        )
    }
    if not entry_by_lane:
        return
    for result_entry in session.scalars(
        select(RaceResultEntry).where(RaceResultEntry.race_result_id == result_id)
    ):
        result_entry.race_entry_id = entry_by_lane.get(result_entry.lane_number)


# --------------------------------------------------------------------------
# B-file (race cards)
# --------------------------------------------------------------------------


def load_b_file_day(
    session: Session, race_date: dt.date, venue_cards: list[ParsedVenueDayCard]
) -> LoadStats:
    """Load one day's parsed B-file into `races` / `race_entries`."""
    stats = LoadStats()
    source_id = _source_id(session, SOURCE_B_FILE)
    available_at = card_available_at(race_date)

    for card in venue_cards:
        if not card.races:
            if card.data_pending:
                stats.venues_data_pending += 1
                continue
            if card.is_cancelled:
                stats.venues_cancelled += 1
                continue
            raise LoaderError(
                f"venue {card.venue_code} on {race_date} has no races and no "
                "data_pending/is_cancelled flag, which indicates a parse defect"
            )
        if card.meeting_date is not None and card.meeting_date != race_date:
            raise LoaderError(
                f"venue {card.venue_code}: day banner date {card.meeting_date} "
                f"disagrees with the file's date {race_date}"
            )

        venue = _venue(session, card.venue_code)
        meeting = _get_or_create_meeting(session, venue, race_date, card)

        racers = _resolve_racers(
            session,
            [
                (entry.racer_registration_number, entry.racer_name)
                for parsed_race in card.races
                for entry in parsed_race.entries
            ],
        )

        for parsed_race in card.races:
            race, _ = _get_or_create_race(session, venue, race_date, parsed_race.race_number)
            race.meeting_id = meeting.id if meeting is not None else None
            race.distance_meters = parsed_race.distance_meters
            race.scheduled_deadline_at = scheduled_deadline_at(
                race_date, parsed_race.scheduled_deadline_time
            )
            race.race_class_label = parsed_race.race_class_label
            race.race_class = parsed_race.race_class
            race.is_fixed_entry = _FIXED_ENTRY_MARKER in parsed_race.race_class
            race.series_day = card.series_day
            stats.races += 1

            # Full replace, so a re-load repairs a partially written day
            # instead of layering a second set of entries beside it.
            _clear_race_entries(session, race)
            for entry in parsed_race.entries:
                session.add(
                    RaceEntry(
                        race_id=race.id,
                        lane_number=entry.lane_number,
                        racer_id=racers[entry.racer_registration_number].id,
                        listed_class=entry.racer_class,
                        listed_age=entry.age,
                        listed_branch=entry.branch,
                        listed_weight=entry.weight_kg,
                        listed_national_win_rate=entry.national_win_rate,
                        listed_national_second_rate=entry.national_second_rate,
                        listed_local_win_rate=entry.local_win_rate,
                        listed_local_second_rate=entry.local_second_rate,
                        listed_motor_number=entry.motor_number,
                        listed_motor_second_rate=entry.motor_second_rate,
                        listed_boat_number=entry.boat_number,
                        listed_boat_second_rate=entry.boat_second_rate,
                        listed_series_form_raw=entry.trailing_info_raw.strip() or None,
                        available_at=available_at,
                        source_id=source_id,
                    )
                )
                stats.entries += 1
            session.flush()
            _relink_result_entries(session, race)

    return stats


# --------------------------------------------------------------------------
# K-file (results)
# --------------------------------------------------------------------------


def load_k_file_day(
    session: Session, race_date: dt.date, venue_days: list[ParsedVenueDay]
) -> LoadStats:
    """Load one day's parsed K-file into `race_results` and friends.

    Creates the `races` row when the B-file for that day has not been
    loaded, so results are never dropped for want of a card; the row is
    then filled in by a later B-file load of the same day.
    """
    stats = LoadStats()
    source_id = _source_id(session, SOURCE_K_FILE)
    available_at = results_available_at(race_date)

    for venue_day in venue_days:
        venue = _venue(session, venue_day.venue_code)
        # A venue-day where *every* listed race is empty is a day that did
        # not run, written without the 中止 marker: the section carries the
        # 1R-12R payout table with no result at all and no race detail
        # block. Exactly one such day exists in the 2005-2026 archive
        # (venue 01, 2011-04-24, 第31回群馬テレビ杯 第1日). Treating it as
        # cancelled costs nothing, while raising loses a whole day's load.
        # A *single* empty race inside an otherwise populated day stays an
        # error, because that shape really would indicate a parse defect.
        day_produced_nothing = venue_day.races and all(
            not race.entries and not race.payouts for race in venue_day.races
        )

        for parsed_race in venue_day.races:
            race, _ = _get_or_create_race(session, venue, race_date, parsed_race.race_number)

            if parsed_race.is_cancelled or day_produced_nothing:
                race.status = RACE_STATUS_CANCELLED
                stats.races_cancelled += 1
                # A cancelled race has no result to record. Any result
                # rows from an earlier load of a since-corrected file are
                # removed so the two never disagree.
                _clear_race_result(session, race)
                continue

            if not parsed_race.entries and not parsed_race.payouts:
                raise LoaderError(
                    f"venue {venue_day.venue_code} race {parsed_race.race_number} on "
                    f"{race_date} has no entries, no payouts and no cancellation flag, "
                    "which indicates a parse defect"
                )

            race.status = RACE_STATUS_FINISHED

            _clear_race_result(session, race)
            result = RaceResult(
                race_id=race.id,
                confirmed_at=available_at,
                available_at=available_at,
                source_id=source_id,
            )
            session.add(result)
            session.flush()
            stats.results += 1

            entry_by_lane = {
                lane: entry_id
                for entry_id, lane in session.execute(
                    select(RaceEntry.id, RaceEntry.lane_number).where(RaceEntry.race_id == race.id)
                )
            }

            for entry in parsed_race.entries:
                race_entry_id = entry_by_lane.get(entry.lane_number)
                session.add(
                    RaceResultEntry(
                        race_result_id=result.id,
                        race_entry_id=race_entry_id,
                        lane_number=entry.lane_number,
                        racer_registration_number=entry.racer_registration_number,
                        actual_course=entry.entry_course,
                        actual_st_sec=entry.start_timing,
                        finish_position=entry.finish_position,
                        status=entry.finish_status_raw,
                        race_time_raw=entry.race_time,
                    )
                )
                stats.result_entries += 1

                if entry.exhibition_time is not None and race_entry_id is not None:
                    previous = session.scalar(
                        select(ExhibitionEntry).where(
                            ExhibitionEntry.race_entry_id == race_entry_id
                        )
                    )
                    if previous is not None:
                        session.delete(previous)
                        session.flush()
                    session.add(
                        ExhibitionEntry(
                            race_entry_id=race_entry_id,
                            exhibition_time_sec=entry.exhibition_time,
                            # Results-time availability, not pre-race --
                            # see models.ExhibitionEntry.
                            published_at=available_at,
                            available_at=available_at,
                            source_id=source_id,
                        )
                    )

            seen_payouts: set[tuple[str, str]] = set()
            for payout in parsed_race.payouts:
                key = (payout.bet_type, payout.combination)
                if key in seen_payouts:
                    # The unique constraint is per (bet_type, combination);
                    # 複勝/拡連複 legitimately print several rows per race,
                    # but a repeated identical pair would be a file quirk,
                    # so the first is kept rather than aborting the day.
                    continue
                seen_payouts.add(key)
                session.add(
                    RacePayout(
                        race_result_id=result.id,
                        bet_type=payout.bet_type,
                        combination=payout.combination,
                        payout_yen=payout.payout_yen,
                        popularity_rank=payout.popularity_rank,
                    )
                )
                stats.payouts += 1

    return stats


# --------------------------------------------------------------------------
# Odds (closing win/place odds)
# --------------------------------------------------------------------------


@dataclass
class OddsLoadStats:
    """What an odds load actually did. Kept separate from `LoadStats`
    because the skip reasons are specific to this source (non-closing
    page, no known observation time, race not yet loaded from B/K-file)
    rather than the card-less-venue cases those loaders handle."""

    snapshots: int = 0
    skipped_not_closing: int = 0
    skipped_no_deadline: int = 0
    skipped_race_not_found: int = 0
    skipped_missing_value: int = 0

    def merge(self, other: OddsLoadStats) -> OddsLoadStats:
        return OddsLoadStats(
            snapshots=self.snapshots + other.snapshots,
            skipped_not_closing=self.skipped_not_closing + other.skipped_not_closing,
            skipped_no_deadline=self.skipped_no_deadline + other.skipped_no_deadline,
            skipped_race_not_found=self.skipped_race_not_found + other.skipped_race_not_found,
            skipped_missing_value=self.skipped_missing_value + other.skipped_missing_value,
        )


def load_odds_day(
    session: Session,
    venue_code: str,
    race_date: dt.date,
    race_number: int,
    race_odds: RaceOdds,
) -> OddsLoadStats:
    """Load one race's parsed closing-odds page into `odds_snapshots`.

    Only `is_closing` pages are loaded: a live/in-progress page renders
    current, not final, odds under the same URL (`odds_source.py`'s
    module docstring), and only one observation per race is ever
    retained by this source, so a non-closing page must not be mistaken
    for the closing one. The race must already exist with a known
    `scheduled_deadline_at` (set by a prior `load_b_file_day` call for
    that day) -- that timestamp is the only leakage-safe
    `observed_at`/`available_at` this source can support, since the odds
    page itself carries no timestamp of its own. Neither case raises:
    odds arriving before the matching B-file, or a non-closing page, are
    ordinary skip conditions, not parse defects (contrast
    `LoaderError`, which is reserved for input that cannot be loaded
    without inventing or losing data).

    Place odds are a low-high range, but `odds_snapshots.odds` is a
    single value, so they are stored as two rows -- `bet_type`
    `"place_low"`/`"place_high"` -- rather than widening the schema for
    one source. Idempotent per race: any existing snapshots for this
    race are fully replaced, matching `load_b_file_day`/`load_k_file_day`.
    """
    stats = OddsLoadStats()
    if not race_odds.is_closing:
        stats.skipped_not_closing += 1
        return stats

    venue = _venue(session, venue_code)
    race = session.scalar(
        select(Race).where(
            Race.race_date == race_date,
            Race.venue_id == venue.id,
            Race.race_number == race_number,
        )
    )
    if race is None:
        stats.skipped_race_not_found += 1
        return stats
    if race.scheduled_deadline_at is None:
        stats.skipped_no_deadline += 1
        return stats

    observed_at = race.scheduled_deadline_at
    source_id = _source_id(session, SOURCE_ODDS)

    for existing in session.scalars(select(OddsSnapshot).where(OddsSnapshot.race_id == race.id)):
        session.delete(existing)
    session.flush()

    for entry in race_odds.entries:
        combination = str(entry.lane_number)
        for bet_type, value in (
            ("win", entry.win_odds),
            ("place_low", entry.place_odds_low),
            ("place_high", entry.place_odds_high),
        ):
            if value is None:
                stats.skipped_missing_value += 1
                continue
            session.add(
                OddsSnapshot(
                    race_id=race.id,
                    bet_type=bet_type,
                    combination=combination,
                    odds=value,
                    observed_at=observed_at,
                    available_at=observed_at,
                    is_closing=True,
                    source_id=source_id,
                )
            )
            stats.snapshots += 1

    return stats


@dataclass
class WeatherLoadStats:
    """What a weather load actually did. `skipped_unknown_venue` should
    never be nonzero in practice: `jma_weather_source.VENUE_STATIONS`
    covers exactly `race_id.VALID_VENUE_CODES` (asserted in that
    module), so this is a defensive count, not an expected outcome."""

    observations: int = 0
    skipped_unknown_venue: int = 0

    def merge(self, other: WeatherLoadStats) -> WeatherLoadStats:
        return WeatherLoadStats(
            observations=self.observations + other.observations,
            skipped_unknown_venue=self.skipped_unknown_venue + other.skipped_unknown_venue,
        )


def load_weather_month(
    session: Session,
    venue_code: str,
    year: int,
    month: int,
    daily_weathers: tuple[DailyWeather, ...],
) -> WeatherLoadStats:
    """Load one venue-month of parsed JMA daily summaries into
    `weather_observations`.

    Idempotent per `(venue, month)`: any existing rows for this venue
    whose `weather_date` falls in this month are fully replaced, matching
    `load_b_file_day`/`load_k_file_day`/`load_odds_day`'s "delete this
    scope's rows, then re-insert" pattern -- a re-run never accumulates
    duplicates or leaves a stale row behind if a later fetch corrected an
    earlier one.
    """
    stats = WeatherLoadStats()
    if venue_code not in VALID_VENUE_CODES:
        stats.skipped_unknown_venue += 1
        return stats

    venue = _venue(session, venue_code)
    source_id = _source_id(session, SOURCE_JMA_WEATHER)

    month_start = dt.date(year, month, 1)
    month_end = dt.date(year + 1, 1, 1) if month == 12 else dt.date(year, month + 1, 1)
    for existing in session.scalars(
        select(WeatherObservation).where(
            WeatherObservation.venue_id == venue.id,
            WeatherObservation.weather_date >= month_start,
            WeatherObservation.weather_date < month_end,
        )
    ):
        session.delete(existing)
    session.flush()

    for daily in daily_weathers:
        weather_date = dt.date.fromisoformat(daily.date_iso)
        session.add(
            WeatherObservation(
                venue_id=venue.id,
                weather_date=weather_date,
                precipitation_total_mm=daily.precipitation_total_mm,
                precipitation_max_1h_mm=daily.precipitation_max_1h_mm,
                precipitation_max_10min_mm=daily.precipitation_max_10min_mm,
                temperature_avg_c=daily.temperature_avg_c,
                temperature_max_c=daily.temperature_max_c,
                temperature_min_c=daily.temperature_min_c,
                humidity_avg_pct=daily.humidity_avg_pct,
                humidity_min_pct=daily.humidity_min_pct,
                wind_avg_ms=daily.wind_avg_ms,
                wind_max_ms=daily.wind_max_ms,
                wind_max_direction=daily.wind_max_direction,
                wind_max_instant_ms=daily.wind_max_instant_ms,
                wind_max_instant_direction=daily.wind_max_instant_direction,
                wind_prevailing_direction=daily.wind_prevailing_direction,
                sunshine_hours=daily.sunshine_hours,
                available_at=weather_available_at(weather_date),
                source_id=source_id,
            )
        )
        stats.observations += 1

    return stats


__all__ = [
    "SOURCE_B_FILE",
    "SOURCE_JMA_WEATHER",
    "SOURCE_K_FILE",
    "SOURCE_ODDS",
    "LoadStats",
    "LoaderError",
    "OddsLoadStats",
    "WeatherLoadStats",
    "card_available_at",
    "ensure_reference_data",
    "load_b_file_day",
    "load_k_file_day",
    "load_odds_day",
    "load_weather_month",
    "results_available_at",
    "scheduled_deadline_at",
    "weather_available_at",
]
