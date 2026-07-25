from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import repository
from app.db.models import Store
from app.api.deps import get_db, require_api_key
from app.api.schemas import StoreDetailOut, StoreOut

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
