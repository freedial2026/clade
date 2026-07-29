import tempfile
import unittest
from pathlib import Path

from boat_prediction.quarantine import QuarantineError, QuarantineStore, STATUS_QUARANTINED, STATUS_REPLAYED


class QuarantineStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ledger_path = Path(self._tmp.name) / "rejected" / "quarantine_ledger.json"

    def test_add_persists_the_original_failure(self) -> None:
        store = QuarantineStore(self.ledger_path)
        record = {"lane_number": 9}

        entry = store.add(record, code="E005", message="lane_number out of range")

        self.assertEqual(entry.status, STATUS_QUARANTINED)
        self.assertEqual(entry.original_record, record)
        self.assertEqual(entry.code, "E005")
        self.assertTrue(self.ledger_path.is_file())

    def test_readding_the_same_record_and_code_does_not_duplicate(self) -> None:
        store = QuarantineStore(self.ledger_path)
        record = {"lane_number": 9}

        first = store.add(record, code="E005", message="lane_number out of range")
        second = store.add(record, code="E005", message="lane_number out of range")

        self.assertEqual(first.quarantine_id, second.quarantine_id)
        self.assertEqual(len(store.list_pending()), 1)

    def test_replay_with_valid_correction_resolves_and_preserves_original(self) -> None:
        store = QuarantineStore(self.ledger_path)
        record = {"lane_number": 9}
        entry = store.add(record, code="E005", message="lane_number out of range")

        resolved = store.replay(
            entry.quarantine_id,
            corrected_record={"lane_number": 3},
            revalidate=lambda r: 1 <= r["lane_number"] <= 6,
        )

        self.assertEqual(resolved.status, STATUS_REPLAYED)
        self.assertEqual(resolved.corrected_record, {"lane_number": 3})
        self.assertEqual(resolved.original_record, record)  # original failure preserved
        self.assertEqual(resolved.code, "E005")
        self.assertIsNotNone(resolved.replayed_at)
        self.assertEqual(store.list_pending(), [])

    def test_replay_with_still_invalid_correction_raises_and_keeps_quarantined(self) -> None:
        store = QuarantineStore(self.ledger_path)
        entry = store.add({"lane_number": 9}, code="E005", message="out of range")

        with self.assertRaises(QuarantineError):
            store.replay(
                entry.quarantine_id,
                corrected_record={"lane_number": 99},
                revalidate=lambda r: 1 <= r["lane_number"] <= 6,
            )

        self.assertEqual(len(store.list_pending()), 1)

    def test_replaying_an_already_replayed_entry_is_idempotent(self) -> None:
        store = QuarantineStore(self.ledger_path)
        entry = store.add({"lane_number": 9}, code="E005", message="out of range")
        first = store.replay(
            entry.quarantine_id,
            corrected_record={"lane_number": 3},
            revalidate=lambda r: True,
        )

        second = store.replay(
            entry.quarantine_id,
            corrected_record={"lane_number": 4},  # ignored: already resolved
            revalidate=lambda r: True,
        )

        self.assertEqual(first, second)
        self.assertEqual(second.corrected_record, {"lane_number": 3})

    def test_replay_unknown_quarantine_id_fails_safely(self) -> None:
        store = QuarantineStore(self.ledger_path)

        with self.assertRaises(QuarantineError):
            store.replay("does-not-exist", {}, revalidate=lambda r: True)

    def test_corrupt_ledger_fails_safely(self) -> None:
        self.ledger_path.parent.mkdir(parents=True)
        self.ledger_path.write_text("not json", encoding="utf-8")

        with self.assertRaises(QuarantineError):
            QuarantineStore(self.ledger_path)

    def test_ledger_survives_reload_from_disk(self) -> None:
        store = QuarantineStore(self.ledger_path)
        entry = store.add({"lane_number": 9}, code="E005", message="out of range")

        reloaded = QuarantineStore(self.ledger_path)

        self.assertEqual(reloaded.get(entry.quarantine_id), entry)


if __name__ == "__main__":
    unittest.main()
