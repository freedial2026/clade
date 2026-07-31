"""Tests for boat_prediction.db.models and boat_prediction.db.ids.

Runs against an in-memory SQLite database (not PostgreSQL, the project's
default per docs/PROJECT_PROFILE.md) so these tests need no external
service. `PRAGMA foreign_keys=ON` is enabled per connection so
`ON DELETE CASCADE`/`SET NULL` behave the same as they will on
PostgreSQL -- SQLite otherwise silently ignores them, which would hide a
real defect.
"""

from __future__ import annotations

import datetime as dt
import unittest
import uuid

from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from boat_prediction.db.ids import uuid7
from boat_prediction.db.models import (
    Base,
    ExhibitionEntry,
    Race,
    RaceEntry,
    Racer,
    RaceResult,
    Venue,
)


def _engine():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection, _record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine


class Uuid7Test(unittest.TestCase):
    def test_is_a_valid_uuid_with_version_7_and_variant_bits_set(self) -> None:
        value = uuid7()

        self.assertEqual(value.version, 7)
        self.assertEqual(value.variant, uuid.RFC_4122)

    def test_embeds_the_given_timestamp_in_the_first_48_bits(self) -> None:
        # 2026-06-01T00:00:00Z in epoch milliseconds.
        timestamp_ms = int(dt.datetime(2026, 6, 1, tzinfo=dt.UTC).timestamp() * 1000)

        value = uuid7(timestamp_ms=timestamp_ms)

        self.assertEqual(int.from_bytes(value.bytes[0:6], "big"), timestamp_ms)

    def test_is_time_ordered_across_distinct_timestamps(self) -> None:
        earlier = uuid7(timestamp_ms=1_000)
        later = uuid7(timestamp_ms=2_000)

        self.assertLess(earlier, later)

    def test_rejects_a_timestamp_that_does_not_fit_in_48_bits(self) -> None:
        with self.assertRaises(ValueError):
            uuid7(timestamp_ms=1 << 48)


class SchemaConstraintTest(unittest.TestCase):
    """Constraints declared in models.py must actually be enforced by the
    database, not just documented -- these mirror the guide's §7.2
    UNIQUE/CHECK clauses."""

    def setUp(self) -> None:
        self.engine = _engine()
        self.addCleanup(self.engine.dispose)

    def _venue(self, session: Session, code: str = "24") -> Venue:
        venue = Venue(code=code, name="大村")
        session.add(venue)
        session.flush()
        return venue

    def test_race_natural_key_is_unique(self) -> None:
        with Session(self.engine) as session:
            venue = self._venue(session)
            session.add(Race(venue_id=venue.id, race_date=dt.date(2026, 6, 1), race_number=1))
            session.flush()
            session.add(Race(venue_id=venue.id, race_date=dt.date(2026, 6, 1), race_number=1))

            with self.assertRaises(IntegrityError):
                session.flush()

    def test_race_number_out_of_range_is_rejected(self) -> None:
        with Session(self.engine) as session:
            venue = self._venue(session)
            session.add(Race(venue_id=venue.id, race_date=dt.date(2026, 6, 1), race_number=13))

            with self.assertRaises(IntegrityError):
                session.flush()

    def test_race_entry_lane_number_out_of_range_is_rejected(self) -> None:
        with Session(self.engine) as session:
            venue = self._venue(session)
            race = Race(venue_id=venue.id, race_date=dt.date(2026, 6, 1), race_number=1)
            racer = Racer(registration_number=1234, name="Test Racer")
            session.add_all([race, racer])
            session.flush()
            session.add(
                RaceEntry(
                    race_id=race.id,
                    lane_number=7,
                    racer_id=racer.id,
                    available_at=dt.datetime.now(dt.UTC),
                )
            )

            with self.assertRaises(IntegrityError):
                session.flush()

    def test_racer_registration_number_is_unique(self) -> None:
        with Session(self.engine) as session:
            session.add(Racer(registration_number=1234, name="First"))
            session.flush()
            session.add(Racer(registration_number=1234, name="Second"))

            with self.assertRaises(IntegrityError):
                session.flush()

    def test_deleting_a_race_cascades_to_its_entries(self) -> None:
        with Session(self.engine) as session:
            venue = self._venue(session)
            race = Race(venue_id=venue.id, race_date=dt.date(2026, 6, 1), race_number=1)
            racer = Racer(registration_number=1234, name="Test Racer")
            session.add_all([race, racer])
            session.flush()
            session.add(
                RaceEntry(
                    race_id=race.id,
                    lane_number=1,
                    racer_id=racer.id,
                    available_at=dt.datetime.now(dt.UTC),
                )
            )
            session.commit()

            session.delete(race)
            session.commit()

            self.assertEqual(session.scalar(select(RaceEntry).limit(1)), None)

    def test_deleting_a_race_entry_sets_result_entry_link_to_null_not_cascading(self) -> None:
        # race_result_entries.race_entry_id is SET NULL (models.py
        # RaceResultEntry docstring): a result must outlive the card
        # being replaced, since re-loading a B-file day deletes and
        # re-inserts that day's race_entries.
        from boat_prediction.db.models import RaceResultEntry

        with Session(self.engine) as session:
            venue = self._venue(session)
            race = Race(venue_id=venue.id, race_date=dt.date(2026, 6, 1), race_number=1)
            racer = Racer(registration_number=1234, name="Test Racer")
            session.add_all([race, racer])
            session.flush()
            entry = RaceEntry(
                race_id=race.id,
                lane_number=1,
                racer_id=racer.id,
                available_at=dt.datetime.now(dt.UTC),
            )
            session.add(entry)
            session.flush()
            result = RaceResult(race_id=race.id, available_at=dt.datetime.now(dt.UTC))
            session.add(result)
            session.flush()
            result_entry = RaceResultEntry(
                race_result_id=result.id, race_entry_id=entry.id, lane_number=1
            )
            session.add(result_entry)
            session.commit()

            session.delete(entry)
            session.commit()

            refreshed = session.get(RaceResultEntry, result_entry.id)
            self.assertIsNotNone(refreshed)
            self.assertIsNone(refreshed.race_entry_id)

    def test_deleting_a_race_entry_cascades_to_its_exhibition_entry(self) -> None:
        with Session(self.engine) as session:
            venue = self._venue(session)
            race = Race(venue_id=venue.id, race_date=dt.date(2026, 6, 1), race_number=1)
            racer = Racer(registration_number=1234, name="Test Racer")
            session.add_all([race, racer])
            session.flush()
            entry = RaceEntry(
                race_id=race.id,
                lane_number=1,
                racer_id=racer.id,
                available_at=dt.datetime.now(dt.UTC),
            )
            session.add(entry)
            session.flush()
            session.add(
                ExhibitionEntry(
                    race_entry_id=entry.id,
                    exhibition_time_sec=6.70,
                    available_at=dt.datetime.now(dt.UTC),
                )
            )
            session.commit()

            session.delete(entry)
            session.commit()

            self.assertEqual(session.scalar(select(ExhibitionEntry).limit(1)), None)


class VenueNamesTest(unittest.TestCase):
    def test_covers_every_valid_venue_code(self) -> None:
        from boat_prediction.db.models import VENUE_NAMES
        from boat_prediction.race_id import VALID_VENUE_CODES

        self.assertEqual(set(VENUE_NAMES), VALID_VENUE_CODES)


if __name__ == "__main__":
    unittest.main()
