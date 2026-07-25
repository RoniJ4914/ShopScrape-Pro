"""
Dashboard API -- the REST layer Base44 (or any other frontend) consumes.

Read-only: this process never writes to the DB. The scraper/analyzer/
alert-engine write; this just serves what they've written back out as
JSON, from the same database (`DATABASE_URL` -- see `app/db/base.py`).

Run locally:

    pip install -r requirements.txt
    uvicorn app.api.main:app --reload

Deploy: Postgres-first (set DATABASE_URL to a postgresql+psycopg:// URL --
see app/db/base.py), runs anywhere ASGI does (Vercel, Fly, Render, etc).
No app state beyond the DB connection pool, so it's safe to run multiple
instances behind a load balancer.
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.db.base import init_db
from app.api.routers import alerts, analytics, events, price_history, products, scrape_runs, search, stores, variants

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Safe to call repeatedly -- create_all() only creates missing tables.
    # Skippable via env var for deployments that manage schema via
    # migrations instead (e.g. once Alembic is introduced).
    if os.environ.get("SKIP_INIT_DB", "").lower() not in ("1", "true"):
        init_db()
    yield


app = FastAPI(
    title="ShopScrape Pro Dashboard API",
    description="Read-only REST API over scraped store/product/variant/event data.",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS: wide open by default so the Base44-hosted frontend (an origin we
# don't control the domain of ahead of time) can call this directly.
# Tighten via DASHBOARD_CORS_ORIGINS (comma-separated) once the frontend's
# final domain is known -- auth (DASHBOARD_API_KEY, see app/api/deps.py)
# is the actual access control either way.
_cors_origins = os.environ.get("DASHBOARD_CORS_ORIGINS", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if _cors_origins == "*" else [o.strip() for o in _cors_origins.split(",")],
    allow_credentials=_cors_origins != "*",
    allow_methods=["GET"],
    allow_headers=["X-API-Key"],
)


@app.middleware("http")
async def log_slow_requests(request: Request, call_next):
    start = time.monotonic()
    response = await call_next(request)
    elapsed_ms = (time.monotonic() - start) * 1000
    if elapsed_ms > 500:
        logger.warning("Slow request: %s %s took %.0fms", request.method, request.url.path, elapsed_ms)
    response.headers["X-Response-Time-Ms"] = f"{elapsed_ms:.0f}"
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # Never leak internals (DB URLs, stack traces) to the dashboard frontend.
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.get("/health", tags=["health"])
def health():
    return {"status": "ok"}


app.include_router(stores.router)
app.include_router(products.router)
app.include_router(variants.router)
app.include_router(events.router)
app.include_router(price_history.router)
app.include_router(analytics.router)
app.include_router(alerts.router)
app.include_router(scrape_runs.router)
app.include_router(search.router)
