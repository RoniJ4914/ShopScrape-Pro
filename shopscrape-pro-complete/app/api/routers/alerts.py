from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import repository
from app.api.deps import Pagination, get_db, pagination, require_api_key
from app.api.schemas import AlertDispatchOut, Paginated

router = APIRouter(prefix="/alerts", tags=["alerts"], dependencies=[Depends(require_api_key)])


@router.get("", response_model=Paginated[AlertDispatchOut])
def list_alert_dispatches(
    store_id: Optional[str] = Query(None),
    channel: Optional[str] = Query(None, description="discord | email | slack | webhook | sms"),
    rule_key: Optional[str] = Query(None, description="The AlertRule.label that fired"),
    since: Optional[datetime] = Query(None),
    until: Optional[datetime] = Query(None),
    page: Pagination = Depends(pagination),
    db: Session = Depends(get_db),
):
    """
    History of alert digests actually sent (`alert_dispatches`), for a
    dashboard "recent alerts" feed. Alert *rules* (which store/channel/
    filters route where) live in application config, not the DB -- see
    `app.alerts.preferences.AlertRule` -- so there's nothing to list or
    edit here yet; this endpoint is the outbound log.
    """
    rows, total = repository.list_alert_dispatches(
        db,
        store_id=store_id, channel=channel, rule_key=rule_key,
        since=since, until=until, limit=page.limit, offset=page.offset,
    )
    return Paginated(items=rows, total=total, limit=page.limit, offset=page.offset)
