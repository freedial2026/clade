# Temporal timestamp model

Implemented in `src/boat_prediction/temporal.py`. Definitions follow
`docs/domain/claude_boatrace_prediction_system_implementation_guide.md`
(§8, データ時点管理).

## Fields

| Field | Meaning |
|---|---|
| `event_time` | When the real-world event happened (e.g. the race was run). |
| `published_at` | When the source provider published the data. |
| `collected_at` | When this system acquired/ingested it (see `ingest.py`'s `ingested_at`). |
| `available_at` | When it became usable for prediction queries. |
| `valid_from` | Start of the period this value is considered current. |
| `valid_to` | End of that period, or unset if still current. |

Ordering enforced by `TemporalRecord`: `event_time <= published_at <=
collected_at <= available_at`, and `valid_from <= valid_to` when
`valid_to` is set.

## Storage vs. display

All timestamps are stored as timezone-aware **UTC** `datetime` values.
`TemporalRecord` rejects naive datetimes and non-UTC offsets at
construction time — conversion to UTC (`to_utc()`) must happen before a
timestamp enters storage. Converting to a local timezone for display
(`for_display()`, using stdlib `zoneinfo`) is a read-time, presentation-only
operation and never changes what is stored.

## Point-in-time queries

`is_available_for_prediction(record, prediction_at)` implements the
project's non-negotiable constraint (`docs/PROJECT_PROFILE.md`):

```
available_at <= prediction_at
```

`filter_available(records, prediction_at)` applies this across a list, and
`is_valid_at(record, as_of)` answers whether a record's `valid_from`/
`valid_to` window covers a given instant. Both are the basis for the
"no future information leakage" tests required before any P1 model work.
