import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from boat_prediction import __version__
from boat_prediction.app import app


class HealthEndpointTest(unittest.TestCase):
    def test_health_returns_ok_and_version(self) -> None:
        client = TestClient(app)
        response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "version": __version__})


class ReadyEndpointTest(unittest.TestCase):
    def test_not_ready_when_database_url_unset(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DATABASE_URL", None)
            client = TestClient(app)
            response = client.get("/ready")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "not_ready")

    def test_not_ready_when_database_unreachable_does_not_leak_the_connection_string(self) -> None:
        secret_dsn = "postgresql://user:supersecretpassword@localhost:1/doesnotexist"
        with patch.dict(os.environ, {"DATABASE_URL": secret_dsn}):
            client = TestClient(app)
            response = client.get("/ready")

        self.assertEqual(response.status_code, 503)
        self.assertNotIn("supersecretpassword", response.text)


if __name__ == "__main__":
    unittest.main()
