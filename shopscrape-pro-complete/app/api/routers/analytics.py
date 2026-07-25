from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import repository
from app.api.deps import get_db, require_api_key
from app.api.schemas import AnalyticsOverviewOut

router = APIRouter(prefix="/analytics", tags=["analytics"], dependencies=[Depends(require_api_key)])


@router.get("", response_model=AnalyticsOverviewOut)
def get_analytics(
    store_id: Optional[str] = Query(None, description="Omit for an all-stores rollup"),
    window_days: int = Query(30, ge=1, le=365, description="Size of the window for event/severity/scrape-run breakdowns"),
    db: Session = Depends(get_db),
):
    since = datetime.now(timezone.utc) - timedelta(days=window_days)
    overview = repository.get_analytics_overview(db, store_id=store_id, since=since)
    return AnalyticsOverviewOut(**overview, window_since=since)
