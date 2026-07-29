import tempfile
import unittest
from pathlib import Path

from boat_prediction.json_store import read_json_or_default, write_json


class LocalError(ValueError):
    pass


class ReadJsonOrDefaultTest(unittest.TestCase):
    def test_returns_default_when_file_does_not_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.json"
            self.assertEqual(read_json_or_default(missing, {"x": 1}, error_type=LocalError), {"x": 1})

    def test_returns_parsed_content_when_file_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data.json"
            path.write_text('{"a": 1}', encoding="utf-8")
            self.assertEqual(read_json_or_default(path, {}, error_type=LocalError), {"a": 1})

    def test_raises_the_given_error_type_on_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text("not json", encoding="utf-8")
            with self.assertRaises(LocalError):
                read_json_or_default(path, {}, error_type=LocalError)


class WriteJsonTest(unittest.TestCase):
    def test_creates_parent_directories_and_writes_readable_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "dir" / "out.json"
            write_json(path, {"b": 2, "a": 1})

            self.assertTrue(path.is_file())
            self.assertEqual(
                read_json_or_default(path, None, error_type=LocalError), {"a": 1, "b": 2}
            )

    def test_output_ends_with_a_trailing_newline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.json"
            write_json(path, {"a": 1})
            self.assertTrue(path.read_text(encoding="utf-8").endswith("\n"))


if __name__ == "__main__":
    unittest.main()
