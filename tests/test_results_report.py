"""Tests for `db.results_report` -- the JSON `web/dashboard/results.php` reads.

Two things matter: the prediction-vs-result "hit" computation must reflect
what is actually in `race_predictions` and `race_results` (not guess), and
`build_cron_report` must only claim a count for a cron job when the
underlying `dashboard_report.build_collection_report` source actually has
one.
"""

from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from boat_prediction.db import loader, results_report
from boat_prediction.db.models import (
    Base,
    LiveRaceResult,
    OddsSnapshot,
    Race,
    RaceEntry,
    RacePrediction,
    Racer,
    RaceResult,
    RaceResultEntry,
)
from boat_prediction.db.predict_daily import PREVIEW_ROLE
from boat_prediction.model_registry import DEFAULT_ROLE, ModelRegistry

JST = ZoneInfo("Asia/Tokyo")


def _engine():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection, _record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine


class ResultsReportTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = _engine()
        self.addCleanup(self.engine.dispose)
        self.now = dt.datetime(2026, 8, 4, 8, 0, tzinfo=dt.UTC)  # 17:00 JST
        self.race_date = self.now.astimezone(JST).date()
        with Session(self.engine) as session:
            loader.ensure_reference_data(session)
            session.commit()

    def _make_race(
        self,
        session: Session,
        *,
        race_number: int,
        venue_code: str = "24",
        race_date=None,
        status: str = "finished",
        deadline_offset_minutes: int = -60,
    ) -> Race:
        venue = loader._venue(session, venue_code)
        race = Race(
            venue_id=venue.id,
            race_date=race_date or self.race_date,
            race_number=race_number,
            status=status,
            race_class="予選",
            scheduled_deadline_at=self.now + dt.timedelta(minutes=deadline_offset_minutes),
        )
        session.add(race)
        session.flush()
        for lane in range(1, 7):
            racer = Racer(
                registration_number=6000 + int(venue_code) * 100 + race_number * 10 + lane,
                name=f"R{lane}",
            )
            session.add(racer)
            session.flush()
            session.add(
                RaceEntry(
                    race_id=race.id,
                    racer_id=racer.id,
                    lane_number=lane,
                    listed_class="A1",
                    available_at=self.now - dt.timedelta(hours=6),
                )
            )
        session.flush()
        return race

    def _add_prediction(self, session: Session, race: Race, model_version: str, probs: dict) -> None:
        for lane, p in probs.items():
            session.add(
                RacePrediction(
                    race_id=race.id,
                    lane_number=lane,
                    model_version=model_version,
                    win_probability=p,
                    predicted_at=self.now - dt.timedelta(minutes=90),
                    features_available_at=self.now - dt.timedelta(hours=2),
                )
            )
        session.flush()

    def _add_result(self, session: Session, race: Race, finish_positions: dict) -> RaceResult:
        result = RaceResult(race_id=race.id, available_at=self.now)
        session.add(result)
        session.flush()
        for lane in range(1, 7):
            session.add(
                RaceResultEntry(
                    race_result_id=result.id,
                    lane_number=lane,
                    finish_position=finish_positions.get(lane),
                )
            )
        session.flush()
        return result

    def _registry(self, tmp: Path, *, default_version=None, preview_version=None) -> Path:
        registry_path = tmp / "registry.json"
        registry = ModelRegistry(registry_path)
        for version, role in ((default_version, DEFAULT_ROLE), (preview_version, PREVIEW_ROLE)):
            if version is None:
                continue
            artifact = tmp / f"{version}.pkl"
            artifact.write_bytes(b"stub")
            registry.register(
                version,
                dataset_version="x",
                feature_set_version="x",
                code_version="x",
                parameters={},
                calibration_version="none",
                evaluation_metrics={},
                artifact_path=artifact,
            )
            registry.activate(version, role=role)
        return registry_path


