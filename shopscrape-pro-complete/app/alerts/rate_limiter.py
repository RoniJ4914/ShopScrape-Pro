"""
Rate limiting, backed by the `alert_dispatches` table so limits survive
restarts/deploys (an in-memory cache would reset and effectively disable
rate limiting on every redeploy, which is exactly when a store might be
mid-way through a burst of changes).
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

from sqlalchemy.orm import Session

from app.db import repository
from .preferences import AlertRule


def is_rate_limited(session: Session, store_id: str, rule: AlertRule) -> bool:
    if rule.rate_limit_seconds <= 0:
        return False

    last_sent = repository.get_last_dispatch_time(session, store_id, rule.channel, rule.label)
    if last_sent is None:
        return False

    elapsed = datetime.now(timezone.utc) - last_sent
    return elapsed < timedelta(seconds=rule.rate_limit_seconds)


def record_dispatch(session: Session, store_id: str, rule: AlertRule, event_count: int) -> None:
    repository.record_alert_dispatch(session, store_id, rule.channel, rule.label, event_count)
