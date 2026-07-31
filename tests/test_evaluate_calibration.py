"""Tests for `db.evaluate_calibration`."""

from __future__ import annotations

import datetime as dt
import unittest

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from boat_prediction.db import evaluate_calibration
from boat_prediction.db.dataset import LANES
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


class PreviousMonthStartTest(unittest.TestCase):
    def test_steps_back_one_month(self) -> None:
        self.assertEqual(
            evaluate_calibration._previous_month_start(dt.date(2026, 3, 1)),
            dt.date(2026, 2, 1),
        )

    def test_wraps_across_a_year_boundary(self) -> None:
        self.assertEqual(
            evaluate_calibration._previous_month_start(dt.date(2026, 1, 1)),
            dt.date(2025, 12, 1),
        )


class ReconstructCalibratedVectorTest(unittest.TestCase):
    def test_rescales_the_remaining_mass_preserving_ratios(self) -> None:
        class _Calibrator:
            def calibrate_confidence(self, confidence: float) -> float:
                return 0.5

        row = [0.7, 0.1, 0.1, 0.05, 0.03, 0.02]
        result = evaluate_calibration._reconstruct_calibrated_vector(row, _Calibrator())

        self.assertAlmostEqual(result[0], 0.5)
        self.assertAlmostEqual(sum(result), 1.0)
        # Ratios among the non-top classes are preserved.
        self.assertAlmostEqual(result[1] / result[2], row[1] / row[2])

    def test_spreads_uniformly_when_the_original_was_near_one_hot(self) -> None:
        class _Calibrator:
            def calibrate_confidence(self, confidence: float) -> float:
                return 0.6

        row = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        result = evaluate_calibration._reconstruct_calibrated_vector(row, _Calibrator())

        self.assertAlmostEqual(result[0], 0.6)
        self.assertAlmostEqual(sum(result), 1.0)
        self.assertAlmostEqual(result[1], result[2])


class EvaluateCalibrationTest(unittest.TestCase):
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

    def _add_race(self, session: Session, race_date: dt.date, winner: int) -> None:
        deadline = dt.datetime.combine(
            race_date, dt.time(DEADLINE_HOUR, 41), tzinfo=dt.UTC
        )
        race = Race(
            venue_id=self.venue_id,
            race_date=race_date,
            race_number=1,
            status="finished",
            scheduled_deadline_at=deadline,
            race_class="予選",
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

    def _seed_months(self, session: Session, months: list[int]) -> None:
        for month in months:
            for day in range(1, 11):
                winner = ((day + month) % 6) + 1
                self._add_race(session, dt.date(2026, month, day), winner)

    def test_rejects_fewer_than_two_min_train_months(self) -> None:
        with Session(self.engine) as session, self.assertRaises(
            evaluate_calibration.CalibrationEvalError
        ):
            evaluate_calibration.evaluate(
                session,
                start_date=dt.date(2026, 1, 1),
                end_date=dt.date(2026, 3, 31),
                min_train_months=1,
            )

    def test_runs_and_reports_raw_and_calibrated_metrics(self) -> None:
        with Session(self.engine) as session:
            self._seed_months(session, [1, 2, 3, 4])
            session.commit()

            result = evaluate_calibration.evaluate(
                session,
                start_date=dt.date(2026, 1, 1),
                end_date=dt.date(2026, 4, 30),
                min_train_months=2,
            )

            self.assertGreater(len(result.per_fold), 0)
            fold = result.per_fold[0]
            self.assertGreater(fold.core_train_races, 0)
            self.assertGreater(fold.calib_valid_races, 0)
            self.assertGreater(fold.test_races, 0)
            self.assertGreater(fold.raw.log_loss, 0)
            self.assertGreater(fold.calibrated.log_loss, 0)
            text = evaluate_calibration.render(result)
            self.assertIn("mean log-loss", text)

    def test_calib_valid_window_is_exactly_the_month_before_test(self) -> None:
        with Session(self.engine) as session:
            self._seed_months(session, [1, 2, 3, 4])
            session.commit()

            result = evaluate_calibration.evaluate(
                session,
                start_date=dt.date(2026, 1, 1),
                end_date=dt.date(2026, 4, 30),
                min_train_months=2,
            )

            for fold_stats in result.per_fold:
                test_month = dt.date.fromisoformat(fold_stats.test_month)
                calib_start = evaluate_calibration._previous_month_start(test_month)
                # 10 races/month in the fixture; calib_valid must be that
                # one month, not core_train's growing window.
                self.assertLess(calib_start, test_month)
                self.assertEqual(fold_stats.calib_valid_races, 10)

    def test_reports_an_empty_range(self) -> None:
        with Session(self.engine) as session, self.assertRaises(
            evaluate_calibration.CalibrationEvalError
        ):
            evaluate_calibration.evaluate(
                session,
                start_date=dt.date(2030, 1, 1),
                end_date=dt.date(2030, 12, 31),
                min_train_months=2,
            )


if __name__ == "__main__":
    unittest.main()