class BuildResultsReportTest(ResultsReportTestBase):
    def test_correct_prediction_is_a_hit(self) -> None:
        with Session(self.engine) as session:
            race = self._make_race(session, race_number=1)
            with tempfile.TemporaryDirectory() as tmp:
                registry_path = self._registry(Path(tmp), default_version="cardv1", preview_version="prevv1")
                self._add_prediction(session, race, "cardv1", {1: 0.6, 2: 0.1, 3: 0.1, 4: 0.1, 5: 0.05, 6: 0.05})
                self._add_prediction(session, race, "prevv1", {1: 0.7, 2: 0.1, 3: 0.1, 4: 0.05, 5: 0.03, 6: 0.02})
                self._add_result(session, race, {1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6})
                session.commit()

                report = results_report.build_results_report(session, registry_path=registry_path, now=self.now)

            races = report["races_by_date"][self.race_date.isoformat()]
            self.assertEqual(len(races), 1)
            out = races[0]
            self.assertEqual(out["race_state"], "finished")
            self.assertEqual(out["card_prediction"]["top_lane"], 1)
            self.assertTrue(out["card_hit"])
            self.assertEqual(out["preview_prediction"]["top_lane"], 1)
            self.assertTrue(out["preview_hit"])

    def test_wrong_prediction_is_a_miss(self) -> None:
        with Session(self.engine) as session:
            race = self._make_race(session, race_number=1)
            with tempfile.TemporaryDirectory() as tmp:
                registry_path = self._registry(Path(tmp), default_version="cardv1")
                self._add_prediction(session, race, "cardv1", {1: 0.6, 2: 0.1, 3: 0.1, 4: 0.1, 5: 0.05, 6: 0.05})
                self._add_result(session, race, {1: 2, 2: 1, 3: 3, 4: 4, 5: 5, 6: 6})
                session.commit()

                report = results_report.build_results_report(session, registry_path=registry_path, now=self.now)

            out = report["races_by_date"][self.race_date.isoformat()][0]
            self.assertFalse(out["card_hit"])
            self.assertEqual(out["winner_lanes"], [2])

    def test_dead_heat_counts_as_a_hit_for_either_winner(self) -> None:
        with Session(self.engine) as session:
            race = self._make_race(session, race_number=1)
            with tempfile.TemporaryDirectory() as tmp:
                registry_path = self._registry(Path(tmp), default_version="cardv1")
                self._add_prediction(session, race, "cardv1", {1: 0.1, 2: 0.6, 3: 0.1, 4: 0.1, 5: 0.05, 6: 0.05})
                self._add_result(session, race, {1: 1, 2: 1, 3: 3, 4: 4, 5: 5, 6: 6})
                session.commit()

                report = results_report.build_results_report(session, registry_path=registry_path, now=self.now)

            out = report["races_by_date"][self.race_date.isoformat()][0]
            self.assertEqual(sorted(out["winner_lanes"]), [1, 2])
            self.assertTrue(out["card_hit"])

    def test_race_with_no_result_yet_is_pending_and_excluded_from_hit_rate(self) -> None:
        with Session(self.engine) as session:
            race = self._make_race(session, race_number=1, deadline_offset_minutes=-10)
            with tempfile.TemporaryDirectory() as tmp:
                registry_path = self._registry(Path(tmp), default_version="cardv1")
                self._add_prediction(session, race, "cardv1", {1: 0.6, 2: 0.1, 3: 0.1, 4: 0.1, 5: 0.05, 6: 0.05})
                session.commit()

                report = results_report.build_results_report(session, registry_path=registry_path, now=self.now)

            out = report["races_by_date"][self.race_date.isoformat()][0]
            self.assertEqual(out["race_state"], "pending")
            self.assertIsNone(out["card_hit"])
            summary = report["summary_by_date"][self.race_date.isoformat()]
            self.assertEqual(summary["card_decided"], 0)
            self.assertIsNone(summary["card_hit_rate"])

    def test_upcoming_race_before_its_deadline_is_not_pending(self) -> None:
        with Session(self.engine) as session:
            race = self._make_race(session, race_number=1, deadline_offset_minutes=30)
            session.commit()
            report = results_report.build_results_report(
                session,
                registry_path=Path(tempfile.mkdtemp()) / "registry.json",
                now=self.now,
            )
            out = report["races_by_date"][self.race_date.isoformat()][0]
            self.assertEqual(out["race_state"], "upcoming")

    def test_cancelled_race_is_excluded_from_hit_rate(self) -> None:
        with Session(self.engine) as session:
            race = self._make_race(session, race_number=1, status="cancelled")
            session.commit()
            report = results_report.build_results_report(
                session,
                registry_path=Path(tempfile.mkdtemp()) / "registry.json",
                now=self.now,
            )
            summary = report["summary_by_date"][self.race_date.isoformat()]
            self.assertEqual(summary["cancelled"], 1)
            self.assertEqual(summary["card_decided"], 0)

    def test_race_with_no_winner_recorded_is_void(self) -> None:
        with Session(self.engine) as session:
            race = self._make_race(session, race_number=1)
            self._add_result(session, race, {})  # every lane DNF/DQ, no finish_position
            session.commit()
            report = results_report.build_results_report(
                session,
                registry_path=Path(tempfile.mkdtemp()) / "registry.json",
                now=self.now,
            )
            out = report["races_by_date"][self.race_date.isoformat()][0]
            self.assertEqual(out["race_state"], "void")
            self.assertIsNone(out["card_hit"])

    def test_missing_preview_prediction_leaves_only_preview_side_null(self) -> None:
        with Session(self.engine) as session:
            race = self._make_race(session, race_number=1)
            with tempfile.TemporaryDirectory() as tmp:
                registry_path = self._registry(Path(tmp), default_version="cardv1")
                self._add_prediction(session, race, "cardv1", {1: 0.6, 2: 0.1, 3: 0.1, 4: 0.1, 5: 0.05, 6: 0.05})
                self._add_result(session, race, {1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6})
                session.commit()

                report = results_report.build_results_report(session, registry_path=registry_path, now=self.now)

            out = report["races_by_date"][self.race_date.isoformat()][0]
            self.assertTrue(out["card_hit"])
            self.assertIsNone(out["preview_prediction"]["top_lane"])
            self.assertIsNone(out["preview_hit"])

    def test_races_are_grouped_by_date_newest_first(self) -> None:
        with Session(self.engine) as session:
            self._make_race(session, race_number=1, race_date=self.race_date)
            self._make_race(session, race_number=1, venue_code="01", race_date=self.race_date - dt.timedelta(days=2))
            session.commit()
            report = results_report.build_results_report(
                session,
                registry_path=Path(tempfile.mkdtemp()) / "registry.json",
                now=self.now,
                days=4,
            )
            self.assertEqual(report["dates"][0], self.race_date.isoformat())
            self.assertEqual(len(report["dates"]), 4)
            self.assertEqual(len(report["races_by_date"][self.race_date.isoformat()]), 1)
            two_days_ago = (self.race_date - dt.timedelta(days=2)).isoformat()
            self.assertEqual(len(report["races_by_date"][two_days_ago]), 1)


