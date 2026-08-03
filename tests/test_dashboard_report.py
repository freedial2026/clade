"""Tests for `db.dashboard_report` -- the JSON the PHP dashboard reads.

Two things matter here: the vendor template's `validate_dashboard_data`
must accept the output (every required key present, every race carrying
its required fields), and the descriptive labels (`data_availability`,
`decision_status`) must actually reflect what is in the database rather
than a guess -- a dashboard that always says "candidate" regardless of
what is missing would be worse than no dashboard.
"""

from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from boat_prediction.db import dashboard_report, loader
from boat_prediction.db.models import (
    Base,
    BeforeInfoEntry,
    OddsSnapshot,
    Race,
    RaceEntry,
    RacePrediction,
    Racer,
    RacerPeriodStats,
)
from boat_prediction.model_registry import DEFAULT_ROLE, ModelRegistry
from boat_prediction.db.predict_daily import PREVIEW_ROLE

JST = ZoneInfo("Asia/Tokyo")


def _engine():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection, _record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine


class DashboardReportTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = _engine()
        self.addCleanup(self.engine.dispose)
        self.now = dt.datetime(2026, 8, 3, 3, 0, tzinfo=dt.UTC)  # 12:00 JST
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
        deadline_offset_minutes: int = 30,
        full_card: bool = True,
    ) -> Race:
        venue = loader._venue(session, venue_code)
        deadline = self.now + dt.timedelta(minutes=deadline_offset_minutes)
        race = Race(
            venue_id=venue.id,
            race_date=self.race_date,
            race_number=race_number,
            status="scheduled",
            race_class="予選",
            scheduled_deadline_at=deadline,
        )
        session.add(race)
        session.flush()
        lanes = range(1, 7) if full_card else range(1, 5)
        for lane in lanes:
            racer = Racer(registration_number=5000 + race_number * 10 + lane, name=f"R{lane}")
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
                    listed_motor_second_rate=35.0 if full_card else None,
                    listed_boat_second_rate=35.0 if full_card else None,
                    listed_age=30,
                    listed_weight=52,
                    available_at=self.now - dt.timedelta(hours=6),
                )
            )
        session.flush()
        return race

    def _add_period_stats(self, session: Session, race: Race) -> None:
        """Minimal `RacerPeriodStats` rows for every racer in `race`, so
        the `racer_period_stats` critical-data flag reads True -- a
        coarse presence check, matching what `dashboard_report` itself
        does."""
        for entry in session.scalars(
            select(RaceEntry).where(RaceEntry.race_id == race.id)
        ):
            session.add(
                RacerPeriodStats(
                    racer_id=entry.racer_id,
                    period_year=2026,
                    period_number=2,
                    period_from=dt.date(2026, 5, 1),
                    period_to=dt.date(2026, 10, 31),
                    available_at=self.now - dt.timedelta(days=30),
                )
            )
        session.flush()

    def _add_before_info(self, session: Session, race: Race, *, courses=None) -> None:
        for lane in range(1, 7):
            session.add(
                BeforeInfoEntry(
                    race_id=race.id,
                    lane_number=lane,
                    exhibition_time_sec=6.70 + lane * 0.01,
                    tilt_angle=-0.5,
                    start_exhibition_course=(courses or {}).get(lane, lane),
                    observed_at=self.now,
                    available_at=self.now,
                )
            )
        session.flush()

    def _add_win_odds(self, session: Session, race: Race, odds: dict, *, at=None) -> None:
        at = at or self.now
        for lane, value in odds.items():
            session.add(
                OddsSnapshot(
                    race_id=race.id,
                    bet_type="win",
                    combination=str(lane),
                    odds=value,
                    observed_at=at,
                    available_at=at,
                    is_closing=False,
                )
            )
        session.flush()

    def _add_prediction(self, session: Session, race: Race, model_version: str, probs: dict) -> None:
        for lane, p in probs.items():
            session.add(
                RacePrediction(
                    race_id=race.id,
                    lane_number=lane,
                    model_version=model_version,
                    win_probability=p,
                    predicted_at=self.now,
                    features_available_at=self.now - dt.timedelta(hours=1),
                )
            )
        session.flush()

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


