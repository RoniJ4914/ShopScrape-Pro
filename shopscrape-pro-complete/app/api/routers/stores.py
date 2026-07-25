from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import repository
from app.db.base import get_session
from app.db.models import Store
from app.api.deps import get_db, get_write_db, require_api_key
from app.api.schemas import ScrapeTriggerOut, StoreCreate, StoreDetailOut, StoreOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/stores", tags=["stores"], dependencies=[Depends(require_api_key)])


@router.get("", response_model=list[StoreOut])
def list_stores(
    active_only: bool = Query(True, description="Only return active stores"),
    db: Session = Depends(get_db),
):
    stmt = select(Store)
    if active_only:
        stmt = stmt.where(Store.is_active.is_(True))
    return db.scalars(stmt.order_by(Store.name)).all()


@router.get("/{store_id}", response_model=StoreDetailOut)
def get_store(store_id: str, db: Session = Depends(get_db)):
    store = repository.get_store(db, store_id)
    if store is None:
        raise HTTPException(status_code=404, detail=f"Store '{store_id}' not found")
    stats = repository.get_store_stats(db, store_id)
    return StoreDetailOut(
        id=store.id,
        name=store.name,
        url=store.url,
        platform=store.platform,
        is_active=store.is_active,
        created_at=store.created_at,
        last_scraped_at=store.last_scraped_at,
        product_count=stats["product_count"],
        variant_count=stats["variant_count"],
        last_run=stats["last_run"],
    )


@router.post("", response_model=StoreOut, status_code=status.HTTP_201_CREATED)
def create_store(body: StoreCreate, db: Session = Depends(get_write_db)):
    """
    Register a store for tracking (or update name/url/platform if
    `id` already exists -- `upsert_store` is idempotent). This is the
    only way a store gets into the DB for the scheduler to pick up; the
    scraper itself never invents stores.
    """
    store = repository.upsert_store(db, body.id, body.name, body.url, body.platform)
    db.flush()
    db.refresh(store)
    return store


@router.post("/{store_id}/activate", response_model=StoreOut)
def activate_store(store_id: str, db: Session = Depends(get_write_db)):
    """Resume tracking a paused store."""
    store = repository.set_store_active(db, store_id, True)
    if store is None:
        raise HTTPException(status_code=404, detail=f"Store '{store_id}' not found")
    db.flush()
    db.refresh(store)
    return store


@router.post("/{store_id}/deactivate", response_model=StoreOut)
def deactivate_store(store_id: str, db: Session = Depends(get_write_db)):
    """
    Pause tracking for a store -- the scheduler should skip inactive
    stores. Existing products/variants/history are left untouched (this
    is a pause, not a delete).
    """
    store = repository.set_store_active(db, store_id, False)
    if store is None:
        raise HTTPException(status_code=404, detail=f"Store '{store_id}' not found")
    db.flush()
    db.refresh(store)
    return store


async def _run_scrape_job(store_id: str, url: str, run_id: int) -> None:
    """
    Background job: run the scraper -> analyzer -> alert-engine chain for
    one store, then mark the scrape run finished/failed. Uses its own
    session (`get_session()`, commit-on-success) since the request's
    session is closed as soon as the 202 response goes out.

    `async def` because `scrape_store` is a coroutine (it awaits the
    fetch and each extractor's `.extract()`) -- FastAPI's `BackgroundTasks`
    awaits async callables itself, so this doesn't need `asyncio.run()`.
    """
    with get_session() as session:
        from app.db.models import ScrapeRun as ScrapeRunModel  # local import avoids a top-level cycle

        run = session.get(ScrapeRunModel, run_id)
        if run is None:
            logger.error("Scrape run %s vanished before the background job started", run_id)
            return
        try:
            from app.scraper.pipeline import scrape_store
            from app.analyzer.pipeline import analyze_and_record

            previous_products = repository.get_current_products(session, store_id)
            new_products, method_used = await scrape_store(store_id, url)
            events = analyze_and_record(session, store_id, run_id, previous_products, new_products)
            counts = repository.upsert_snapshot(session, store_id, run_id, new_products)
            repository.finish_scrape_run(
                session, run, status="success",
                products_found=counts["products_found"],
                products_new=counts["products_new"],
                products_removed=counts["products_removed"],
                events_generated=len(events),
                method_used=method_used,
            )
        except Exception as exc:  # a broken scrape must never crash the API process
            logger.exception("Scrape run %s for store %s failed", run_id, store_id)
            repository.finish_scrape_run(session, run, status="failed", error_message=str(exc))


@router.post("/{store_id}/scrape", response_model=ScrapeTriggerOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_scrape(store_id: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """
    Kick off an on-demand scrape for one store (the "Scrape now" button
    Base44 shows on a store's detail page), instead of waiting for the
    scheduler's next interval. Returns immediately with the new
    `scrape_runs` row in `running` state -- poll `GET /scrape-runs/{id}`
    or `GET /stores/{store_id}` for the outcome.
    """
    store = repository.get_store(db, store_id)
    if store is None:
        raise HTTPException(status_code=404, detail=f"Store '{store_id}' not found")

    with get_session() as write_session:
        run = repository.start_scrape_run(write_session, store_id)
        run_id = run.id

    background_tasks.add_task(_run_scrape_job, store_id, store.url, run_id)
    return ScrapeTriggerOut(run_id=run_id, store_id=store_id, status="running")
