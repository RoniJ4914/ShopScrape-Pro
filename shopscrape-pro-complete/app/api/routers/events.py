from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import repository
from app.api.deps import Pagination, get_db, pagination, require_api_key
from app.api.schemas import EventOut, Paginated

router = APIRouter(prefix="/events", tags=["events"], dependencies=[Depends(require_api_key)])


@router.get("", response_model=Paginated[EventOut])
def list_events(
    store_id: Optional[str] = Query(None),
    event_type: Optional[List[str]] = Query(None, description="Repeat to filter multiple types, e.g. ?event_type=sold_out&event_type=price_decrease"),
    severity: Optional[List[str]] = Query(None, description="info | warning | critical -- repeat for multiple"),
    vendor: Optional[str] = Query(None),
    product_id: Optional[str] = Query(None),
    variant_id: Optional[str] = Query(None),
    sku: Optional[str] = Query(None),
    run_id: Optional[int] = Query(None),
    since: Optional[datetime] = Query(None, description="ISO 8601, inclusive lower bound on created_at"),
    until: Optional[datetime] = Query(None, description="ISO 8601, inclusive upper bound on created_at"),
    sort: Optional[str] = Query("-created_at", description="created_at | severity, prefix with - for descending"),
    page: Pagination = Depends(pagination),
    db: Session = Depends(get_db),
):
    rows, total = repository.list_events(
        db,
        store_id=store_id, event_types=event_type, severities=severity, vendor=vendor,
        product_id=product_id, variant_id=variant_id, sku=sku, run_id=run_id,
        since=since, until=until, sort=sort, limit=page.limit, offset=page.offset,
    )
    return Paginated(items=rows, total=total, limit=page.limit, offset=page.offset)
