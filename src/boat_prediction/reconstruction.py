"""Point-in-time historical reconstruction (P0-T009).

Given multiple time-stamped versions of a fact about the same entity
(e.g. a racer's stats, re-published periodically as `racer_term_stats`
rows per docs/domain/.../implementation_guide.md §7.2), reconstruct the
single version that would have been knowable at a chosen
`prediction_at` — reusing `temporal.py`'s availability/validity
predicates rather than re-implementing that logic here.

A fact is a candidate only if:
- `is_available_for_prediction(fact.temporal, prediction_at)` — it was
  already available by prediction time (no future-data leakage), and
- `is_valid_at(fact.temporal, as_of)` — its valid_from/valid_to window
  covers the instant being reconstructed (`as_of`, defaulting to
  `prediction_at` itself).

Among an entity's remaining candidates, the one with the latest
`available_at` wins (the most recently known version).
"""

from __future__ import annotations

from collections.abc import Hashable
from dataclasses import dataclass
from datetime import datetime

from .temporal import TemporalRecord, is_available_for_prediction, is_valid_at


@dataclass(frozen=True)
class VersionedFact:
    entity_key: Hashable
    payload: dict
    temporal: TemporalRecord


def reconstruct_as_of(
    facts: list[VersionedFact],
    prediction_at: datetime,
    *,
    as_of: datetime | None = None,
) -> dict[Hashable, VersionedFact]:
    """Return, per entity_key, the fact version knowable at prediction_at
    and valid at `as_of` (defaults to prediction_at)."""
    effective_as_of = as_of if as_of is not None else prediction_at
    selected: dict[Hashable, VersionedFact] = {}

    for fact in facts:
        if not is_available_for_prediction(fact.temporal, prediction_at):
            continue
        if not is_valid_at(fact.temporal, effective_as_of):
            continue

        current = selected.get(fact.entity_key)
        if current is None or fact.temporal.available_at > current.temporal.available_at:
            selected[fact.entity_key] = fact

    return selected
