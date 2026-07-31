"""Engine and session construction from `config.Settings`.

Kept separate from `models.py` so importing the schema never opens a
connection -- Alembic and the tests both need the metadata without a
live database.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import load_settings


class DatabaseNotConfiguredError(RuntimeError):
    """Raised when DATABASE_URL is absent.

    A distinct type so callers can tell "you have not set this up yet"
    apart from "the database rejected the connection". The message never
    echoes the URL, which carries the password.
    """


def resolve_database_url(url: str | None = None) -> str:
    if url is not None:
        return url
    settings = load_settings()
    if settings.database_url is None:
        raise DatabaseNotConfiguredError(
            "DATABASE_URL is not set; start the local database with "
            "`docker compose up -d db` and export the URL from docs/local-setup.md"
        )
    return settings.database_url


def create_db_engine(url: str | None = None, *, echo: bool = False) -> Engine:
    return create_engine(resolve_database_url(url), echo=echo, future=True)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    """Transactional scope that commits on success and rolls back on any
    exception, so a failed archive day never leaves half a venue loaded."""
    session = create_session_factory(engine)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
