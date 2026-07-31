"""Alembic environment.

The URL comes from `DATABASE_URL` (via `boat_prediction.config`) rather
than from alembic.ini, so no password is ever committed. Offline mode
still needs a URL to pick a dialect, so `--sql` runs accept
`-x dialect=postgresql` to emit PostgreSQL DDL without a live server --
which is how the initial migration is checked when no database is
running.
"""

from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from boat_prediction.db.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _offline_url() -> str:
    """A driverless URL for `--sql` runs.

    `-x dialect=<name>` selects which backend's DDL to render; without a
    real connection Alembic only needs enough of a URL to load a
    dialect.
    """
    from boat_prediction.db.session import DatabaseNotConfiguredError, resolve_database_url

    dialect = context.get_x_argument(as_dictionary=True).get("dialect")
    if dialect:
        return f"{dialect}://"
    try:
        return resolve_database_url()
    except DatabaseNotConfiguredError as exc:
        raise SystemExit(
            f"{exc}\nFor a dry run without a database: alembic -x dialect=postgresql upgrade head --sql"
        ) from exc


def run_migrations_offline() -> None:
    context.configure(
        url=_offline_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    from boat_prediction.db.session import resolve_database_url

    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = resolve_database_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
