"""Model registry and reproducibility (P1-T006).

Records, per trained model version: dataset version, feature-set
version, code version, hyperparameters, calibration version, and
evaluation metrics — the lineage required by
.claude/rules/09-ml-data-science.md ("Track dataset, features, code,
model, calibration, and evaluation versions").

Exactly one version can be "active" at a time (the last entry in the
activation history), so a prediction always resolves unambiguously to
one model version. Activating a new version records the previously
active one, so `rollback()` can restore it. Model artifacts are
checksummed (SHA-256) at registration time so a later mismatch
(corrupted or swapped file) is detectable via `verify_artifact()`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from .checksums import sha256_file
from .json_store import read_json_or_default, write_json


class ModelRegistryError(ValueError):
    """Raised for invalid registry operations."""


@dataclass(frozen=True)
class ModelVersion:
    version_id: str
    dataset_version: str
    feature_set_version: str
    code_version: str
    parameters: dict
    calibration_version: str
    evaluation_metrics: dict
    artifact_path: str
    artifact_checksum: str
    registered_at: str


@dataclass(frozen=True)
class ActivationRecord:
    version_id: str
    activated_at: str
    previous_version_id: str | None


class ModelRegistry:
    def __init__(self, registry_path: Path) -> None:
        self._registry_path = registry_path
        self._versions: dict[str, dict] = {}
        self._activations: list[dict] = []
        self._load()

    def _load(self) -> None:
        data = read_json_or_default(self._registry_path, None, error_type=ModelRegistryError)
        if data is None:
            return
        self._versions = dict(data.get("versions", {}))
        self._activations = list(data.get("activations", []))

    def _save(self) -> None:
        write_json(
            self._registry_path, {"versions": self._versions, "activations": self._activations}
        )

    def register(
        self,
        version_id: str,
        *,
        dataset_version: str,
        feature_set_version: str,
        code_version: str,
        parameters: dict,
        calibration_version: str,
        evaluation_metrics: dict,
        artifact_path: Path,
        now: datetime | None = None,
    ) -> ModelVersion:
        if version_id in self._versions:
            raise ModelRegistryError(f"version already registered: {version_id}")
        if not artifact_path.is_file():
            raise ModelRegistryError(f"artifact_path is not an existing file: {artifact_path}")

        entry = ModelVersion(
            version_id=version_id,
            dataset_version=dataset_version,
            feature_set_version=feature_set_version,
            code_version=code_version,
            parameters=dict(parameters),
            calibration_version=calibration_version,
            evaluation_metrics=dict(evaluation_metrics),
            artifact_path=str(artifact_path),
            artifact_checksum=sha256_file(artifact_path),
            registered_at=(now or datetime.now(UTC)).isoformat(),
        )
        self._versions[version_id] = asdict(entry)
        self._save()
        return entry

    def get(self, version_id: str) -> ModelVersion:
        entry = self._versions.get(version_id)
        if entry is None:
            raise ModelRegistryError(f"unknown version_id: {version_id}")
        return ModelVersion(**entry)

    def verify_artifact(self, version_id: str) -> bool:
        """Recompute the artifact's checksum and compare it to what was
        recorded at registration time."""
        entry = self.get(version_id)
        path = Path(entry.artifact_path)
        if not path.is_file():
            return False
        return sha256_file(path) == entry.artifact_checksum

    def activate(self, version_id: str, *, now: datetime | None = None) -> ActivationRecord:
        if version_id not in self._versions:
            raise ModelRegistryError(f"cannot activate unknown version_id: {version_id}")

        previous = self._activations[-1]["version_id"] if self._activations else None
        record = ActivationRecord(
            version_id=version_id,
            activated_at=(now or datetime.now(UTC)).isoformat(),
            previous_version_id=previous,
        )
        self._activations.append(asdict(record))
        self._save()
        return record

    def get_active(self) -> ModelVersion:
        if not self._activations:
            raise ModelRegistryError("no version has been activated")
        return self.get(self._activations[-1]["version_id"])

    def rollback(self, *, now: datetime | None = None) -> ModelVersion:
        """Reactivate the version that was active immediately before the
        current one."""
        if not self._activations:
            raise ModelRegistryError("no version has been activated; nothing to roll back")
        previous_id = self._activations[-1]["previous_version_id"]
        if previous_id is None:
            raise ModelRegistryError("no prior version to roll back to")
        self.activate(previous_id, now=now)
        return self.get_active()
