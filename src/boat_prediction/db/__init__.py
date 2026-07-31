"""Relational persistence for parsed BOATRACE source files.

Requires the `app` extra (`pip install -e ".[app]"`) for SQLAlchemy.

- `models` -- the schema (docs/domain/...implementation_guide.md §7.2,
  with the deviations listed in that module's docstring).
- `session` -- engine/session factory reading `DATABASE_URL`.
- `loader` -- parsed dataclasses (`bfile_parser`, `kfile_parser`) into
  rows, idempotent per (race_date, venue_code).
- `load_archive` -- CLI walking `data/raw/boatrace/{B,K}` for a date
  range.
"""
