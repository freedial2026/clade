import json
import unittest
from pathlib import Path


class SettingsTest(unittest.TestCase):
    def test_settings_has_approval_layers_and_hooks(self) -> None:
        data = json.loads(Path(".claude/settings.json").read_text(encoding="utf-8"))
        permissions = data["permissions"]
        self.assertTrue(permissions["allow"])
        self.assertTrue(permissions["ask"])
        self.assertTrue(permissions["deny"])
        self.assertIn("PreToolUse", data["hooks"])


if __name__ == "__main__":
    unittest.main()
