import json
import unittest
from datetime import datetime
from pathlib import Path

from boat_prediction.reconstruction import VersionedFact, reconstruct_as_of
from boat_prediction.temporal import TemporalRecord

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_facts(path: Path) -> list[VersionedFact]:
    data = json.loads(path.read_text(encoding="utf-8"))
    facts = []
    for entry in data:
        temporal_fields = {
            key: (datetime.fromisoformat(value) if value is not None else None)
            for key, value in entry["temporal"].items()
        }
        facts.append(
            VersionedFact(
                entity_key=entry["entity_key"],
                payload=entry["payload"],
                temporal=TemporalRecord(**temporal_fields),
            )
        )
    return facts


def _payloads(selected: dict) -> dict:
    return {key: fact.payload for key, fact in selected.items()}


class ReconstructAsOfGoldenTest(unittest.TestCase):
    def setUp(self) -> None:
        self.facts = _load_facts(FIXTURES_DIR / "racer_stats_versions.json")

    def test_snapshot_2026_01_15_matches_golden_fixture(self) -> None:
        expected = json.loads(
            (FIXTURES_DIR / "racer_stats_snapshot_2026-01-15.json").read_text(encoding="utf-8")
        )

        selected = reconstruct_as_of(self.facts, datetime.fromisoformat("2026-01-15T12:00:00+00:00"))

        self.assertEqual(_payloads(selected), expected)

    def test_snapshot_2026_03_01_matches_golden_fixture(self) -> None:
        expected = json.loads(
            (FIXTURES_DIR / "racer_stats_snapshot_2026-03-01.json").read_text(encoding="utf-8")
        )

        selected = reconstruct_as_of(self.facts, datetime.fromisoformat("2026-03-01T12:00:00+00:00"))

        self.assertEqual(_payloads(selected), expected)


class ReconstructAsOfBehaviorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.facts = _load_facts(FIXTURES_DIR / "racer_stats_versions.json")

    def test_excludes_a_version_not_yet_available_even_if_temporally_valid(self) -> None:
        # 2026-01-20 falls inside v2's valid_from/valid_to-less window's
        # *predecessor* v1 window, and v2 isn't available until
        # 2026-02-01T03:00 — so only v1 (win_rate 5.5) should be selected.
        prediction_at = datetime.fromisoformat("2026-01-20T00:00:00+00:00")

        selected = reconstruct_as_of(self.facts, prediction_at)

        self.assertEqual(selected["4321"].payload, {"win_rate": 5.5})

    def test_all_selected_facts_satisfy_the_availability_constraint(self) -> None:
        prediction_at = datetime.fromisoformat("2026-03-01T12:00:00+00:00")

        selected = reconstruct_as_of(self.facts, prediction_at)

        for fact in selected.values():
            self.assertLessEqual(fact.temporal.available_at, prediction_at)

    def test_prediction_before_any_availability_selects_nothing(self) -> None:
        prediction_at = datetime.fromisoformat("2025-12-31T00:00:00+00:00")

        selected = reconstruct_as_of(self.facts, prediction_at)

        self.assertEqual(selected, {})


if __name__ == "__main__":
    unittest.main()
