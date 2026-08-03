"""Freezing a model into the registry (`db.train_model`).

Two models are registered from here into two roles, and the thing worth
testing is not that a fit happened -- sklearn's job -- but that the
artifact is labelled with enough truth for `predict_daily` to apply it
correctly months later: which role it answers for, and which feature set
it was fit on.
"""

from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from boat_prediction.db.models import (
    Base,
    BeforeInfoEntry,
    Race,
    RaceEntry,
    Racer,
    RaceResult,
    RaceResultEntry,
    Venue,
)
from boat_prediction.db.train_model import (
    EARLIEST_BEFORE_INFO_DATE,
    FEATURE_SET_VERSION,
    PREVIEW_FEATURE_SET_VERSION,
    PREVIEW_ROLE,
    train_and_register,
)
from boat_prediction.model_registry import DEFAULT_ROLE, ModelRegistry

START = dt.date(2026, 1, 5)
END = dt.date(2026, 1, 24)


def _engine():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection, _record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine


class TrainModelTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = _engine()
        self.addCleanup(self.engine.dispose)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.registry_path = self.root / "registry.json"

        with Session(self.engine) as session:
            venue = Venue(code="24", name="大村")
            session.add(venue)
            session.flush()
            for lane in range(1, 7):
                session.add(Racer(registration_number=4000 + lane, name=f"選手{lane}"))
            session.flush()
            racers = session.query(Racer).order_by(Racer.registration_number).all()

            # Twenty races over twenty days, the win alternating between
            # lanes 1 and 2 so the fit has more than one class to separate.
            for index in range(20):
                race_date = START + dt.timedelta(days=index)
                deadline = dt.datetime.combine(race_date, dt.time(8, 41), tzinfo=dt.UTC)
                race = Race(
                    venue_id=venue.id,
                    race_date=race_date,
                    race_number=1,
                    status="finished",
                    race_class="予選",
                    scheduled_deadline_at=deadline,
                )
                session.add(race)
                session.flush()
                observed = deadline - dt.timedelta(minutes=20)
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
                    session.add(
                        BeforeInfoEntry(
                            race_id=race.id,
                            lane_number=lane,
                            exhibition_time_sec=6.70 + lane * 0.01,
                            start_exhibition_st_sec=0.15,
                            tilt_angle=-0.5,
                            start_exhibition_course=lane,
                            observed_at=observed,
                            available_at=observed,
                        )
                    )
                result = RaceResult(
                    race_id=race.id, available_at=deadline + dt.timedelta(hours=6)
                )
                session.add(result)
                session.flush()
                winner = 1 if index % 2 == 0 else 2
                position = 2
                for lane in range(1, 7):
                    if lane == winner:
                        finish = 1
                    else:
                        finish = position
                        position += 1
                    session.add(
                        RaceResultEntry(
                            race_result_id=result.id,
                            lane_number=lane,
                            finish_position=finish,
                            status="01",
                        )
                    )
            session.commit()

    def _train(self, **kwargs) -> str:
        with Session(self.engine) as session:
            return train_and_register(
                session,
                start_date=START,
                end_date=END,
                registry_path=self.registry_path,
                artifact_dir=self.root,
                **kwargs,
            )

    def test_the_card_model_registers_into_the_default_role(self) -> None:
        version_id = self._train()

        registry = ModelRegistry(self.registry_path)
        self.assertEqual(version_id, "logistic_cards_20260124")
        self.assertEqual(registry.get_active(DEFAULT_ROLE).version_id, version_id)
        entry = registry.get(version_id)
        self.assertEqual(entry.feature_set_version, FEATURE_SET_VERSION)
        self.assertFalse(entry.parameters["include_before_info"])
        self.assertEqual(entry.evaluation_metrics["feature_count"], 6 * 11 + 1)

    def test_the_preview_model_registers_into_the_preview_role(self) -> None:
        version_id = self._train(include_before_info=True)

        registry = ModelRegistry(self.registry_path)
        self.assertEqual(version_id, "logistic_cards_preview_20260124")
        self.assertEqual(registry.get_active(PREVIEW_ROLE).version_id, version_id)
        entry = registry.get(version_id)
        self.assertEqual(entry.feature_set_version, PREVIEW_FEATURE_SET_VERSION)
        self.assertTrue(entry.parameters["include_before_info"])
        self.assertEqual(entry.evaluation_metrics["feature_count"], 6 * 15 + 1)

    def test_the_two_models_coexist_without_displacing_each_other(self) -> None:
        card = self._train()
        preview = self._train(include_before_info=True)

        registry = ModelRegistry(self.registry_path)
        self.assertEqual(registry.get_active(DEFAULT_ROLE).version_id, card)
        self.assertEqual(registry.get_active(PREVIEW_ROLE).version_id, preview)
        self.assertEqual(registry.active_roles(), [DEFAULT_ROLE, PREVIEW_ROLE])

    def test_refuses_a_preview_window_predating_the_beforeinfo_data(self) -> None:
        """Those races would be dropped one by one and the run would look
        like a success on a fraction of the window it was asked for."""
        with Session(self.engine) as session, self.assertRaises(ValueError) as caught:
            train_and_register(
                session,
                start_date=EARLIEST_BEFORE_INFO_DATE - dt.timedelta(days=1),
                end_date=END,
                registry_path=self.registry_path,
                artifact_dir=self.root,
                include_before_info=True,
            )
        self.assertIn("直前情報", str(caught.exception))

    def test_the_artifact_checksum_verifies_after_registration(self) -> None:
        version_id = self._train(include_before_info=True)

        self.assertTrue(ModelRegistry(self.registry_path).verify_artifact(version_id))


if __name__ == "__main__":
    unittest.main()