class BuildDashboardTest(DashboardReportTestBase):
    def test_a_complete_race_is_a_candidate_with_full_availability(self) -> None:
        with Session(self.engine) as session:
            race = self._make_race(session, race_number=1)
            self._add_before_info(session, race)
            self._add_period_stats(session, race)
            self._add_win_odds(session, race, {1: 1.5, 2: 5.0, 3: 8.0, 4: 10.0, 5: 12.0, 6: 20.0})
            with tempfile.TemporaryDirectory() as tmp:
                registry_path = self._registry(Path(tmp), default_version="cardv1")
                self._add_prediction(
                    session,
                    race,
                    "cardv1",
                    {1: 0.55, 2: 0.15, 3: 0.10, 4: 0.10, 5: 0.06, 6: 0.04},
                )
                session.commit()

                dashboard = dashboard_report.build_dashboard(
                    session, registry_path=registry_path, now=self.now
                )

            self.assertEqual(len(dashboard["races"]), 1)
            out = dashboard["races"][0]
            self.assertTrue(all(out["data_availability"][k] for k in ("race_card", "current_odds")))
            # p=0.55 * odds 1.5 * 100 = 82.5 -> rounds to 83, below 100 -> skip
            self.assertEqual(out["decision_status"], "skip")

    def test_missing_critical_data_marks_the_race_waiting_not_skip(self) -> None:
        with Session(self.engine) as session:
            race = self._make_race(session, race_number=1, full_card=False)  # only 4 lanes
            session.commit()

            with tempfile.TemporaryDirectory() as tmp:
                registry_path = self._registry(Path(tmp))
                dashboard = dashboard_report.build_dashboard(
                    session, registry_path=registry_path, now=self.now
                )

            out = dashboard["races"][0]
            self.assertFalse(out["data_availability"]["race_card"])
            self.assertEqual(out["decision_status"], "waiting")
            self.assertIn(
                "重要データが不足",
                [r["title"] for r in out["decision_reasons"]],
            )

    def test_a_race_clearing_ev_100_is_a_candidate(self) -> None:
        with Session(self.engine) as session:
            race = self._make_race(session, race_number=1)
            self._add_before_info(session, race)
            self._add_period_stats(session, race)
            self._add_win_odds(session, race, {1: 3.0, 2: 5.0, 3: 8.0, 4: 10.0, 5: 12.0, 6: 20.0})
            with tempfile.TemporaryDirectory() as tmp:
                registry_path = self._registry(Path(tmp), default_version="cardv1")
                # p=0.40 * odds 3.0 * 100 = 120 -> candidate
                self._add_prediction(
                    session,
                    race,
                    "cardv1",
                    {1: 0.40, 2: 0.20, 3: 0.15, 4: 0.10, 5: 0.10, 6: 0.05},
                )
                session.commit()
                dashboard = dashboard_report.build_dashboard(
                    session, registry_path=registry_path, now=self.now
                )

            out = dashboard["races"][0]
            self.assertEqual(out["decision_status"], "candidate")
            self.assertEqual(out["expected_return_per_100_yen"], 120)

    def test_the_preview_model_overrides_the_card_model_when_present(self) -> None:
        with Session(self.engine) as session:
            race = self._make_race(session, race_number=1)
            self._add_before_info(session, race)
            self._add_win_odds(session, race, {1: 3.0, 2: 5.0, 3: 8.0, 4: 10.0, 5: 12.0, 6: 20.0})
            with tempfile.TemporaryDirectory() as tmp:
                registry_path = self._registry(
                    Path(tmp), default_version="cardv1", preview_version="previewv1"
                )
                self._add_prediction(
                    session, race, "cardv1", {1: 0.10, 2: 0.10, 3: 0.10, 4: 0.10, 5: 0.10, 6: 0.50}
                )
                self._add_prediction(
                    session,
                    race,
                    "previewv1",
                    {1: 0.60, 2: 0.10, 3: 0.10, 4: 0.10, 5: 0.05, 6: 0.05},
                )
                session.commit()
                dashboard = dashboard_report.build_dashboard(
                    session, registry_path=registry_path, now=self.now
                )

            out = dashboard["races"][0]
            self.assertEqual(out["prediction_source"], "preview")
            self.assertAlmostEqual(out["model_probability"], 0.60)
            self.assertTrue(out["data_availability"]["prediction_preview"])

    def test_course_changed_flag_reflects_a_real_lane_swap(self) -> None:
        with Session(self.engine) as session:
            race = self._make_race(session, race_number=1)
            self._add_before_info(session, race, courses={1: 2, 2: 1, 3: 3, 4: 4, 5: 5, 6: 6})
            self._add_win_odds(session, race, {1: 1.5, 2: 5.0, 3: 8.0, 4: 10.0, 5: 12.0, 6: 20.0})
            with tempfile.TemporaryDirectory() as tmp:
                registry_path = self._registry(Path(tmp))
                session.commit()
                dashboard = dashboard_report.build_dashboard(
                    session, registry_path=registry_path, now=self.now
                )

            out = dashboard["races"][0]
            self.assertIn("進入変更あり", [r["title"] for r in out["decision_reasons"]])

    def test_a_race_past_the_horizon_is_excluded(self) -> None:
        with Session(self.engine) as session:
            self._make_race(session, race_number=1, deadline_offset_minutes=600)
            with tempfile.TemporaryDirectory() as tmp:
                registry_path = self._registry(Path(tmp))
                session.commit()
                dashboard = dashboard_report.build_dashboard(
                    session, registry_path=registry_path, now=self.now, horizon_hours=6
                )

            self.assertEqual(dashboard["races"], [])

    def test_a_race_whose_deadline_passed_is_excluded(self) -> None:
        with Session(self.engine) as session:
            self._make_race(session, race_number=1, deadline_offset_minutes=-5)
            with tempfile.TemporaryDirectory() as tmp:
                registry_path = self._registry(Path(tmp))
                session.commit()
                dashboard = dashboard_report.build_dashboard(
                    session, registry_path=registry_path, now=self.now
                )

            self.assertEqual(dashboard["races"], [])

    def test_venue_summary_counts_match_its_races(self) -> None:
        with Session(self.engine) as session:
            self._make_race(session, race_number=1, venue_code="24")
            self._make_race(session, race_number=2, venue_code="24")
            with tempfile.TemporaryDirectory() as tmp:
                registry_path = self._registry(Path(tmp))
                session.commit()
                dashboard = dashboard_report.build_dashboard(
                    session, registry_path=registry_path, now=self.now
                )

            venue = next(v for v in dashboard["venues"] if v["venue_code"] == "24")
            self.assertEqual(venue["remaining_race_count"], 2)
            # The vendor template renders this unconditionally
            # (`e($venue['water_type_label'])`); a missing key would be a
            # PHP warning on every page load, not just a cosmetic gap.
            self.assertIn("water_type_label", venue)
            self.assertEqual(venue["water_type_label"], "海水")

    def test_no_active_model_does_not_crash_and_shows_a_gap(self) -> None:
        with Session(self.engine) as session:
            self._make_race(session, race_number=1)
            with tempfile.TemporaryDirectory() as tmp:
                # Registry file created but nothing activated.
                registry_path = Path(tmp) / "registry.json"
                ModelRegistry(registry_path)
                session.commit()
                dashboard = dashboard_report.build_dashboard(
                    session, registry_path=registry_path, now=self.now
                )

            self.assertEqual(dashboard["site"]["model_version"], "(未登録)")
            self.assertFalse(dashboard["races"][0]["data_availability"]["prediction_card"])

    def test_no_bet_is_ever_actually_placed(self) -> None:
        """Structural guard: this module must never enable real wagering."""
        with Session(self.engine) as session:
            with tempfile.TemporaryDirectory() as tmp:
                registry_path = self._registry(Path(tmp))
                dashboard = dashboard_report.build_dashboard(
                    session, registry_path=registry_path, now=self.now
                )

            self.assertFalse(dashboard["risk"]["actual_betting_enabled"])


