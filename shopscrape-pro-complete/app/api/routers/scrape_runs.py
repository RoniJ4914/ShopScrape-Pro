from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import repository
from app.api.deps import Pagination, get_db, pagination, require_api_key
from app.api.schemas import Paginated, ScrapeRunOut

router = APIRouter(prefix="/scrape-runs", tags=["scrape-runs"], dependencies=[Depends(require_api_key)])


@router.get("", response_model=Paginated[ScrapeRunOut])
def list_scrape_runs(
    store_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None, description="running | success | failed | partial"),
    since: Optional[datetime] = Query(None, description="Filters on started_at"),
    until: Optional[datetime] = Query(None),
    sort: Optional[str] = Query("-started_at", description="started_at | finished_at, prefix with - for descending"),
    page: Pagination = Depends(pagination),
    db: Session = Depends(get_db),
):
    rows, total = repository.list_scrape_runs(
        db,
        store_id=store_id, status=status, since=since, until=until,
        sort=sort, limit=page.limit, offset=page.offset,
    )
    return Paginated(items=rows, total=total, limit=page.limit, offset=page.offset)


@router.get("/{run_id}", response_model=ScrapeRunOut)
def get_scrape_run(run_id: int, db: Session = Depends(get_db)):
    run = repository.get_scrape_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Scrape run '{run_id}' not found")
    return run
