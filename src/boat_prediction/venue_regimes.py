"""When each venue changed regime, curated from official announcements.

The schema has treated venues as interchangeable apart from a
`venue_id`. They are not, and the gap became concrete on 2026-08-01:
BOAT RACE振興会 is rolling E30 fuel out **venue by venue, in step with
each venue's motor replacement**, so 戸田 from 2026-08-05 is racing a
different fleet on a different fuel from 戸田 the week before. Any
analysis that pools them is pooling two regimes.

**This is a curated reference, not a feed.** These are announcements,
published per venue at different times and in different places, so a
scraper would be the wrong instrument — it would be fragile, it would
need 24 parsers, and it would still miss anything announced only in a
venue's own news post. A hand-maintained table with a source URL per row
is smaller, checkable in a diff, and honest about where each fact came
from.

**Fuel and motor generation change together by design.** The rollout is
explicitly tied to motor replacement, at every venue. So no
difference-in-differences can separate the fuel's effect from the new
fleet's — they are the same event. The 0.020 s exhibition-time slowdown
measured across the April venues (see tasks/HANDOFF.md) is the combined
effect and cannot be attributed further.

**`certainty` is recorded and matters.** An announced date from the
promoter is not the same kind of fact as a date reported second-hand, and
a later analysis that segments on a wrong boundary will silently mix
regimes rather than fail. Rows below `announced` should be treated as
provisional.

Coverage is deliberately partial: what is known is here, and what is not
is absent rather than guessed. `venues_missing_fuel_date()` reports the
gap so it stays visible.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from .race_id import VALID_VENUE_CODES

REGIME_FUEL = "fuel"
REGIME_MOTOR_GENERATION = "motor_generation"

CERTAINTY_ANNOUNCED = "announced"
"""Stated by the promoter or the venue itself, with a date."""
CERTAINTY_REPORTED = "reported"
"""Second-hand (press, venue social media) with a specific date."""
CERTAINTY_APPROXIMATE = "approximate"
"""Period known, exact date not established."""

_PR_RELEASE = "https://www.boatrace-pr.jp/pc/site/release/2026/03/10976/"


@dataclass(frozen=True)
class VenueRegime:
    """One venue's regime from `effective_from` until the next row for the
    same `(venue_code, regime_type)`.

    No `effective_to`: it is derivable from the next row, and storing it
    invites the two disagreeing — the same reasoning that keeps
    `race_meetings.meeting_end_date` NULL at load time.
    """

    venue_code: str
    regime_type: str
    effective_from: dt.date
    value: str
    certainty: str
    source_url: str
    note: str = ""


# Ordered by venue then date. Every row needs a source; a row without one
# is a guess wearing a citation's clothes.
VENUE_REGIMES: tuple[VenueRegime, ...] = (
    # --- E30 rollout, tied to motor replacement at each venue ---
    VenueRegime(
        "11", REGIME_FUEL, dt.date(2025, 5, 1), "E30", CERTAINTY_APPROXIMATE, _PR_RELEASE,
        "びわこ. Trial venue from 2025-05; the release gives the month, not a day.",
    ),
    VenueRegime(
        "24", REGIME_FUEL, dt.date(2025, 5, 1), "E30", CERTAINTY_APPROXIMATE, _PR_RELEASE,
        "大村. Trial venue, same caveat as びわこ.",
    ),
    VenueRegime(
        "06", REGIME_FUEL, dt.date(2026, 4, 9), "E30", CERTAINTY_ANNOUNCED, _PR_RELEASE,
        "浜名湖. First venue of the general rollout.",
    ),
    VenueRegime(
        "21", REGIME_FUEL, dt.date(2026, 4, 16), "E30", CERTAINTY_ANNOUNCED, _PR_RELEASE, "芦屋",
    ),
    VenueRegime(
        "13", REGIME_FUEL, dt.date(2026, 4, 17), "E30", CERTAINTY_ANNOUNCED, _PR_RELEASE, "尼崎",
    ),
    VenueRegime(
        "05", REGIME_FUEL, dt.date(2026, 4, 18), "E30", CERTAINTY_ANNOUNCED, _PR_RELEASE, "多摩川",
    ),
    VenueRegime(
        "18", REGIME_FUEL, dt.date(2026, 4, 20), "E30", CERTAINTY_ANNOUNCED, _PR_RELEASE, "徳山",
    ),
    VenueRegime(
        "19", REGIME_FUEL, dt.date(2026, 4, 29), "E30", CERTAINTY_ANNOUNCED, _PR_RELEASE, "下関",
    ),
    VenueRegime(
        "02", REGIME_FUEL, dt.date(2026, 8, 5), "E30", CERTAINTY_REPORTED,
        "https://x.com/tadashi9263/status/2061362588882194552",
        "戸田. From the 前検日 of the meeting starting 2026-08-06, with the new "
        "motor fleet. Venue-side report; not yet confirmed against a promoter release.",
    ),
    # --- motor generations, where the fuel switch dates them ---
    VenueRegime(
        "02", REGIME_MOTOR_GENERATION, dt.date(2026, 8, 5), "2026", CERTAINTY_REPORTED,
        "https://x.com/tadashi9263/status/2061362588882194552",
        "戸田. New motors and boats from this meeting. This is the first "
        "service-period boundary this project has been able to date -- see "
        "deviation 1 in db/models.py, which was written when none was known.",
    ),
)


def regimes_for(venue_code: str, regime_type: str) -> tuple[VenueRegime, ...]:
    if venue_code not in VALID_VENUE_CODES:
        raise ValueError(f"unknown venue_code: {venue_code!r}")
    return tuple(
        r
        for r in sorted(VENUE_REGIMES, key=lambda r: r.effective_from)
        if r.venue_code == venue_code and r.regime_type == regime_type
    )


def regime_at(venue_code: str, regime_type: str, on: dt.date) -> VenueRegime | None:
    """The regime in force on `on`, or None if nothing is recorded before it.

    None means *not known*, never "the old regime" — a caller that needs
    to exclude unknown-regime races must check for it rather than assume.
    """
    applicable = [r for r in regimes_for(venue_code, regime_type) if r.effective_from <= on]
    return applicable[-1] if applicable else None


def venues_missing_fuel_date() -> tuple[str, ...]:
    """Venues with no recorded fuel switch, so the gap stays visible
    rather than reading as "still on the old fuel"."""
    known = {r.venue_code for r in VENUE_REGIMES if r.regime_type == REGIME_FUEL}
    return tuple(sorted(VALID_VENUE_CODES - known))
