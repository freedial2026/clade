"""Schema and range validation for the P0 data-audit stage (P0-T006).

Generic record-level validation: type, required fields, enumerations, and
numeric/time ranges. Cross-record checks (duplicates, referential
integrity) are out of scope here (see P0-T007); rolling results into an
overall data-quality score is P0-T010.

Reason codes reuse the ingestion error vocabulary from
docs/domain/claude_boatrace_prediction_system_implementation_guide.md
(§9.3) where a matching code already exists for a field (e.g. `E003`
required column missing, `E005` lane number out of range, `E006` invalid
racer number). Fields with no code defined in that table fall back to the
generic `E900`/`E901`/`E902` codes below.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

GENERIC_TYPE_CODE = "E900"
GENERIC_ENUM_CODE = "E901"
GENERIC_RANGE_CODE = "E902"
REQUIRED_FIELD_CODE = "E003"


def count_reason_codes[T](items: Iterable[T], codes_of: Callable[[T], Iterable[str]]) -> dict[str, int]:
    """Tally how many times each reason code appears across `items`,
    where `codes_of` extracts the (possibly multiple) codes for one item.
    Shared by `ValidationReport`/`IntegrityReport` (elsewhere: `integrity.py`)."""
    counts: dict[str, int] = {}
    for item in items:
        for code in codes_of(item):
            counts[code] = counts.get(code, 0) + 1
    return counts


@dataclass(frozen=True)
class FieldSpec:
    name: str
    field_type: type
    required: bool = True
    enum: frozenset[Any] | None = None
    min_value: Any = None
    max_value: Any = None
    missing_code: str = REQUIRED_FIELD_CODE
    type_code: str = GENERIC_TYPE_CODE
    enum_code: str = GENERIC_ENUM_CODE
    range_code: str = GENERIC_RANGE_CODE


@dataclass(frozen=True)
class FieldFailure:
    field: str
    code: str
    message: str


@dataclass(frozen=True)
class RecordResult:
    record: dict
    failures: tuple[FieldFailure, ...]

    @property
    def is_valid(self) -> bool:
        return not self.failures


@dataclass
class ValidationReport:
    valid: list[dict] = field(default_factory=list)
    rejected: list[RecordResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.valid) + len(self.rejected)

    def reason_code_counts(self) -> dict[str, int]:
        return count_reason_codes(self.rejected, lambda result: [f.code for f in result.failures])


def validate_record(record: dict, schema: list[FieldSpec]) -> RecordResult:
    """Validate one record against a schema. Never mutates `record`."""
    failures: list[FieldFailure] = []
    for spec in schema:
        if spec.name not in record or record[spec.name] is None:
            if spec.required:
                failures.append(
                    FieldFailure(spec.name, spec.missing_code, f"{spec.name} is required")
                )
            continue

        value = record[spec.name]
        if not isinstance(value, spec.field_type):
            failures.append(
                FieldFailure(
                    spec.name,
                    spec.type_code,
                    f"{spec.name} must be {spec.field_type.__name__}, got {type(value).__name__}",
                )
            )
            continue
        if spec.enum is not None and value not in spec.enum:
            failures.append(
                FieldFailure(
                    spec.name, spec.enum_code, f"{spec.name}={value!r} not in allowed set {spec.enum!r}"
                )
            )
            continue
        if spec.min_value is not None and value < spec.min_value:
            failures.append(
                FieldFailure(
                    spec.name,
                    spec.range_code,
                    f"{spec.name}={value!r} below minimum {spec.min_value!r}",
                )
            )
            continue
        if spec.max_value is not None and value > spec.max_value:
            failures.append(
                FieldFailure(
                    spec.name,
                    spec.range_code,
                    f"{spec.name}={value!r} above maximum {spec.max_value!r}",
                )
            )
            continue

    return RecordResult(record=record, failures=tuple(failures))


def validate_records(records: list[dict], schema: list[FieldSpec]) -> ValidationReport:
    report = ValidationReport()
    for record in records:
        result = validate_record(record, schema)
        if result.is_valid:
            report.valid.append(result.record)
        else:
            report.rejected.append(result)
    return report


# Concrete schema for a minimal race_entries row (docs/domain/.../implementation_guide.md §7.2).
# lane_number and registration_number map to the guide's E005/E006 codes;
# fields not yet defined there keep the generic codes above.
RACE_ENTRY_SCHEMA: list[FieldSpec] = [
    FieldSpec(
        name="lane_number",
        field_type=int,
        required=True,
        min_value=1,
        max_value=6,
        missing_code="E003",
        range_code="E005",
    ),
    FieldSpec(
        name="registration_number",
        field_type=int,
        required=True,
        min_value=1,
        missing_code="E003",
        type_code="E006",
        range_code="E006",
    ),
]
