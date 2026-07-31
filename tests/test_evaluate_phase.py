"""Tests for `db.evaluate_phase`."""

from __future__ import annotations

import datetime as dt
import unittest

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from boat_prediction.db import evaluate_phase
from boat_prediction.db.models import (
    Base,
    Race,
    RaceEntry,
    Racer,
    RaceResult,
    RaceResultEntry,
    Venue,
)

DEADLINE_HOUR = 8


def _engine():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection, _record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine


class EvaluatePhaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = _engine()
        self.addCleanup(self.engine.dispose)
        with Session(self.engine) as session:
            venue = Venue(code="24", name="大村")
            session.add(venue)
            session.flush()
            self.venue_id = venue.id
            for lane in range(1, 7):
                session.add(Racer(registration_number=4000 + lane, name=f"選手{lane}"))
            session.commit()

    def _add_race(
        self,
        session: Session,
        race_date: dt.date,
        race_number: int,
        *,
        winner: int = 1,
        race_class: str = "予選",
    ) -> None:
        deadline = dt.datetime.combine(
            race_date, dt.time(DEADLINE_HOUR, 41), tzinfo=dt.UTC
        )
        race = Race(
            venue_id=self.venue_id,
            race_date=race_date,
            race_number=race_number,
            status="finished",
            scheduled_deadline_at=deadline,
            race_class=race_class,
        )
        session.add(race)
        session.flush()
        racers = session.query(Racer).order_by(Racer.registration_number).all()
        for lane in range(1, 7):
            session.add(
                RaceEntry(
                    race_id=race.id,
                    lane_number=lane,
                    racer_id=racers[lane - 1].id,
                    listed_class="A1",
                    listed_age=30,
                    listed_weight=52,
                    listed_national_win_rate=5.5,
                    listed_national_second_rate=35.0,
                    listed_local_win_rate=5.0,
                    listed_local_second_rate=30.0,
                    listed_motor_second_rate=33.0,
                    listed_boat_second_rate=31.0,
                    available_at=deadline - dt.timedelta(hours=9),
                )
            )
        result = RaceResult(race_id=race.id, available_at=deadline + dt.timedelta(hours=6))
        session.add(result)
        session.flush()
        next_position = 2
        for lane in range(1, 7):
            if lane == winner:
                position = 1
            else:
                position = next_position
                next_position += 1
            session.add(
                RaceResultEntry(
                    race_result_id=result.id,
                    lane_number=lane,
                    finish_position=position,
                    status="01",
                )
            )
        session.flush()

    def test_reports_every_phase_present_in_the_test_folds(self) -> None:
        with Session(self.engine) as session:
            for month in (1, 2, 3):
                for day in range(1, 11):
                    winner = 1 if day <= 7 else (day % 6) + 1
                    self._add_race(
                        session,
                        dt.date(2026, month, day),
                        1,
                        winner=winner,
                        race_class="優勝戦" if day == 10 else "予選",
                    )
            session.commit()

            result = evaluate_phase.evaluate(
                session,
                start_date=dt.date(2026, 1, 1),
                end_date=dt.date(2026, 3, 31),
                min_train_months=1,
            )

            self.assertIn("uniform", result.reports)
            self.assertIn("lane_prior", result.reports)
            phases = {s.group for s in result.reports["lane_prior"].subgroups}
            self.assertIn("trial", phases)
            self.assertIn("final", phases)

    def test_render_shows_a_confidence_interval_per_phase(self) -> None:
        with Session(self.engine) as session:
            for month in (1, 2, 3):
                for day in range(1, 11):
                    winner = 1 if day <= 7 else (day % 6) + 1
                    self._add_race(session, dt.date(2026, month, day), 1, winner=winner)
            session.commit()

            result = evaluate_phase.evaluate(
                session,
                start_date=dt.date(2026, 1, 1),
                end_date=dt.date(2026, 3, 31),
                min_train_months=1,
            )
            text = evaluate_phase.render(result)

            self.assertIn("95% CI", text)
            self.assertIn("trial", text)

    def test_reports_an_empty_range_rather_than_a_meaningless_breakdown(self) -> None:
        with Session(self.engine) as session, self.assertRaises(ValueError):
            evaluate_phase.evaluate(
                session, start_date=dt.date(2030, 1, 1), end_date=dt.date(2030, 12, 31)
            )


if __name__ == "__main__":
    unittest.main()
