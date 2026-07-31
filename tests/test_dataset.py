"""Tests for `db.dataset` and `db.evaluate_p1`.

The SQL runs against SQLite so a non-portable query fails here rather
than on the host, and every exclusion rule is exercised with the shape
that triggers it.
"""

from __future__ import annotations

import datetime as dt
import unittest

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from boat_prediction.db import dataset, evaluate_p1
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


class DatasetTestBase(unittest.TestCase):
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
        winner_lanes: tuple[int, ...] = (1,),
        lanes: tuple[int, ...] = (1, 2, 3, 4, 5, 6),
        entry_available_at: dt.datetime | None = None,
        missing_class: bool = False,
        status: str = "finished",
    ) -> Race:
        deadline = dt.datetime.combine(
            race_date, dt.time(DEADLINE_HOUR, 41), tzinfo=dt.UTC
        )
        race = Race(
            venue_id=self.venue_id,
            race_date=race_date,
            race_number=race_number,
            status=status,
            scheduled_deadline_at=deadline,
        )
        session.add(race)
        session.flush()
        racers = session.query(Racer).order_by(Racer.registration_number).all()
        for lane in lanes:
            session.add(
                RaceEntry(
                    race_id=race.id,
                    lane_number=lane,
                    racer_id=racers[lane - 1].id,
                    listed_class=None if missing_class else "A1",
                    listed_age=30,
                    listed_weight=52,
                    listed_national_win_rate=5.5,
                    listed_national_second_rate=35.0,
                    listed_local_win_rate=5.0,
                    listed_local_second_rate=30.0,
                    listed_motor_second_rate=33.0,
                    listed_boat_second_rate=31.0,
                    available_at=entry_available_at
                    or (deadline - dt.timedelta(hours=9)),
                )
            )
        result = RaceResult(race_id=race.id, available_at=deadline + dt.timedelta(hours=6))
        session.add(result)
        session.flush()
        next_position = 1 + len(winner_lanes)
        for lane in lanes:
            if lane in winner_lanes:
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
        return race


