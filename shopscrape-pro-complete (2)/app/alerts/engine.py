"""
Alert Engine pipeline.

    events (from Inventory Analyzer)
            |
            v
    filter by AlertRule (severity, event types, vendor, thresholds)
            |
            v
    rate-limit check (skip if this rule fired too recently)
            |
            v
    group into a digest (or leave ungrouped, per rule.grouped)
            |
            v
    channel-specific formatter
            |
            v
    channel-specific sender
            |
            v
    record dispatch (for future rate-limit checks)

`dispatch_alerts()` is the single public entry point: given a store's
events and its list of configured `AlertRule`s, it runs every rule
independently (a store can fan the same events out to Discord AND email
AND a custom webhook, each with different filters).
"""

from __future__ import annotations

import logging
from typing import List, Dict, Any, Optional

from sqlalchemy.orm import Session

from .preferences import AlertRule, filter_events
from .rate_limiter import is_rate_limited, record_dispatch
from .grouping import build_digest
from .formatters import (
    format_discord_digest, format_discord_single_event,
    format_slack_digest, format_slack_single_event,
    format_email_digest,
    format_webhook_payload,
)
from .senders import SENDER_REGISTRY

logger = logging.getLogger(__name__)


async def _send_for_rule(
    rule: AlertRule,
    store_id: str,
    store_name: str,
    matched_events: List[Dict[str, Any]],
    dashboard_url: Optional[str],
) -> bool:
    sender_cls = SENDER_REGISTRY.get(rule.channel)
    if sender_cls is None:
        logger.warning("Unknown alert channel '%s' for store %s; skipping", rule.channel, store_id)
        return False
    sender = sender_cls()

    if rule.grouped or len(matched_events) > 1:
        digest = build_digest(store_id, store_name, matched_events)
        if rule.channel == "discord":
            payload = format_discord_digest(digest, dashboard_url)
        elif rule.channel == "slack":
            payload = format_slack_digest(digest, dashboard_url)
        elif rule.channel == "email":
            payload = format_email_digest(digest, dashboard_url)
        else:  # webhook and any future generic channel
            payload = format_webhook_payload(digest)
        return await sender.send(rule.destination, payload)

    # Ungrouped: single event, single message (rare -- e.g. a "critical
    # sold-out alert on my top SKU, tell me immediately" rule)
    event = matched_events[0]
    if rule.channel == "discord":
        payload = format_discord_single_event(event)
    elif rule.channel == "slack":
        payload = format_slack_single_event(event)
    elif rule.channel == "email":
        digest = build_digest(store_id, store_name, matched_events)
        payload = format_email_digest(digest, dashboard_url)
    else:
        payload = event
    return await sender.send(rule.destination, payload)


async def dispatch_alerts(
    session: Session,
    store_id: str,
    store_name: str,
    events: List[Dict[str, Any]],
    rules: List[AlertRule],
    dashboard_url: Optional[str] = None,
) -> Dict[str, int]:
    """
    Run every rule against this store's events. Returns a summary of how
    many rules actually sent vs. were skipped (no matches / rate-limited)
    for logging/observability.
    """
    if not events:
        return {"rules_evaluated": len(rules), "sent": 0, "skipped_no_match": 0, "skipped_rate_limited": 0}

    sent = 0
    skipped_no_match = 0
    skipped_rate_limited = 0

    for rule in rules:
        matched = filter_events(events, rule, store_id)
        if not matched:
            skipped_no_match += 1
            continue

        if is_rate_limited(session, store_id, rule):
            logger.info("Rule '%s' (%s) rate-limited for store %s; skipping", rule.label, rule.channel, store_id)
            skipped_rate_limited += 1
            continue

        success = await _send_for_rule(rule, store_id, store_name, matched, dashboard_url)
        if success:
            record_dispatch(session, store_id, rule, len(matched))
            sent += 1
        else:
            logger.warning("Alert send failed for rule '%s' (%s), store %s", rule.label, rule.channel, store_id)

    return {
        "rules_evaluated": len(rules),
        "sent": sent,
        "skipped_no_match": skipped_no_match,
        "skipped_rate_limited": skipped_rate_limited,
    }
