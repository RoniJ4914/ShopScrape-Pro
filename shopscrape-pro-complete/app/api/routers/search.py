from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import repository
from app.api.deps import get_db, require_api_key
from app.api.schemas import SearchResultsOut

router = APIRouter(prefix="/search", tags=["search"], dependencies=[Depends(require_api_key)])


@router.get("", response_model=SearchResultsOut)
def search(
    q: str = Query(..., min_length=1, description="Matches store name/url, product title/vendor/handle/tags, and variant SKU"),
    store_id: Optional[str] = Query(None, description="Scope the search to one store"),
    limit: int = Query(10, ge=1, le=50, description="Max results per entity type (not a global cap)"),
    db: Session = Depends(get_db),
):
    results = repository.search_all(db, q, store_id=store_id, limit=limit)
    return SearchResultsOut(**results)
