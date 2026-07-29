"""Raw ingestion staging with idempotency (P0-T004).

Copies validated raw files into an immutable `data/staged/` area and
records an ingestion ledger keyed by `(source_file_hash, parser_version)`.
Re-running ingestion over the same file/parser combination is a no-op: it
returns the existing record rather than re-staging or creating a
duplicate entry (docs/domain/.../implementation_guide.md §9.2: dedupe by
`source_file_hash` + `parser_version`, upsert, unique constraint).

Content parsing/normalization into business tables is out of scope here
(see the RAW -> STAGED -> parse -> DB pipeline in the same doc, §9.1);
this stage only validates and stages files, and records lineage.

`ingest_directory` processes files independently so that one file's
failure does not block the rest of the batch, and a later re-run only
needs to retry the files that failed (partial-failure recovery).
"""

from __future__ import annotations

import shutil
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .checksums import sha256_file
from .inventory import DEFAULT_ALLOWED_SUFFIXES
from .json_store import read_json_or_default, write_json


class IngestionError(ValueError):
    """Raised when a raw file cannot be ingested safely."""


@dataclass(frozen=True)
class IngestionRecord:
    source_file_hash: str
    parser_version: str
    relative_path: str
    staged_path: str
    ingestion_run_id: str
    ingested_at: str


@dataclass
class IngestionBatchResult:
    succeeded: list[IngestionRecord] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)


def _ledger_key(source_file_hash: str, parser_version: str) -> str:
    return f"{source_file_hash}:{parser_version}"


def _load_ledger(ledger_path: Path) -> dict[str, dict]:
    data = read_json_or_default(ledger_path, {}, error_type=IngestionError)
    return dict(data.get("runs", {}))


def _write_ledger(ledger_path: Path, runs: dict[str, dict]) -> None:
    write_json(ledger_path, {"runs": runs})


def ingest_file(
    raw_path: Path,
    raw_root: Path,
    staged_root: Path,
    ledger_path: Path,
    *,
    parser_version: str,
    now: datetime | None = None,
) -> IngestionRecord:
    """Stage one raw file and record it in the ledger. Idempotent per
    (checksum, parser_version): a repeat call returns the existing record
    unchanged instead of copying again."""
    if not raw_path.is_file():
        raise IngestionError(f"raw_path is not an existing file: {raw_path}")
    if raw_path.stat().st_size == 0:
        raise IngestionError(f"raw_path is empty: {raw_path}")
    try:
        relative_path = raw_path.resolve().relative_to(raw_root.resolve())
    except ValueError as exc:
        raise IngestionError(f"raw_path is not inside raw_root: {raw_path}") from exc

    checksum = sha256_file(raw_path)
    ledger = _load_ledger(ledger_path)
    key = _ledger_key(checksum, parser_version)

    existing = ledger.get(key)
    if existing is not None:
        return IngestionRecord(**existing)

    staged_path = staged_root / relative_path
    staged_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(raw_path, staged_path)

    record = IngestionRecord(
        source_file_hash=checksum,
        parser_version=parser_version,
        relative_path=relative_path.as_posix(),
        staged_path=staged_path.as_posix(),
        ingestion_run_id=str(uuid.uuid4()),
        ingested_at=(now or datetime.now(UTC)).isoformat(),
    )
    ledger[key] = asdict(record)
    _write_ledger(ledger_path, ledger)
    return record


def ingest_directory(
    raw_root: Path,
    staged_root: Path,
    ledger_path: Path,
    *,
    parser_version: str,
    allowed_suffixes: frozenset[str] = DEFAULT_ALLOWED_SUFFIXES,
    now: datetime | None = None,
) -> IngestionBatchResult:
    """Ingest every approved file under raw_root. Each file is independent:
    one failure is recorded and does not stop the rest of the batch, and a
    later re-run will only need to redo the failed ones (already-ingested
    files are skipped via the idempotency check in `ingest_file`)."""
    if not raw_root.is_dir():
        raise IngestionError(f"raw_root is not an existing directory: {raw_root}")

    result = IngestionBatchResult()
    for path in sorted(raw_root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in allowed_suffixes:
            continue
        try:
            record = ingest_file(
                path,
                raw_root,
                staged_root,
                ledger_path,
                parser_version=parser_version,
                now=now,
            )
        except IngestionError as exc:
            result.failed.append((path.relative_to(raw_root).as_posix(), str(exc)))
            continue
        result.succeeded.append(record)
    return result


def _main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw_root", type=Path)
    parser.add_argument("staged_root", type=Path)
    parser.add_argument("--parser-version", required=True)
    parser.add_argument(
        "--ledger", type=Path, default=Path("data/manifests/ingestion_ledger.json")
    )
    args = parser.parse_args(argv)

    try:
        result = ingest_directory(
            args.raw_root,
            args.staged_root,
            args.ledger,
            parser_version=args.parser_version,
        )
    except IngestionError as exc:
        print(f"error: {exc}")
        return 1

    print(f"ingested {len(result.succeeded)} file(s), {len(result.failed)} failed")
    for relative_path, message in result.failed:
        print(f"  failed: {relative_path}: {message}")
    return 1 if result.failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
