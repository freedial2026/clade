import unittest
from datetime import date

from boat_prediction.race_id import InvalidRaceKeyError, RaceKey, RaceKeyRegistry


class RaceKeyTest(unittest.TestCase):
    def test_canonical_id_is_deterministic(self) -> None:
        key_a = RaceKey(race_date=date(2026, 7, 29), venue_code="01", race_number=3)
        key_b = RaceKey(race_date=date(2026, 7, 29), venue_code="01", race_number=3)

        self.assertEqual(key_a.canonical_id, key_b.canonical_id)
        self.assertEqual(key_a.canonical_id, "2026-07-29-01-03")

    def test_rejects_unknown_venue_code(self) -> None:
        with self.assertRaises(InvalidRaceKeyError):
            RaceKey(race_date=date(2026, 7, 29), venue_code="99", race_number=1)

    def test_rejects_race_number_below_minimum(self) -> None:
        with self.assertRaises(InvalidRaceKeyError):
            RaceKey(race_date=date(2026, 7, 29), venue_code="01", race_number=0)

    def test_rejects_race_number_above_maximum(self) -> None:
        with self.assertRaises(InvalidRaceKeyError):
            RaceKey(race_date=date(2026, 7, 29), venue_code="01", race_number=13)

    def test_rejects_non_date_race_date(self) -> None:
        with self.assertRaises(InvalidRaceKeyError):
            RaceKey(race_date="2026-07-29", venue_code="01", race_number=1)


class RaceKeyRegistryTest(unittest.TestCase):
    def test_registers_distinct_keys(self) -> None:
        registry = RaceKeyRegistry()
        key_a = RaceKey(race_date=date(2026, 7, 29), venue_code="01", race_number=1)
        key_b = RaceKey(race_date=date(2026, 7, 29), venue_code="01", race_number=2)

        registry.register(key_a)
        registry.register(key_b)

        self.assertIn(key_a, registry)
        self.assertIn(key_b, registry)

    def test_rejects_duplicate_key(self) -> None:
        registry = RaceKeyRegistry()
        key = RaceKey(race_date=date(2026, 7, 29), venue_code="01", race_number=1)
        registry.register(key)

        with self.assertRaises(InvalidRaceKeyError):
            registry.register(RaceKey(race_date=date(2026, 7, 29), venue_code="01", race_number=1))


if __name__ == "__main__":
    unittest.main()
