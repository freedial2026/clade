"""Prospective prediction recording (`db.predict_daily`).

The assertions that matter here are temporal. This table exists to be
evidence that a probability was known before a deadline, so a test suite
that only checked "rows were written" would miss the entire point: the
failure mode is a row that looks fine and was actually produced too late,
or by a model that had seen the future.
"""

from __future__ import annotations

import datetime as dt
import unittest
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from boat_prediction.db import loader
from boat_prediction.db.models import (
    Base,
    Race,
    RaceEntry,
    RacePrediction,
    Racer,
)
from boat_prediction.db.predict_daily import predict_day
from boat_prediction.db.session import create_session_factory  # noqa: F401

JST = ZoneInfo("Asia/Tokyo")
RACE_DATE = dt.date(2026, 8, 1)


def _engine():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine


class _ConstantModel:
    """Returns a fixed six-lane distribution. `predict_day` must never
    call `fit`, so this deliberately does not define one."""

    def __init__(self, row=None) -> None:
        self.row = row or [0.5, 0.2, 0.1, 0.1, 0.05, 0.05]
        self.calls = 0

    def predict_proba(self, X):
        self.calls += 1
        return [list(self.row) for _ in X]


def _make_race(session, *, deadline_hour: int, race_number: int) -> Race:
    venue = loader._venue(session, "01")
    race = Race(
        venue_id=venue.id,
        race_date=RACE_DATE,
        race_number=race_number,
        status="scheduled",
        race_class="予選",
        race_class_label="予選",
        scheduled_deadline_at=dt.datetime(
            RACE_DATE.year, RACE_DATE.month, RACE_DATE.day, deadline_hour, 0, tzinfo=JST
        ).astimezone(dt.UTC),
    )
    session.add(race)
    session.flush()

    available = dt.datetime(
        RACE_DATE.year, RACE_DATE.month, RACE_DATE.day, 0, 0, tzinfo=JST
    ).astimezone(dt.UTC)
    for lane in range(1, 7):
        racer = Racer(registration_number=4000 + lane + race_number * 10, name=f"R{lane}")
        session.add(racer)
        session.flush()
        session.add(
            RaceEntry(
                race_id=race.id,
                racer_id=racer.id,
                lane_number=lane,
                listed_class="A1",
                listed_national_win_rate=6.0,
                listed_national_second_rate=40.0,
                listed_local_win_rate=6.0,
                listed_local_second_rate=40.0,
                listed_motor_second_rate=35.0,
                listed_boat_second_rate=35.0,
                listed_age=30,
                listed_weight=52,
                available_at=available,
            )
        )
    session.flush()
    return race


class PredictDayTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = _engine()
        self.session = Session(self.engine)
        loader.ensure_reference_data(self.session)
        self.now = dt.datetime(2026, 8, 1, 3, 0, tzinfo=dt.UTC)  # 12:00 JST

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_writes_six_rows_summing_to_one(self) -> None:
        _make_race(self.session, deadline_hour=18, race_number=1)

        stats = predict_day(
            self.session,
            race_date=RACE_DATE,
            model=_ConstantModel(),
            model_version="v1",
            now=self.now,
        )

        self.assertEqual(stats.races_predicted, 1)
        self.assertEqual(stats.rows_written, 6)
        probs = list(self.session.scalars(select(RacePrediction.win_probability)))
        self.assertEqual(len(probs), 6)
        self.assertAlmostEqual(sum(float(p) for p in probs), 1.0, places=6)

    def test_skips_a_race_whose_deadline_has_passed(self) -> None:
        _make_race(self.session, deadline_hour=9, race_number=1)  # 09:00 JST, already gone

        stats = predict_day(
            self.session,
            race_date=RACE_DATE,
            model=_ConstantModel(),
            model_version="v1",
            now=self.now,
        )

        self.assertEqual(stats.skipped_deadline_passed, 1)
        self.assertEqual(stats.rows_written, 0)

    def test_predicted_at_precedes_the_deadline_for_every_row(self) -> None:
        """The invariant the table exists to make checkable."""
        _make_race(self.session, deadline_hour=18, race_number=1)
        _make_race(self.session, deadline_hour=9, race_number=2)

        predict_day(
            self.session,
            race_date=RACE_DATE,
            model=_ConstantModel(),
            model_version="v1",
            now=self.now,
        )

        rows = self.session.execute(
            select(RacePrediction.predicted_at, Race.scheduled_deadline_at)
            .join(Race, Race.id == RacePrediction.race_id)
        ).all()
        self.assertTrue(rows)
        for predicted_at, deadline in rows:
            self.assertLess(predicted_at, deadline)

    def test_features_available_at_precedes_prediction(self) -> None:
        _make_race(self.session, deadline_hour=18, race_number=1)

        predict_day(
            self.session,
            race_date=RACE_DATE,
            model=_ConstantModel(),
            model_version="v1",
            now=self.now,
        )

        for feat_at, predicted_at in self.session.execute(
            select(RacePrediction.features_available_at, RacePrediction.predicted_at)
        ):
            self.assertLessEqual(feat_at, predicted_at)

    def test_never_fits_the_model(self) -> None:
        """A model refit at prediction time would make the whole record a
        backtest. `_ConstantModel` has no `fit`, so a call would raise."""
        _make_race(self.session, deadline_hour=18, race_number=1)
        model = _ConstantModel()

        predict_day(
            self.session,
            race_date=RACE_DATE,
            model=model,
            model_version="v1",
            now=self.now,
        )

        self.assertEqual(model.calls, 1)
        self.assertFalse(hasattr(model, "fit"))

    def test_model_version_is_recorded_on_every_row(self) -> None:
        _make_race(self.session, deadline_hour=18, race_number=1)

        predict_day(
            self.session,
            race_date=RACE_DATE,
            model=_ConstantModel(),
            model_version="logistic_cards_20260731",
            now=self.now,
        )

        versions = set(self.session.scalars(select(RacePrediction.model_version)))
        self.assertEqual(versions, {"logistic_cards_20260731"})

    def test_a_later_run_adds_a_second_prediction_rather_than_overwriting(self) -> None:
        _make_race(self.session, deadline_hour=18, race_number=1)
        kwargs = {
            "race_date": RACE_DATE,
            "model": _ConstantModel(),
            "model_version": "v1",
        }

        predict_day(self.session, now=self.now, **kwargs)
        predict_day(self.session, now=self.now + dt.timedelta(hours=1), **kwargs)

        stamps = set(self.session.scalars(select(RacePrediction.predicted_at)))
        self.assertEqual(len(stamps), 2)
        self.assertEqual(len(list(self.session.scalars(select(RacePrediction)))), 12)

    def test_no_races_is_not_an_error(self) -> None:
        stats = predict_day(
            self.session,
            race_date=RACE_DATE,
            model=_ConstantModel(),
            model_version="v1",
            now=self.now,
        )

        self.assertEqual(stats.races_predicted, 0)
        self.assertEqual(stats.rows_written, 0)

    def test_records_probabilities_and_no_decision(self) -> None:
        """Deliberate: no bet/no-bet column exists, so a policy change
        cannot invalidate the accumulated record."""
        columns = {c.name for c in RacePrediction.__table__.columns}
        for forbidden in ("bet", "stake", "decision", "selected", "skip_reason"):
            self.assertNotIn(forbidden, columns)


if __name__ == "__main__":
    unittest.main()
