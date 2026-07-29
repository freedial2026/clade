import tempfile
import unittest
from pathlib import Path

from boat_prediction.ingest import (
    IngestionError,
    ingest_directory,
    ingest_file,
)


class IngestFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.raw_root = root / "raw"
        self.staged_root = root / "staged"
        self.raw_root.mkdir()
        self.ledger_path = root / "ledger.json"

    def _write_raw(self, name: str, content: str) -> Path:
        path = self.raw_root / name
        path.write_text(content, encoding="utf-8")
        return path

    def test_ingests_new_file_and_stages_a_copy(self) -> None:
        raw_path = self._write_raw("K260101.txt", "payload")

        record = ingest_file(
            raw_path,
            self.raw_root,
            self.staged_root,
            self.ledger_path,
            parser_version="v1",
        )

        staged_path = self.staged_root / "K260101.txt"
        self.assertTrue(staged_path.is_file())
        self.assertEqual(staged_path.read_text(encoding="utf-8"), "payload")
        self.assertEqual(record.parser_version, "v1")
        self.assertTrue(self.ledger_path.is_file())

    def test_rerun_same_file_and_parser_version_is_idempotent(self) -> None:
        raw_path = self._write_raw("K260101.txt", "payload")

        first = ingest_file(
            raw_path, self.raw_root, self.staged_root, self.ledger_path, parser_version="v1"
        )
        second = ingest_file(
            raw_path, self.raw_root, self.staged_root, self.ledger_path, parser_version="v1"
        )

        self.assertEqual(first.ingestion_run_id, second.ingestion_run_id)
        self.assertEqual(first.ingested_at, second.ingested_at)

    def test_different_parser_version_creates_a_new_record(self) -> None:
        raw_path = self._write_raw("K260101.txt", "payload")

        first = ingest_file(
            raw_path, self.raw_root, self.staged_root, self.ledger_path, parser_version="v1"
        )
        second = ingest_file(
            raw_path, self.raw_root, self.staged_root, self.ledger_path, parser_version="v2"
        )

        self.assertNotEqual(first.ingestion_run_id, second.ingestion_run_id)
        self.assertEqual(second.parser_version, "v2")

    def test_missing_file_fails_safely(self) -> None:
        missing = self.raw_root / "missing.txt"

        with self.assertRaises(IngestionError):
            ingest_file(
                missing, self.raw_root, self.staged_root, self.ledger_path, parser_version="v1"
            )

    def test_empty_file_fails_safely(self) -> None:
        empty = self._write_raw("empty.txt", "")

        with self.assertRaises(IngestionError):
            ingest_file(
                empty, self.raw_root, self.staged_root, self.ledger_path, parser_version="v1"
            )

    def test_path_outside_raw_root_fails_safely(self) -> None:
        outside = Path(self._tmp.name) / "outside.txt"
        outside.write_text("payload", encoding="utf-8")

        with self.assertRaises(IngestionError):
            ingest_file(
                outside, self.raw_root, self.staged_root, self.ledger_path, parser_version="v1"
            )

    def test_corrupt_ledger_fails_safely(self) -> None:
        raw_path = self._write_raw("K260101.txt", "payload")
        self.ledger_path.write_text("not json", encoding="utf-8")

        with self.assertRaises(IngestionError):
            ingest_file(
                raw_path, self.raw_root, self.staged_root, self.ledger_path, parser_version="v1"
            )


class IngestDirectoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.raw_root = root / "raw"
        self.staged_root = root / "staged"
        self.raw_root.mkdir()
        self.ledger_path = root / "ledger.json"

    def test_one_bad_file_does_not_block_the_rest_of_the_batch(self) -> None:
        (self.raw_root / "good.txt").write_text("payload", encoding="utf-8")
        (self.raw_root / "bad.txt").write_text("", encoding="utf-8")

        result = ingest_directory(
            self.raw_root, self.staged_root, self.ledger_path, parser_version="v1"
        )

        self.assertEqual(len(result.succeeded), 1)
        self.assertEqual(len(result.failed), 1)
        self.assertEqual(result.failed[0][0], "bad.txt")

    def test_rerun_after_fixing_bad_file_only_adds_the_fixed_one(self) -> None:
        (self.raw_root / "good.txt").write_text("payload", encoding="utf-8")
        bad_path = self.raw_root / "bad.txt"
        bad_path.write_text("", encoding="utf-8")

        first = ingest_directory(
            self.raw_root, self.staged_root, self.ledger_path, parser_version="v1"
        )
        self.assertEqual(len(first.failed), 1)

        bad_path.write_text("now has content", encoding="utf-8")
        second = ingest_directory(
            self.raw_root, self.staged_root, self.ledger_path, parser_version="v1"
        )

        self.assertEqual(len(second.failed), 0)
        self.assertEqual(len(second.succeeded), 2)

    def test_missing_raw_root_fails_safely(self) -> None:
        missing = Path(self._tmp.name) / "does-not-exist"

        with self.assertRaises(IngestionError):
            ingest_directory(missing, self.staged_root, self.ledger_path, parser_version="v1")


if __name__ == "__main__":
    unittest.main()
