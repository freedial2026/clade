import unittest

from scripts.model_route import route


class ModelRouteTest(unittest.TestCase):
    def test_mechanical_low_risk_uses_haiku(self) -> None:
        self.assertEqual(route("mechanical format", "low"), "haiku")

    def test_normal_work_uses_sonnet(self) -> None:
        self.assertEqual(route("implement endpoint", "medium"), "sonnet")

    def test_high_risk_architecture_uses_opus(self) -> None:
        self.assertEqual(route("cross-system architecture", "high"), "opus")


if __name__ == "__main__":
    unittest.main()
