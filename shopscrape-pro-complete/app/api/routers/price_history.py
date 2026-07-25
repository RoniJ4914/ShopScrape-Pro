from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import repository
from app.api.deps import Pagination, get_db, pagination, require_api_key
from app.api.schemas import Paginated, PriceHistoryOut

router = APIRouter(prefix="/price-history", tags=["price-history"], dependencies=[Depends(require_api_key)])


@router.get("", response_model=Paginated[PriceHistoryOut])
def list_price_history(
    variant_id: Optional[str] = Query(None),
    product_id: Optional[str] = Query(None, description="All variants of this product"),
    store_id: Optional[str] = Query(None),
    since: Optional[datetime] = Query(None),
    until: Optional[datetime] = Query(None),
    page: Pagination = Depends(pagination),
    db: Session = Depends(get_db),
):
    if not any([variant_id, product_id, store_id]):
        raise HTTPException(
            status_code=422,
            detail="Provide at least one of variant_id, product_id, or store_id to scope the history query",
        )

    rows, total = repository.list_price_history(
        db,
        variant_id=variant_id, product_id=product_id, store_id=store_id,
        since=since, until=until, limit=page.limit, offset=page.offset,
    )
    return Paginated(items=rows, total=total, limit=page.limit, offset=page.offset)
