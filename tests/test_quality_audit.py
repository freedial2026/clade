"""Tests for `db.quality_audit`.

Runs the real SQL against SQLite, so a check whose SQL is invalid or
non-portable fails here rather than on the host.
"""

from __future__ import annotations

import datetime as dt
import unittest

from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from boat_prediction.db import quality_audit
from boat_prediction.db.models import (
    Base,
    Race,
    RaceEntry,
    Racer,
    RaceResult,
    RaceResultEntry,
    Venue,
)
from boat_prediction.quality import AXIS_WEIGHTS

RACE_DATE = dt.date(2026, 6, 1)
DEADLINE = dt.datetime(2026, 6, 1, 8, 41, tzinfo=dt.UTC)
CARD_AVAILABLE = dt.datetime(2026, 5, 31, 15, 0, tzinfo=dt.UTC)
RESULT_AVAILABLE = dt.datetime(2026, 6, 1, 15, 0, tzinfo=dt.UTC)


def _engine():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection, _record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine


class QualityAuditTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = _engine()
        self.addCleanup(self.engine.dispose)

    def _seed_clean_race(self, session: Session) -> Race:
        venue = Venue(code="24", name="大村")
        session.add(venue)
        session.flush()
        race = Race(
            venue_id=venue.id,
            race_date=RACE_DATE,
            race_number=1,
            status="finished",
            scheduled_deadline_at=DEADLINE,
        )
        session.add(race)
        session.flush()
        for lane in range(1, 7):
            racer = Racer(registration_number=4000 + lane, name=f"選手{lane}")
            session.add(racer)
            session.flush()
            session.add(
                RaceEntry(
                    race_id=race.id,
                    lane_number=lane,
                    racer_id=racer.id,
                    listed_national_win_rate=5.0,
                    available_at=CARD_AVAILABLE,
                )
            )
        result = RaceResult(race_id=race.id, available_at=RESULT_AVAILABLE)
        session.add(result)
        session.flush()
        for lane in range(1, 7):
            session.add(
                RaceResultEntry(
                    race_result_id=result.id,
                    lane_number=lane,
                    finish_position=lane,
                    status="0",
                )
            )
        session.flush()
        return race

    def test_every_check_runs_on_an_empty_database(self) -> None:
        # Catches invalid or non-portable SQL without needing fixtures.
        with Session(self.engine) as session:
            checks = quality_audit.run_checks(session)

            self.assertEqual(len(checks), len(quality_audit._CHECKS))
            self.assertTrue(all(c.skipped for c in checks))

    def test_an_empty_database_scores_full_marks_and_says_it_skipped(self) -> None:
        # Absent data is a coverage question, not a defect -- scoring it
        # as failure would block ML on a merely incomplete dataset.
        with Session(self.engine) as session:
            report, checks = quality_audit.audit(session)

            self.assertEqual(report.total_score, sum(AXIS_WEIGHTS.values()))
            self.assertIn("[skip]", quality_audit.render(report, checks))

    def test_clean_data_passes_every_check(self) -> None:
        with Session(self.engine) as session:
            self._seed_clean_race(session)
            session.commit()

            report, checks = quality_audit.audit(session)

            failing = [c for c in checks if c.defects]
            self.assertEqual(failing, [], msg=quality_audit.render(report, checks))
            self.assertEqual(report.verdict, "train_or_predict")

    def test_a_missing_result_costs_completeness(self) -> None:
        with Session(self.engine) as session:
            race = self._seed_clean_race(session)
            session.query(RaceResult).filter(RaceResult.race_id == race.id).delete()
            session.commit()

            report, checks = quality_audit.audit(session)

            by_name = {c.name: c for c in checks}
            self.assertEqual(by_name["finished_races_have_a_result"].defects, 1)
            self.assertLess(report.axis_scores["completeness"], AXIS_WEIGHTS["completeness"])

    def test_a_feature_available_after_the_deadline_costs_point_in_time(self) -> None:
        # The defect the whole project is built to prevent.
        with Session(self.engine) as session:
            self._seed_clean_race(session)
            session.execute(
                RaceEntry.__table__.update().values(
                    available_at=DEADLINE + dt.timedelta(minutes=1)
                )
            )
            session.commit()

            report, checks = quality_audit.audit(session)

            by_name = {c.name: c for c in checks}
            self.assertEqual(by_name["card_features_precede_the_deadline"].defects, 6)
            self.assertLess(report.axis_scores["point_in_time"], AXIS_WEIGHTS["point_in_time"])

    def test_a_dead_heat_is_not_a_defect(self) -> None:
        # 16 races in the archive have two boats on finish_position 1.
        with Session(self.engine) as session:
            self._seed_clean_race(session)
            session.execute(
                RaceResultEntry.__table__.update()
                .where(RaceResultEntry.lane_number == 2)
                .values(finish_position=1)
            )
            session.commit()

            _, checks = quality_audit.audit(session)

            by_name = {c.name: c for c in checks}
            self.assertEqual(by_name["a_race_that_produced_placings_has_a_first"].defects, 0)

    def test_a_void_race_with_no_placings_at_all_is_not_a_defect(self) -> None:
        # 132 races end with every boat carrying a status code (mostly F)
        # and none a placing. That is an outcome, not missing data.
        with Session(self.engine) as session:
            self._seed_clean_race(session)
            session.execute(
                RaceResultEntry.__table__.update().values(finish_position=None, status="F")
            )
            session.commit()

            _, checks = quality_audit.audit(session)

            by_name = {c.name: c for c in checks}
            self.assertTrue(by_name["a_race_that_produced_placings_has_a_first"].skipped)

    def test_placings_that_skip_first_are_a_defect(self) -> None:
        with Session(self.engine) as session:
            self._seed_clean_race(session)
            session.execute(
                RaceResultEntry.__table__.update()
                .where(RaceResultEntry.lane_number == 1)
                .values(finish_position=None, status="F")
            )
            session.commit()

            _, checks = quality_audit.audit(session)

            by_name = {c.name: c for c in checks}
            self.assertEqual(by_name["a_race_that_produced_placings_has_a_first"].defects, 1)

    def test_a_duplicate_lane_cannot_be_inserted_at_all(self) -> None:
        # The uniqueness checks are unreachable while the schema's own
        # constraints hold -- which is the point of asserting it here:
        # they exist to catch a constraint dropped by a future migration,
        # not a defect the loader could produce today.
        with Session(self.engine) as session:
            race = self._seed_clean_race(session)
            racer = session.scalar(select(Racer))
            session.add(
                RaceEntry(
                    race_id=race.id,
                    lane_number=1,
                    racer_id=racer.id,
                    listed_national_win_rate=5.0,
                    available_at=CARD_AVAILABLE,
                )
            )

            with self.assertRaises(IntegrityError):
                session.commit()
            session.rollback()

        with Session(self.engine) as session:
            _, checks = quality_audit.audit(session)

            by_name = {c.name: c for c in checks}
            self.assertEqual(by_name["one_entry_per_lane"].defects, 0)

    def test_cancelled_races_are_not_counted_as_missing(self) -> None:
        with Session(self.engine) as session:
            venue = Venue(code="01", name="桐生")
            session.add(venue)
            session.flush()
            session.add(
                Race(
                    venue_id=venue.id,
                    race_date=RACE_DATE,
                    race_number=1,
                    status="cancelled",
                    scheduled_deadline_at=None,
                )
            )
            session.commit()

            _, checks = quality_audit.audit(session)

            by_name = {c.name: c for c in checks}
            self.assertTrue(by_name["races_have_six_entries"].skipped)
            self.assertTrue(by_name["races_have_a_deadline"].skipped)

    def test_axis_score_is_the_mean_pass_rate_not_row_weighted(self) -> None:
        checks = [
            quality_audit.Check("validity", "big", examined=1_000_000, defects=0, detail=""),
            quality_audit.Check("validity", "small", examined=10, defects=5, detail=""),
        ]

        scores = quality_audit.score_axes(checks)

        # Row-weighted would round to full marks and hide the small check.
        self.assertAlmostEqual(scores["validity"], AXIS_WEIGHTS["validity"] * 0.75)


if __name__ == "__main__":
    unittest.main()
