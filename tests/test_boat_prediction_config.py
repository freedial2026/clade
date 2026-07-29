import unittest

from boat_prediction.config import load_settings


class LoadSettingsTest(unittest.TestCase):
    def test_defaults_to_local_env_without_database_url(self) -> None:
        settings = load_settings(env={})
        self.assertEqual(settings.app_env, "local")
        self.assertIsNone(settings.database_url)
        self.assertTrue(settings.is_local)

    def test_reads_provided_environment_values(self) -> None:
        settings = load_settings(env={"APP_ENV": "staging", "DATABASE_URL": "postgresql://x"})
        self.assertEqual(settings.app_env, "staging")
        self.assertEqual(settings.database_url, "postgresql://x")
        self.assertFalse(settings.is_local)


if __name__ == "__main__":
    unittest.main()
