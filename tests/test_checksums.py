import tempfile
import unittest
from pathlib import Path

from boat_prediction.checksums import sha256_file


class Sha256FileTest(unittest.TestCase):
    def test_matches_a_known_sha256_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.txt"
            path.write_text("hello", encoding="utf-8")

            # sha256("hello") -- a well-known test vector
            self.assertEqual(
                sha256_file(path),
                "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
            )

    def test_same_content_gives_the_same_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "a.txt"
            second = Path(tmp) / "b.txt"
            first.write_text("identical content", encoding="utf-8")
            second.write_text("identical content", encoding="utf-8")

            self.assertEqual(sha256_file(first), sha256_file(second))

    def test_different_content_gives_a_different_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "a.txt"
            second = Path(tmp) / "b.txt"
            first.write_text("content A", encoding="utf-8")
            second.write_text("content B", encoding="utf-8")

            self.assertNotEqual(sha256_file(first), sha256_file(second))


if __name__ == "__main__":
    unittest.main()
