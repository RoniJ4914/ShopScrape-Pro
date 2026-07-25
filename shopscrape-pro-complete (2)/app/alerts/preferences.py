"""
Alert preferences and event filtering.

An `AlertRule` is one row of configuration: "send store X's events to
channel Y, but only if they clear these filters." A store can have
multiple rules (e.g. "Discord gets everything >= warning" AND "email only
gets critical price drops on vendor Z").

Filtering is a pure function -- `matches(event, rule)` -- so it's testable
without touching the DB or any network sender.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Set, Dict, Any, List

from app.analyzer.events import Severity, EventType

_SEVERITY_RANK = {Severity.INFO: 0, Severity.WARNING: 1, Severity.CRITICAL: 2}


def _severity_of(event: Dict[str, Any]) -> Severity:
    return Severity(event.get("severity", "info"))


@dataclass
class AlertRule:
    """One alert routing rule."""
    channel: str  # "discord" | "email" | "slack" | "webhook" | "sms"
    destination: str  # webhook URL, email address, etc. -- sender-specific
    store_ids: Optional[Set[str]] = None       # None = applies to every store
    min_severity: Severity = Severity.INFO
    event_types: Optional[Set[EventType]] = None  # None = all event types
    vendors: Optional[Set[str]] = None         # None = all vendors
    price_threshold: Optional[float] = None     # absolute $ change required for price events
    percentage_threshold: Optional[float] = None  # abs % change required for price/inventory events
    rate_limit_seconds: int = 0                 # 0 = no rate limiting
    grouped: bool = True                        # digest-style summary vs. one message per event
    label: str = "default"                      # used as the DB rate-limit key; keep unique per rule


def _pct_change(old: Optional[float], new: Optional[float]) -> Optional[float]:
    if old in (None, 0) or new is None:
        return None
    return abs(new - old) / abs(old)


def matches(event: Dict[str, Any], rule: AlertRule, store_id: str) -> bool:
    if rule.store_ids is not None and store_id not in rule.store_ids:
        return False

    if _SEVERITY_RANK[_severity_of(event)] < _SEVERITY_RANK[rule.min_severity]:
        return False

    if rule.event_types is not None:
        try:
            event_type = EventType(event["event_type"])
        except ValueError:
            return False
        if event_type not in rule.event_types:
            return False

    if rule.vendors is not None:
        vendor = event.get("vendor")
        if vendor is None or vendor not in rule.vendors:
            return False

    is_price_event = event.get("event_type") in (EventType.PRICE_INCREASE.value, EventType.PRICE_DECREASE.value)
    is_inventory_event = event.get("event_type") in (
        EventType.INVENTORY_INCREASE.value, EventType.INVENTORY_DECREASE.value
    )

    if rule.price_threshold is not None and is_price_event:
        old_n, new_n = event.get("old_number"), event.get("new_number")
        if old_n is None or new_n is None or abs(new_n - old_n) < rule.price_threshold:
            return False

    if rule.percentage_threshold is not None and (is_price_event or is_inventory_event):
        pct = _pct_change(event.get("old_number"), event.get("new_number"))
        if pct is None or pct < rule.percentage_threshold:
            return False

    return True


def filter_events(events: List[Dict[str, Any]], rule: AlertRule, store_id: str) -> List[Dict[str, Any]]:
    return [e for e in events if matches(e, rule, store_id)]