class BuildCronReportTest(ResultsReportTestBase):
    def test_matched_job_carries_the_real_count(self) -> None:
        with Session(self.engine) as session:
            race = self._make_race(session, race_number=1)
            with tempfile.TemporaryDirectory() as tmp:
                registry_path = self._registry(Path(tmp), default_version="cardv1")
                self._add_prediction(session, race, "cardv1", {1: 0.6, 2: 0.1, 3: 0.1, 4: 0.1, 5: 0.05, 6: 0.05})
                session.add(
                    OddsSnapshot(
                        race_id=race.id,
                        bet_type="win",
                        combination="1",
                        odds=2.5,
                        observed_at=self.now,
                        available_at=self.now,
                        is_closing=False,
                    )
                )
                session.commit()

                report = results_report.build_cron_report(session, registry_path=registry_path)

        by_module = {j["module"]: j for j in report["jobs"]}
        self.assertEqual(by_module["predict_daily"]["count"], 6)
        self.assertEqual(by_module["capture_odds --with-exacta"]["count"], 1)

    def test_card_and_preview_prediction_counts_are_not_conflated(self) -> None:
        """Regression: both predict_daily jobs write rows labelled
        `予測 (<model version>)`; matching by a shared prefix would let
        the preview job's count silently pick up the card model's rows
        (or vice versa) whenever both have run."""
        with Session(self.engine) as session:
            race = self._make_race(session, race_number=1)
            with tempfile.TemporaryDirectory() as tmp:
                registry_path = self._registry(
                    Path(tmp), default_version="cardv1", preview_version="prevv1"
                )
                self._add_prediction(session, race, "cardv1", {1: 0.6, 2: 0.1, 3: 0.1, 4: 0.1, 5: 0.05, 6: 0.05})
                self._add_prediction(session, race, "prevv1", {1: 0.7, 2: 0.1, 3: 0.1, 4: 0.05, 5: 0.03, 6: 0.02})
                session.commit()

                report = results_report.build_cron_report(session, registry_path=registry_path)

        by_module = {j["module"]: j for j in report["jobs"]}
        self.assertEqual(by_module["predict_daily"]["count"], 6)
        self.assertEqual(by_module["predict_daily --role preview"]["count"], 6)

    def test_unmatched_job_has_no_count(self) -> None:
        with Session(self.engine) as session:
            session.commit()
            report = results_report.build_cron_report(
                session, registry_path=Path(tempfile.mkdtemp()) / "registry.json"
            )

        by_module = {j["module"]: j for j in report["jobs"]}
        self.assertIsNone(by_module["ingest_daily card"]["count"])
        self.assertIsNone(by_module["ingest_daily results"]["count"])
        # No active model in the registry, so neither predict_daily job matches.
        self.assertIsNone(by_module["predict_daily"]["count"])
        self.assertIsNone(by_module["predict_daily --role preview"]["count"])

    def test_every_configured_job_is_present(self) -> None:
        with Session(self.engine) as session:
            report = results_report.build_cron_report(
                session, registry_path=Path(tempfile.mkdtemp()) / "registry.json"
            )
        self.assertEqual(len(report["jobs"]), len(results_report.CRON_JOBS))


