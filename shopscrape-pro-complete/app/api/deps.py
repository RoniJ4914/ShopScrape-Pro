"""
Shared FastAPI dependencies: a per-request DB session, pagination
params, and optional API-key auth.

Kept deliberately thin -- routers depend on these, nothing here depends
on routers, so there's no import cycle risk as the router list grows.
"""

from __future__ import annotations

import os
from typing import Iterator, Optional

from fastapi import Depends, HTTPException, Query, status
from fastapi.security import APIKeyHeader
from sqlalchemy.orm import Session

from app.db.base import SessionLocal

# --- DB session --------------------------------------------------------------


def get_db() -> Iterator[Session]:
    """
    Per-request session. Unlike `app.db.base.get_session()` (used by the
    scraper/analyzer/alert-engine writers, which commit on success), the
    API is read-only, so there's nothing to commit -- just close.
    """
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def get_write_db() -> Iterator[Session]:
    """
    Per-request session for the small set of write endpoints (registering
    a store, pausing/resuming tracking, kicking off an on-demand scrape).
    Unlike `get_db`, this commits on success and rolls back on error --
    mirrors `app.db.base.get_session()`'s behavior but as a FastAPI
    dependency so routers can `Depends()` it like any other session.
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


# --- Pagination ----------------------------------------------------------

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


class Pagination:
    def __init__(self, limit: int, offset: int):
        self.limit = limit
        self.offset = offset


def pagination(
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT, description="Max rows to return"),
    offset: int = Query(0, ge=0, description="Rows to skip"),
) -> Pagination:
    return Pagination(limit=limit, offset=offset)


# --- Auth ----------------------------------------------------------------
#
# Optional -- only enforced if DASHBOARD_API_KEY is set in the
# environment, so local/dev usage (and the smoke tests) needs no setup.
# Base44 (or any other consumer) sends it back as `X-API-Key`.

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(provided: Optional[str] = Depends(_api_key_header)) -> None:
    expected = os.environ.get("DASHBOARD_API_KEY")
    if not expected:
        return  # auth disabled -- no key configured
    if provided != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing API key")
