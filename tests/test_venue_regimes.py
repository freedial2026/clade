"""Curated venue regime boundaries.

The value of this table is entirely in its trustworthiness, so the tests
are about provenance and about the one failure mode that would be silent:
a lookup returning "the old regime" when the truth is "not known".
"""

from __future__ import annotations

import datetime as dt
import unittest

from boat_prediction.race_id import VALID_VENUE_CODES
from boat_prediction.venue_regimes import (
    CERTAINTY_ANNOUNCED,
    CERTAINTY_APPROXIMATE,
    CERTAINTY_REPORTED,
    REGIME_FUEL,
    REGIME_MOTOR_GENERATION,
    VENUE_REGIMES,
    regime_at,
    regimes_for,
    venues_missing_fuel_date,
)

CERTAINTIES = {CERTAINTY_ANNOUNCED, CERTAINTY_REPORTED, CERTAINTY_APPROXIMATE}


class TableIntegrityTest(unittest.TestCase):
    def test_every_row_cites_a_source(self) -> None:
        """A row without one is a guess wearing a citation's clothes."""
        for regime in VENUE_REGIMES:
            self.assertTrue(
                regime.source_url.startswith("http"),
                f"{regime.venue_code} {regime.regime_type} has no source",
            )

    def test_every_row_declares_its_certainty(self) -> None:
        for regime in VENUE_REGIMES:
            self.assertIn(regime.certainty, CERTAINTIES, regime.venue_code)

    def test_every_venue_code_is_real(self) -> None:
        for regime in VENUE_REGIMES:
            self.assertIn(regime.venue_code, VALID_VENUE_CODES)

    def test_no_duplicate_boundary_for_one_venue_and_type(self) -> None:
        keys = [(r.venue_code, r.regime_type, r.effective_from) for r in VENUE_REGIMES]
        self.assertEqual(len(keys), len(set(keys)))


class RegimeAtTest(unittest.TestCase):
    def test_returns_the_regime_in_force(self) -> None:
        regime = regime_at("06", REGIME_FUEL, dt.date(2026, 5, 1))

        self.assertIsNotNone(regime)
        self.assertEqual(regime.value, "E30")

    def test_the_day_before_a_switch_is_not_yet_switched(self) -> None:
        self.assertIsNone(regime_at("02", REGIME_FUEL, dt.date(2026, 8, 4)))
        self.assertIsNotNone(regime_at("02", REGIME_FUEL, dt.date(2026, 8, 5)))

    def test_unknown_reads_as_none_not_as_the_old_regime(self) -> None:
        """The failure mode that would be silent: a caller taking None to
        mean "still on the old fuel" when it means "we do not know"."""
        self.assertIsNone(regime_at("06", REGIME_FUEL, dt.date(2026, 1, 1)))

    def test_rejects_an_unknown_venue(self) -> None:
        with self.assertRaises(ValueError):
            regime_at("99", REGIME_FUEL, dt.date(2026, 5, 1))

    def test_regimes_are_returned_in_date_order(self) -> None:
        for code in VALID_VENUE_CODES:
            for regime_type in (REGIME_FUEL, REGIME_MOTOR_GENERATION):
                dates = [r.effective_from for r in regimes_for(code, regime_type)]
                self.assertEqual(dates, sorted(dates))


class CoverageTest(unittest.TestCase):
    def test_the_gap_is_reported_rather_than_hidden(self) -> None:
        missing = venues_missing_fuel_date()

        # partial by design -- what matters is that it is visible
        self.assertTrue(missing)
        self.assertNotIn("06", missing)
        self.assertNotIn("02", missing)

    def test_missing_and_known_together_cover_every_venue(self) -> None:
        known = {r.venue_code for r in VENUE_REGIMES if r.regime_type == REGIME_FUEL}

        self.assertEqual(known | set(venues_missing_fuel_date()), VALID_VENUE_CODES)

    def test_toda_carries_both_a_fuel_and_a_motor_boundary(self) -> None:
        """The rollout is tied to motor replacement, so the two coincide --
        which is also why no design can separate their effects."""
        fuel = regime_at("02", REGIME_FUEL, dt.date(2026, 8, 6))
        motor = regime_at("02", REGIME_MOTOR_GENERATION, dt.date(2026, 8, 6))

        self.assertIsNotNone(fuel)
        self.assertIsNotNone(motor)
        self.assertEqual(fuel.effective_from, motor.effective_from)


if __name__ == "__main__":
    unittest.main()