if __name__ == "__main__":
    unittest.main()


RACE_DATE = dt.date(2026, 8, 4)


class LiveResultFallbackTest(unittest.TestCase):
    """Today's races must show a result today.

    The K-file arrives at 02:00 the next day, so before the live capture
    existed every race on the current card read as `pending` and a
    prediction could not be checked against its own race.
    """

    def setUp(self) -> None:
        self.engine = _engine()
        self.session = Session(self.engine)
        loader.ensure_reference_data(self.session)
        venue = loader._venue(self.session, "01")
        self.race = Race(
            venue_id=venue.id,
            race_date=RACE_DATE,
            race_number=1,
            status="scheduled",
            scheduled_deadline_at=dt.datetime(
                RACE_DATE.year, RACE_DATE.month, RACE_DATE.day, 10, 0, tzinfo=dt.UTC
            ),
        )
        self.session.add(self.race)
        self.session.flush()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def _add_live(self, winner: int = 1) -> None:
        observed = dt.datetime(
            RACE_DATE.year, RACE_DATE.month, RACE_DATE.day, 10, 10, tzinfo=dt.UTC
        )
        for lane in range(1, 7):
            self.session.add(
                LiveRaceResult(
                    race_id=self.race.id,
                    lane_number=lane,
                    finish_position=1 if lane == winner else lane + 1,
                    observed_at=observed,
                    available_at=observed,
                )
            )
        self.session.flush()

    def test_a_live_result_is_used_when_the_k_file_has_not_arrived(self) -> None:
        self._add_live(winner=3)

        found = results_report._actual_results(self.session, [self.race.id])

        self.assertIn(self.race.id, found)
        self.assertEqual(found[self.race.id][3], 1)

    def test_without_either_source_the_race_is_absent(self) -> None:
        found = results_report._actual_results(self.session, [self.race.id])

        self.assertEqual(found, {})

    def test_the_k_file_wins_where_both_exist(self) -> None:
        """The archive is authoritative; the live capture is for timing."""
        self._add_live(winner=3)
        result = RaceResult(race_id=self.race.id, available_at=dt.datetime.now(dt.UTC))
        self.session.add(result)
        self.session.flush()
        for lane in range(1, 7):
            self.session.add(
                RaceResultEntry(
                    race_result_id=result.id,
                    lane_number=lane,
                    finish_position=1 if lane == 5 else lane,
                )
            )
        self.session.flush()

        found = results_report._actual_results(self.session, [self.race.id])

        self.assertEqual(found[self.race.id][5], 1)
        self.assertNotEqual(found[self.race.id][3], 1)
