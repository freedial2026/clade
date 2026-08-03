import json
import tempfile
import unittest
from pathlib import Path

from boat_prediction.model_registry import ModelRegistry, ModelRegistryError


class RegistryFixture(unittest.TestCase):
    """Shared setup only -- no tests, so a subclass adds cases rather than
    re-running the ones above it."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.registry_path = root / "registry.json"
        self.artifact_v1 = root / "model_v1.bin"
        self.artifact_v1.write_bytes(b"model-bytes-v1")
        self.artifact_v2 = root / "model_v2.bin"
        self.artifact_v2.write_bytes(b"model-bytes-v2")

    def _register(self, registry: ModelRegistry, version_id: str, artifact: Path):
        return registry.register(
            version_id,
            dataset_version="ds-2026-01",
            feature_set_version="fs-v1",
            code_version="local-dev",
            parameters={"n_estimators": 10},
            calibration_version="cal-v1",
            evaluation_metrics={"log_loss": 1.6},
            artifact_path=artifact,
        )


class ModelRegistryTest(RegistryFixture):
    def test_register_computes_checksum_and_persists(self) -> None:
        registry = ModelRegistry(self.registry_path)

        entry = self._register(registry, "v1", self.artifact_v1)

        self.assertEqual(len(entry.artifact_checksum), 64)
        self.assertTrue(self.registry_path.is_file())

    def test_register_rejects_duplicate_version_id(self) -> None:
        registry = ModelRegistry(self.registry_path)
        self._register(registry, "v1", self.artifact_v1)

        with self.assertRaises(ModelRegistryError):
            self._register(registry, "v1", self.artifact_v1)

    def test_register_rejects_missing_artifact_file(self) -> None:
        registry = ModelRegistry(self.registry_path)
        missing = Path(self._tmp.name) / "does-not-exist.bin"

        with self.assertRaises(ModelRegistryError):
            self._register(registry, "v1", missing)

    def test_verify_artifact_detects_tampering(self) -> None:
        registry = ModelRegistry(self.registry_path)
        self._register(registry, "v1", self.artifact_v1)

        self.assertTrue(registry.verify_artifact("v1"))

        self.artifact_v1.write_bytes(b"tampered-bytes")
        self.assertFalse(registry.verify_artifact("v1"))

    def test_get_active_raises_when_nothing_activated(self) -> None:
        registry = ModelRegistry(self.registry_path)
        self._register(registry, "v1", self.artifact_v1)

        with self.assertRaises(ModelRegistryError):
            registry.get_active()

    def test_activate_then_get_active_resolves_to_exactly_one_version(self) -> None:
        registry = ModelRegistry(self.registry_path)
        self._register(registry, "v1", self.artifact_v1)
        self._register(registry, "v2", self.artifact_v2)

        registry.activate("v1")
        self.assertEqual(registry.get_active().version_id, "v1")

        registry.activate("v2")
        self.assertEqual(registry.get_active().version_id, "v2")

    def test_activating_unknown_version_raises(self) -> None:
        registry = ModelRegistry(self.registry_path)
        with self.assertRaises(ModelRegistryError):
            registry.activate("does-not-exist")

    def test_activation_records_previous_version_as_rollback_metadata(self) -> None:
        registry = ModelRegistry(self.registry_path)
        self._register(registry, "v1", self.artifact_v1)
        self._register(registry, "v2", self.artifact_v2)

        registry.activate("v1")
        record = registry.activate("v2")

        self.assertEqual(record.previous_version_id, "v1")
        self.assertEqual(record.version_id, "v2")

    def test_rollback_restores_the_previous_version(self) -> None:
        registry = ModelRegistry(self.registry_path)
        self._register(registry, "v1", self.artifact_v1)
        self._register(registry, "v2", self.artifact_v2)
        registry.activate("v1")
        registry.activate("v2")

        restored = registry.rollback()

        self.assertEqual(restored.version_id, "v1")
        self.assertEqual(registry.get_active().version_id, "v1")

    def test_rollback_with_no_prior_version_raises(self) -> None:
        registry = ModelRegistry(self.registry_path)
        self._register(registry, "v1", self.artifact_v1)
        registry.activate("v1")

        with self.assertRaises(ModelRegistryError):
            registry.rollback()

    def test_rollback_with_nothing_activated_raises(self) -> None:
        registry = ModelRegistry(self.registry_path)
        self._register(registry, "v1", self.artifact_v1)

        with self.assertRaises(ModelRegistryError):
            registry.rollback()

    def test_registry_persists_and_reloads_from_disk(self) -> None:
        registry = ModelRegistry(self.registry_path)
        self._register(registry, "v1", self.artifact_v1)
        registry.activate("v1")

        reloaded = ModelRegistry(self.registry_path)

        self.assertEqual(reloaded.get_active().version_id, "v1")
        self.assertEqual(reloaded.get("v1").artifact_checksum, registry.get("v1").artifact_checksum)

    def test_corrupt_registry_file_fails_safely(self) -> None:
        self.registry_path.write_text("not json", encoding="utf-8")

        with self.assertRaises(ModelRegistryError):
            ModelRegistry(self.registry_path)


class ModelRegistryRoleTest(RegistryFixture):
    """Roles: the card model and the 直前情報 model are both deployed, and
    neither is a candidate for the other's slot."""

    def test_roles_hold_independent_active_versions(self) -> None:
        registry = ModelRegistry(self.registry_path)
        self._register(registry, "v1", self.artifact_v1)
        self._register(registry, "v2", self.artifact_v2)

        registry.activate("v1")
        registry.activate("v2", role="preview")

        self.assertEqual(registry.get_active().version_id, "v1")
        self.assertEqual(registry.get_active("preview").version_id, "v2")

    def test_activating_one_role_does_not_disturb_another(self) -> None:
        """The failure this prevents: retraining the preview model
        silently swapping the model that predicts every race."""
        registry = ModelRegistry(self.registry_path)
        self._register(registry, "v1", self.artifact_v1)
        self._register(registry, "v2", self.artifact_v2)
        registry.activate("v1")

        registry.activate("v2", role="preview")

        self.assertEqual(registry.get_active().version_id, "v1")

    def test_an_unactivated_role_raises_rather_than_falling_back(self) -> None:
        registry = ModelRegistry(self.registry_path)
        self._register(registry, "v1", self.artifact_v1)
        registry.activate("v1")

        with self.assertRaises(ModelRegistryError):
            registry.get_active("preview")

    def test_rollback_is_scoped_to_its_own_role(self) -> None:
        registry = ModelRegistry(self.registry_path)
        self._register(registry, "v1", self.artifact_v1)
        self._register(registry, "v2", self.artifact_v2)
        registry.activate("v1", role="preview")
        registry.activate("v2", role="preview")
        registry.activate("v1")

        restored = registry.rollback(role="preview")

        self.assertEqual(restored.version_id, "v1")
        self.assertEqual(registry.get_active().version_id, "v1")

    def test_an_activation_written_before_roles_existed_reads_as_default(self) -> None:
        """Backwards compatibility with the registry already on the host,
        whose single activation entry carries no `role` key."""
        registry = ModelRegistry(self.registry_path)
        self._register(registry, "v1", self.artifact_v1)
        registry.activate("v1")

        data = json.loads(self.registry_path.read_text(encoding="utf-8"))
        for entry in data["activations"]:
            entry.pop("role")
        self.registry_path.write_text(json.dumps(data), encoding="utf-8")

        self.assertEqual(ModelRegistry(self.registry_path).get_active().version_id, "v1")

    def test_active_roles_lists_what_is_deployed(self) -> None:
        registry = ModelRegistry(self.registry_path)
        self._register(registry, "v1", self.artifact_v1)
        self._register(registry, "v2", self.artifact_v2)
        registry.activate("v1")
        registry.activate("v2", role="preview")

        self.assertEqual(registry.active_roles(), ["default", "preview"])


if __name__ == "__main__":
    unittest.main()
