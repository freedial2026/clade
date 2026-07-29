import tempfile
import unittest
from pathlib import Path

from scripts.repo_map import walk


class RepoMapTest(unittest.TestCase):
    def test_skips_large_standard_directories(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src").mkdir()
            (root / "src" / "a.py").write_text("x", encoding="utf-8")
            (root / "node_modules").mkdir()
            (root / "node_modules" / "x.js").write_text("x", encoding="utf-8")
            result = "\n".join(walk(root, 3))
            self.assertIn("src", result)
            self.assertNotIn("node_modules", result)


if __name__ == "__main__":
    unittest.main()
