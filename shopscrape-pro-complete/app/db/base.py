"""
Database engine + session management.

Defaults to a local SQLite file so the backend runs with zero external
setup. Set DATABASE_URL to point at Postgres in production, e.g.:

    export DATABASE_URL="postgresql+psycopg://user:pass@host:5432/shopscrape"

Nothing elsewhere in the codebase should import sqlite3 or psycopg
directly -- everything goes through the SQLAlchemy models/session defined
here so swapping backends is a one-line env var change.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session, DeclarativeBase


class Base(DeclarativeBase):
    pass


def _default_sqlite_url() -> str:
    db_path = os.path.join(os.getcwd(), "shopscrape.db")
    return f"sqlite:///{db_path}"


DATABASE_URL = os.environ.get("DATABASE_URL", _default_sqlite_url())

_engine_kwargs = {}
if DATABASE_URL.startswith("sqlite"):
    # SQLite needs this to allow use across the async worker threads that
    # the scheduler's thread pool will use.
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
    _engine_kwargs["pool_pre_ping"] = True
else:
    # Postgres: real connection pooling for many concurrent store workers.
    _engine_kwargs["pool_size"] = int(os.environ.get("DB_POOL_SIZE", 10))
    _engine_kwargs["max_overflow"] = int(os.environ.get("DB_MAX_OVERFLOW", 20))
    _engine_kwargs["pool_pre_ping"] = True

engine = create_engine(DATABASE_URL, **_engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def init_db() -> None:
    """Create all tables that don't already exist. Safe to call repeatedly."""
    from app.db import models  # noqa: F401 -- ensures models are registered on Base
    Base.metadata.create_all(bind=engine)


@contextmanager
def get_session() -> Iterator[Session]:
    """
    Standard unit-of-work context manager:

        with get_session() as session:
            repository.create_store(session, ...)
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
