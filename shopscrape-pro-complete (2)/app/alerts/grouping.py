"""
Grouping -- collapses a run's (already filtered) events into a digest:
counts per event type, plus a short "top changes" list, matching the
spec's Discord example:

    🚨 ShopShopLive
    68 New Products
    12 Price Drops
    5 Restocks
    Top Changes
    • Nike Air Max
    • Adidas Hoodie
    • Winter Jacket

This module is channel-agnostic -- it produces a plain `AlertDigest`
dataclass; each channel's formatter (discord.py, slack.py, email.py)
decides how to render it.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import List, Dict, Any

from app.analyzer.events import EventType

# Friendly labels for digest counts, in the order they should be displayed.
_LABELS = [
    (EventType.NEW_PRODUCT, "New Products"),
    (EventType.REMOVED_PRODUCT, "Removed Products"),
    (EventType.NEW_VARIANT, "New Variants"),
    (EventType.REMOVED_VARIANT, "Removed Variants"),
    (EventType.PRICE_DECREASE, "Price Drops"),
    (EventType.PRICE_INCREASE, "Price Increases"),
    (EventType.RESTOCKED, "Restocks"),
    (EventType.SOLD_OUT, "Sold Out"),
    (EventType.INVENTORY_INCREASE, "Inventory Increases"),
    (EventType.INVENTORY_DECREASE, "Inventory Decreases"),
    (EventType.COMPARE_AT_PRICE_ADDED, "New Sales"),
    (EventType.COMPARE_AT_PRICE_REMOVED, "Sales Ended"),
    (EventType.COMPARE_AT_PRICE_CHANGED, "Sale Price Changes"),
    (EventType.TRENDING_PRODUCT, "Trending Products"),
    (EventType.BULK_PRICE_CHANGE, "Bulk Repricing Events"),
    (EventType.INVENTORY_SPIKE, "Inventory Spikes"),
    (EventType.INVENTORY_DROP, "Inventory Drops"),
]

# Event types worth surfacing by name in "Top Changes", roughly in order
# of how interesting/actionable they are.
_TOP_CHANGE_PRIORITY = [
    EventType.SOLD_OUT,
    EventType.PRICE_DECREASE,
    EventType.RESTOCKED,
    EventType.PRICE_INCREASE,
    EventType.NEW_PRODUCT,
    EventType.COMPARE_AT_PRICE_ADDED,
]


@dataclass
class AlertDigest:
    store_id: str
    store_name: str
    total_events: int
    counts: Dict[str, int] = field(default_factory=dict)
    top_changes: List[str] = field(default_factory=list)
    events: List[Dict[str, Any]] = field(default_factory=list)  # raw, for channels that want detail (webhook)


def build_digest(store_id: str, store_name: str, events: List[Dict[str, Any]], top_n: int = 5) -> AlertDigest:
    counts = Counter(e["event_type"] for e in events)

    ordered_counts = {}
    for event_type, label in _LABELS:
        if counts.get(event_type.value):
            ordered_counts[label] = counts[event_type.value]

    top_changes: List[str] = []
    seen_titles = set()
    for event_type in _TOP_CHANGE_PRIORITY:
        if len(top_changes) >= top_n:
            break
        for e in events:
            if len(top_changes) >= top_n:
                break
            if e["event_type"] != event_type.value:
                continue
            title = e.get("product_title")
            if not title or title in seen_titles:
                continue
            seen_titles.add(title)
            top_changes.append(title)

    return AlertDigest(
        store_id=store_id,
        store_name=store_name,
        total_events=len(events),
        counts=ordered_counts,
        top_changes=top_changes,
        events=events,
    )
