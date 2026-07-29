import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from boat_prediction.inventory import InventoryError, build_inventory


class BuildInventoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.raw_dir = Path(self._tmp.name) / "raw"
        self.raw_dir.mkdir()
        self.manifest_path = Path(self._tmp.name) / "manifest.json"

    def _write(self, name: str, content: str) -> Path:
        path = self.raw_dir / name
        path.write_text(content, encoding="utf-8")
        return path

    def test_records_size_checksum_and_timestamp(self) -> None:
        self._write("race_20260101.txt", "hello")
        fixed_time = datetime(2026, 1, 1, tzinfo=timezone.utc)

        records = build_inventory(
            self.raw_dir, self.manifest_path, source="official", now=fixed_time
        )

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.relative_path, "race_20260101.txt")
        self.assertEqual(record.size_bytes, 5)
        self.assertEqual(len(record.checksum_sha256), 64)
        self.assertEqual(record.collected_at, fixed_time.isoformat())
        self.assertTrue(self.manifest_path.is_file())

    def test_only_allowed_suffixes_are_inventoried(self) -> None:
        self._write("race.txt", "data")
        self._write("notes.md", "ignore me")

        records = build_inventory(self.raw_dir, self.manifest_path, source="official")

        self.assertEqual([r.relative_path for r in records], ["race.txt"])

    def test_rerun_over_unchanged_file_keeps_original_collected_at(self) -> None:
        self._write("race.txt", "data")
        first_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
        second_time = datetime(2026, 1, 2, tzinfo=timezone.utc)

        first = build_inventory(
            self.raw_dir, self.manifest_path, source="official", now=first_time
        )
        second = build_inventory(
            self.raw_dir, self.manifest_path, source="official", now=second_time
        )

        self.assertEqual(first[0].collected_at, second[0].collected_at)
        self.assertEqual(first[0].checksum_sha256, second[0].checksum_sha256)

    def test_changed_file_gets_a_fresh_collected_at(self) -> None:
        path = self._write("race.txt", "data-v1")
        first_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
        second_time = datetime(2026, 1, 2, tzinfo=timezone.utc)

        first = build_inventory(
            self.raw_dir, self.manifest_path, source="official", now=first_time
        )
        path.write_text("data-v2", encoding="utf-8")
        second = build_inventory(
            self.raw_dir, self.manifest_path, source="official", now=second_time
        )

        self.assertNotEqual(first[0].checksum_sha256, second[0].checksum_sha256)
        self.assertEqual(second[0].collected_at, second_time.isoformat())

    def test_removed_file_stays_in_manifest_as_history(self) -> None:
        self._write("race.txt", "data")
        build_inventory(self.raw_dir, self.manifest_path, source="official")

        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["files"]), 1)

        (self.raw_dir / "race.txt").unlink()
        current = build_inventory(self.raw_dir, self.manifest_path, source="official")

        self.assertEqual(current, [])
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["files"]), 1)

    def test_missing_raw_dir_fails_safely(self) -> None:
        missing = Path(self._tmp.name) / "does-not-exist"

        with self.assertRaises(InventoryError):
            build_inventory(missing, self.manifest_path, source="official")

    def test_corrupt_manifest_fails_safely(self) -> None:
        self._write("race.txt", "data")
        self.manifest_path.write_text("not json", encoding="utf-8")

        with self.assertRaises(InventoryError):
            build_inventory(self.raw_dir, self.manifest_path, source="official")


if __name__ == "__main__":
    unittest.main()
