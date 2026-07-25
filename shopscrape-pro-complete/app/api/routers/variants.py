from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import repository
from app.api.deps import Pagination, get_db, pagination, require_api_key
from app.api.schemas import Paginated, VariantOut

router = APIRouter(prefix="/variants", tags=["variants"], dependencies=[Depends(require_api_key)])


@router.get("", response_model=Paginated[VariantOut])
def list_variants(
    store_id: Optional[str] = Query(None),
    product_id: Optional[str] = Query(None),
    sku: Optional[str] = Query(None),
    available: Optional[bool] = Query(None),
    is_active: Optional[bool] = Query(True),
    min_price: Optional[float] = Query(None, ge=0),
    max_price: Optional[float] = Query(None, ge=0),
    sort: Optional[str] = Query("-last_seen_at", description="price | inventory_quantity | last_seen_at, prefix with - for descending"),
    page: Pagination = Depends(pagination),
    db: Session = Depends(get_db),
):
    if min_price is not None and max_price is not None and min_price > max_price:
        raise HTTPException(status_code=422, detail="min_price cannot exceed max_price")

    rows, total = repository.list_variants(
        db,
        store_id=store_id, product_id=product_id, sku=sku, available=available,
        is_active=is_active, min_price=min_price, max_price=max_price,
        sort=sort, limit=page.limit, offset=page.offset,
    )
    return Paginated(items=rows, total=total, limit=page.limit, offset=page.offset)


@router.get("/{variant_id}", response_model=VariantOut)
def get_variant(variant_id: str, db: Session = Depends(get_db)):
    variant = repository.get_variant(db, variant_id)
    if variant is None:
        raise HTTPException(status_code=404, detail=f"Variant '{variant_id}' not found")
    return variant