class BuildDatasetTest(DatasetTestBase):
    def test_builds_one_row_per_race_six_lanes_wide(self) -> None:
        with Session(self.engine) as session:
            self._add_race(session, dt.date(2026, 1, 5), 1)
            session.commit()

            data = dataset.build_dataset(
                session, start_date=dt.date(2026, 1, 1), end_date=dt.date(2026, 1, 31)
            )

            self.assertEqual(len(data), 1)
            self.assertEqual(len(data.X[0]), 6 * len(dataset.FEATURE_NAMES))
            self.assertEqual(data.y, [1])
            self.assertEqual(data.dates, [dt.date(2026, 1, 5)])
            self.assertEqual(data.feature_names[0], "lane1_national_win_rate")

    def test_excludes_a_dead_heat(self) -> None:
        # Two boats on first place have no single winning lane, and
        # picking one would invent a label.
        with Session(self.engine) as session:
            self._add_race(session, dt.date(2026, 1, 5), 1, winner_lanes=(1, 2))
            session.commit()

            data = dataset.build_dataset(
                session, start_date=dt.date(2026, 1, 1), end_date=dt.date(2026, 1, 31)
            )

            self.assertEqual(len(data), 0)
            self.assertEqual(data.stats.dropped_no_single_winner, 1)

    def test_excludes_a_card_without_six_lanes(self) -> None:
        with Session(self.engine) as session:
            self._add_race(session, dt.date(2026, 1, 5), 1, lanes=(1, 2, 3, 4, 5))
            session.commit()

            data = dataset.build_dataset(
                session, start_date=dt.date(2026, 1, 1), end_date=dt.date(2026, 1, 31)
            )

            self.assertEqual(data.stats.dropped_not_six_lanes, 1)

    def test_excludes_a_feature_available_after_the_deadline(self) -> None:
        # The guard exists so a loader change cannot silently start
        # training on the future.
        with Session(self.engine) as session:
            deadline = dt.datetime.combine(
                dt.date(2026, 1, 5), dt.time(DEADLINE_HOUR, 41), tzinfo=dt.UTC
            )
            self._add_race(
                session,
                dt.date(2026, 1, 5),
                1,
                entry_available_at=deadline + dt.timedelta(minutes=1),
            )
            session.commit()

            data = dataset.build_dataset(
                session, start_date=dt.date(2026, 1, 1), end_date=dt.date(2026, 1, 31)
            )

            self.assertEqual(len(data), 0)
            self.assertEqual(data.stats.dropped_late_feature, 1)

    def test_excludes_a_row_with_a_missing_feature_rather_than_imputing(self) -> None:
        with Session(self.engine) as session:
            self._add_race(session, dt.date(2026, 1, 5), 1, missing_class=True)
            session.commit()

            data = dataset.build_dataset(
                session, start_date=dt.date(2026, 1, 1), end_date=dt.date(2026, 1, 31)
            )

            self.assertEqual(data.stats.dropped_missing_feature, 1)

    def test_excludes_races_outside_the_range_and_unfinished_ones(self) -> None:
        with Session(self.engine) as session:
            self._add_race(session, dt.date(2026, 1, 5), 1)
            self._add_race(session, dt.date(2026, 2, 5), 1)
            self._add_race(session, dt.date(2026, 1, 6), 1, status="scheduled")
            session.commit()

            data = dataset.build_dataset(
                session, start_date=dt.date(2026, 1, 1), end_date=dt.date(2026, 1, 31)
            )

            self.assertEqual(len(data), 1)
            self.assertEqual(data.dates, [dt.date(2026, 1, 5)])

    def test_rows_come_back_in_date_order(self) -> None:
        with Session(self.engine) as session:
            for day in (20, 3, 11):
                self._add_race(session, dt.date(2026, 1, day), 1)
            session.commit()

            data = dataset.build_dataset(
                session, start_date=dt.date(2026, 1, 1), end_date=dt.date(2026, 1, 31)
            )

            self.assertEqual(data.dates, sorted(data.dates))

    def test_rejects_a_reversed_range(self) -> None:
        with Session(self.engine) as session, self.assertRaises(ValueError):
            dataset.build_dataset(
                session, start_date=dt.date(2026, 2, 1), end_date=dt.date(2026, 1, 1)
            )


class EvaluateP1Test(DatasetTestBase):
    def test_runs_walk_forward_over_real_rows(self) -> None:
        with Session(self.engine) as session:
            # Three months, lane 1 winning more often than the rest, so
            # lane_prior has something to learn that uniform does not.
            for month in (1, 2, 3):
                for day in range(1, 13):
                    # every lane wins at least once per month, lane 1 most
                    winner = 1 if day <= 7 else day - 6
                    self._add_race(
                        session, dt.date(2026, month, day), 1, winner_lanes=(winner,)
                    )
            session.commit()

            result = evaluate_p1.evaluate(
                session,
                start_date=dt.date(2026, 1, 1),
                end_date=dt.date(2026, 3, 31),
                min_train_months=1,
            )

            self.assertEqual(result.n_races, 36)
            self.assertEqual(result.n_folds, 2)
            self.assertIn("uniform", result.mean_log_loss)
            self.assertIn("lane_prior", result.mean_log_loss)
            # The point of the run: the informed baseline must beat 1/6.
            self.assertLess(
                result.mean_log_loss["lane_prior"], result.mean_log_loss["uniform"]
            )
            self.assertIn("mean log-loss", evaluate_p1.render(result))

    def test_reports_an_empty_range_rather_than_producing_a_meaningless_score(
        self,
    ) -> None:
        with Session(self.engine) as session, self.assertRaises(ValueError):
            evaluate_p1.evaluate(
                session, start_date=dt.date(2030, 1, 1), end_date=dt.date(2030, 12, 31)
            )


if __name__ == "__main__":
    unittest.main()
