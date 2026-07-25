"""
Trending product detection.

Unlike diff.py/aggregates.py, this genuinely needs the historical event
log -- "trending" means a product has changed unusually often across
recent runs, not just in this one. Kept as its own module (rather than
folded into diff.py) so the pure, DB-free diffing logic stays trivially
unit-testable, while this one is the single place that queries history.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.db.models import InventoryEvent
from .events import AnalyzerEvent, EventType, Severity


def detect_trending(
    session: Session,
    store_id: str,
    lookback_days: int = 7,
    min_event_count: int = 5,
    limit: int = 20,
) -> List[AnalyzerEvent]:
    """
    Flag products with >= `min_event_count` inventory events (price moves,
    restocks, sellouts, etc.) in the last `lookback_days` -- these are the
    "actively moving" products worth surfacing separately from one-off
    single-event alerts.
    """
    since = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    stmt = (
        select(
            InventoryEvent.product_id,
            InventoryEvent.product_title,
            InventoryEvent.vendor,
            func.count(InventoryEvent.id).label("event_count"),
        )
        .where(
            InventoryEvent.store_id == store_id,
            InventoryEvent.created_at >= since,
            InventoryEvent.product_id.is_not(None),
        )
        .group_by(InventoryEvent.product_id, InventoryEvent.product_title, InventoryEvent.vendor)
        .having(func.count(InventoryEvent.id) >= min_event_count)
        .order_by(func.count(InventoryEvent.id).desc())
        .limit(limit)
    )

    rows = session.execute(stmt).all()

    events: List[AnalyzerEvent] = []
    for product_id, product_title, vendor, event_count in rows:
        events.append(AnalyzerEvent(
            event_type=EventType.TRENDING_PRODUCT,
            severity=Severity.INFO,
            product_id=product_id,
            product_title=product_title,
            vendor=vendor,
            new_number=float(event_count),
            message=(
                f'"{product_title}" is trending: {event_count} changes in the last '
                f'{lookback_days} days.'
            ),
        ))
    return events
