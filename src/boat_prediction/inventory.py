"""Source-file inventory for the P0 data audit stage.

Walks an approved raw-data directory and records, per file: its source
label, relative path, size, SHA-256 checksum, and a `collected_at`
timestamp. It does not parse file contents or write to business tables
(see tasks/P0-T002.md).

Re-running over unchanged files is idempotent: an existing manifest entry
keeps its original `collected_at` as long as the checksum has not changed.
A changed checksum is treated as a new collection event and gets a fresh
`collected_at`. Entries for files removed from disk are preserved in the
manifest as a historical audit trail rather than deleted.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from .checksums import sha256_file
from .json_store import read_json_or_default, write_json

DEFAULT_ALLOWED_SUFFIXES = frozenset({".lzh", ".txt", ".csv", ".zip", ".json"})


class InventoryError(ValueError):
    """Raised when the raw directory or an entry cannot be inventoried safely."""


@dataclass(frozen=True)
class SourceFileRecord:
    source: str
    relative_path: str
    size_bytes: int
    checksum_sha256: str
    collected_at: str


def _load_manifest(manifest_path: Path) -> dict[str, dict]:
    data = read_json_or_default(manifest_path, {}, error_type=InventoryError)
    return {entry["relative_path"]: entry for entry in data.get("files", [])}


def _write_manifest(manifest_path: Path, records: dict[str, dict]) -> None:
    ordered = sorted(records.values(), key=lambda entry: entry["relative_path"])
    write_json(manifest_path, {"files": ordered}, sort_keys=False)


def build_inventory(
    raw_dir: Path,
    manifest_path: Path,
    source: str,
    *,
    allowed_suffixes: frozenset[str] = DEFAULT_ALLOWED_SUFFIXES,
    now: datetime | None = None,
) -> list[SourceFileRecord]:
    if not raw_dir.is_dir():
        raise InventoryError(f"raw_dir is not an existing directory: {raw_dir}")

    resolved_root = raw_dir.resolve()
    timestamp = (now or datetime.now(UTC)).isoformat()
    existing = _load_manifest(manifest_path)
    updated: dict[str, dict] = dict(existing)
    current_records: list[SourceFileRecord] = []

    for path in sorted(raw_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in allowed_suffixes:
            continue
        resolved_path = path.resolve()
        if resolved_root not in resolved_path.parents:
            raise InventoryError(f"file escapes raw_dir: {path}")

        relative_path = path.relative_to(raw_dir).as_posix()
        checksum = sha256_file(path)
        prior = existing.get(relative_path)
        collected_at = (
            prior["collected_at"]
            if prior is not None and prior["checksum_sha256"] == checksum
            else timestamp
        )

        record = SourceFileRecord(
            source=source,
            relative_path=relative_path,
            size_bytes=path.stat().st_size,
            checksum_sha256=checksum,
            collected_at=collected_at,
        )
        current_records.append(record)
        updated[relative_path] = asdict(record)

    _write_manifest(manifest_path, updated)
    return current_records


def _main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw_dir", type=Path)
    parser.add_argument("--source", default="unspecified")
    parser.add_argument(
        "--manifest", type=Path, default=Path("data/manifests/source_inventory.json")
    )
    args = parser.parse_args(argv)

    try:
        records = build_inventory(args.raw_dir, args.manifest, args.source)
    except InventoryError as exc:
        print(f"error: {exc}")
        return 1

    print(f"inventoried {len(records)} file(s) into {args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
