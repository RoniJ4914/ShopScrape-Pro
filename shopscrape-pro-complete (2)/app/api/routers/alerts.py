from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.alerts.formatters import (
    format_discord_single_event, format_slack_single_event,
    format_email_digest, format_webhook_payload,
)
from app.alerts.grouping import build_digest
from app.alerts.senders import SENDER_REGISTRY
from app.analyzer.events import AnalyzerEvent, EventType, Severity
from app.db import repository
from app.api.deps import Pagination, get_db, pagination, require_api_key
from app.api.schemas import AlertDispatchOut, AlertTestRequest, AlertTestResult, Paginated

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


@router.post("/test-send", response_model=AlertTestResult)
async def test_send(body: AlertTestRequest):
    """
    Send one synthetic event through a channel/destination so Base44's
    "test this webhook" button can confirm a Discord/Slack/email/webhook
    destination is configured correctly *before* it's wired into a real
    `AlertRule`. Deliberately bypasses the rate limiter and never writes
    to `alert_dispatches` -- this isn't a real dispatch, so it shouldn't
    count against a rule's rate limit or show up in the alerts history feed.
    """
    sender_cls = SENDER_REGISTRY.get(body.channel)
    if sender_cls is None:
        raise HTTPException(status_code=400, detail=f"Unknown channel '{body.channel}'")

    test_event = AnalyzerEvent(
        event_type=EventType.PRICE_DECREASE,
        message="Test alert from ShopScrape Pro -- your channel is configured correctly.",
        severity=Severity.INFO,
        product_title="Sample Product",
        vendor="Sample Vendor",
        old_number=39.99,
        new_number=29.99,
    ).to_dict()

    if body.channel == "discord":
        payload = format_discord_single_event(test_event)
    elif body.channel == "slack":
        payload = format_slack_single_event(test_event)
    elif body.channel == "email":
        digest = build_digest("test-store", body.store_name, [test_event])
        payload = format_email_digest(digest, dashboard_url=None)
    else:  # webhook and any future generic channel -- wants structured data, not a rendered message
        digest = build_digest("test-store", body.store_name, [test_event])
        payload = format_webhook_payload(digest)

    sender = sender_cls()
    try:
        sent = await sender.send(body.destination, payload)
    except Exception as exc:
        # Senders are supposed to swallow their own errors and return False,
        # but a test endpoint shouldn't 500 the whole request either way.
        return AlertTestResult(channel=body.channel, destination=body.destination, sent=False, detail=str(exc))

    return AlertTestResult(
        channel=body.channel,
        destination=body.destination,
        sent=sent,
        detail=None if sent else "Send failed -- check server logs for the sender's error.",
    )
