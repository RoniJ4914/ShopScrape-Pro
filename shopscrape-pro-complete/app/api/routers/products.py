from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import repository
from app.api.deps import Pagination, get_db, pagination, require_api_key
from app.api.schemas import Paginated, ProductDetailOut, ProductOut

router = APIRouter(prefix="/products", tags=["products"], dependencies=[Depends(require_api_key)])


@router.get("", response_model=Paginated[ProductOut])
def list_products(
    store_id: Optional[str] = Query(None),
    vendor: Optional[str] = Query(None),
    product_type: Optional[str] = Query(None),
    tag: Optional[str] = Query(None),
    q: Optional[str] = Query(None, description="Matches title, vendor, handle, or a variant SKU"),
    is_active: Optional[bool] = Query(True),
    in_stock: Optional[bool] = Query(None, description="Filter to products with (or without) an available variant"),
    min_price: Optional[float] = Query(None, ge=0),
    max_price: Optional[float] = Query(None, ge=0),
    sort: Optional[str] = Query("-last_seen_at", description="title | vendor | first_seen_at | last_seen_at, prefix with - for descending"),
    page: Pagination = Depends(pagination),
    db: Session = Depends(get_db),
):
    if min_price is not None and max_price is not None and min_price > max_price:
        raise HTTPException(status_code=422, detail="min_price cannot exceed max_price")

    rows, total = repository.list_products(
        db,
        store_id=store_id, vendor=vendor, product_type=product_type, tag=tag, q=q,
        is_active=is_active, in_stock=in_stock, min_price=min_price, max_price=max_price,
        sort=sort, limit=page.limit, offset=page.offset,
    )
    return Paginated(items=rows, total=total, limit=page.limit, offset=page.offset)


@router.get("/{product_id}", response_model=ProductDetailOut)
def get_product(product_id: str, db: Session = Depends(get_db)):
    product = repository.get_product(db, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail=f"Product '{product_id}' not found")
    return product
