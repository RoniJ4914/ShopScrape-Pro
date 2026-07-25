"""
Aggregate detectors -- operate over the full set of events produced by a
single run's `diff_products()` call, looking for store-wide patterns
rather than single-product changes: bulk price changes, inventory spikes,
inventory drops. Also pure functions, no DB dependency.

Thresholds are configurable per call so a store with 5 products and a
store with 50,000 products don't share the same "bulk" definition --
callers can pass either a fixed count or a percentage of the store's
total product count.
"""

from __future__ import annotations

from typing import List

from .events import AnalyzerEvent, EventType, Severity


def detect_bulk_and_spikes(
    events: List[AnalyzerEvent],
    total_product_count: int,
    bulk_price_change_pct: float = 0.05,   # 5% of catalog changing price at once
    bulk_price_change_min: int = 10,       # ...but always trigger past this absolute count
    inventory_spike_pct: float = 0.05,
    inventory_spike_min: int = 10,
) -> List[AnalyzerEvent]:
    summary_events: List[AnalyzerEvent] = []

    price_change_count = sum(
        1 for e in events if e.event_type in (EventType.PRICE_INCREASE, EventType.PRICE_DECREASE)
    )
    restock_count = sum(1 for e in events if e.event_type == EventType.RESTOCKED)
    sold_out_count = sum(1 for e in events if e.event_type == EventType.SOLD_OUT)
    new_product_count = sum(1 for e in events if e.event_type == EventType.NEW_PRODUCT)
    removed_product_count = sum(1 for e in events if e.event_type == EventType.REMOVED_PRODUCT)

    bulk_price_threshold = max(bulk_price_change_min, int(total_product_count * bulk_price_change_pct))
    if price_change_count >= bulk_price_threshold and price_change_count > 0:
        severity = Severity.CRITICAL if price_change_count >= bulk_price_threshold * 2 else Severity.WARNING
        summary_events.append(AnalyzerEvent(
            event_type=EventType.BULK_PRICE_CHANGE,
            severity=severity,
            new_number=float(price_change_count),
            message=f"{price_change_count} products changed price in this run -- looks like a bulk repricing.",
        ))

    spike_threshold = max(inventory_spike_min, int(total_product_count * inventory_spike_pct))

    inventory_up_signal = restock_count + new_product_count
    if inventory_up_signal >= spike_threshold and inventory_up_signal > 0:
        summary_events.append(AnalyzerEvent(
            event_type=EventType.INVENTORY_SPIKE,
            severity=Severity.WARNING,
            new_number=float(inventory_up_signal),
            message=(
                f"Inventory spike: {new_product_count} new products and {restock_count} restocks "
                f"in this run."
            ),
        ))

    inventory_down_signal = sold_out_count + removed_product_count
    if inventory_down_signal >= spike_threshold and inventory_down_signal > 0:
        severity = Severity.CRITICAL if inventory_down_signal >= spike_threshold * 2 else Severity.WARNING
        summary_events.append(AnalyzerEvent(
            event_type=EventType.INVENTORY_DROP,
            severity=severity,
            new_number=float(inventory_down_signal),
            message=(
                f"Inventory drop: {sold_out_count} products sold out and {removed_product_count} "
                f"products removed in this run."
            ),
        ))

    return summary_events