class RequiredKeysTest(DashboardReportTestBase):
    """The vendor template's own validation is the contract; every output
    must satisfy it without modification to `src/helpers.php`."""

    def test_every_top_level_key_the_template_requires_is_present(self) -> None:
        with Session(self.engine) as session:
            self._make_race(session, race_number=1)
            with tempfile.TemporaryDirectory() as tmp:
                registry_path = self._registry(Path(tmp))
                session.commit()
                dashboard = dashboard_report.build_dashboard(
                    session, registry_path=registry_path, now=self.now
                )

        for key in ("site", "risk", "data_catalog", "venues", "races"):
            self.assertIn(key, dashboard)

    def test_every_race_carries_the_fields_the_template_requires(self) -> None:
        with Session(self.engine) as session:
            self._make_race(session, race_number=1)
            with tempfile.TemporaryDirectory() as tmp:
                registry_path = self._registry(Path(tmp))
                session.commit()
                dashboard = dashboard_report.build_dashboard(
                    session, registry_path=registry_path, now=self.now
                )

        required = (
            "race_id", "venue_code", "venue_name", "race_number",
            "scheduled_deadline_at", "decision_status", "decision_label",
            "max_stake_yen", "data_availability",
        )
        for key in required:
            self.assertIn(key, dashboard["races"][0])

    def test_the_json_serializes_without_error(self) -> None:
        """UUIDs and datetimes must not reach `json.dump` unconverted --
        this is what the CLI's `default=str` masks, so this test bypasses
        it and calls `json.dumps` directly."""
        with Session(self.engine) as session:
            race = self._make_race(session, race_number=1)
            self._add_before_info(session, race)
            with tempfile.TemporaryDirectory() as tmp:
                registry_path = self._registry(Path(tmp))
                session.commit()
                dashboard = dashboard_report.build_dashboard(
                    session, registry_path=registry_path, now=self.now
                )

        json.dumps(dashboard, ensure_ascii=False)


class CollectionAndRoiReportTest(DashboardReportTestBase):
    def test_collection_report_counts_reflect_real_rows(self) -> None:
        with Session(self.engine) as session:
            race = self._make_race(session, race_number=1)
            self._add_win_odds(session, race, {1: 1.5, 2: 5.0})
            session.commit()

            report = dashboard_report.build_collection_report(session)

        win_source = next(s for s in report["sources"] if "単勝" in s["label"])
        self.assertEqual(win_source["count"], 2)
        self.assertEqual(win_source["distinct_races"], 1)

    def test_roi_report_states_it_is_a_backtest_not_a_live_ledger(self) -> None:
        report = dashboard_report.build_roi_report()

        self.assertIn("バックテスト", report["note"])
        self.assertEqual(report["break_even_roi"], 1.0)
        self.assertTrue(report["baselines"])
        self.assertIn("note", report["ev_hypothesis"])


if __name__ == "__main__":
    unittest.main()
