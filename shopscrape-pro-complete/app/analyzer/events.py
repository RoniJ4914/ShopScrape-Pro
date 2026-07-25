"""
Event Model definitions for the Inventory Analyzer.

Mirrors the spec's Event Model fields exactly:
  Store Name, Product ID, Variant ID, Product Title, Vendor, Product Type,
  SKU, Old Value, New Value, Old Number, New Number, Severity, Timestamp,
  Human-readable Message.

`AnalyzerEvent` is the in-memory representation used throughout this
module. `to_dict()` converts it to exactly the shape
`app.db.repository.record_events()` expects, keeping the analyzer
decoupled from the ORM.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from enum import Enum
from typing import Optional


class EventType(str, Enum):
    NEW_PRODUCT = "new_product"
    REMOVED_PRODUCT = "removed_product"
    NEW_VARIANT = "new_variant"
    REMOVED_VARIANT = "removed_variant"
    PRICE_INCREASE = "price_increase"
    PRICE_DECREASE = "price_decrease"
    RESTOCKED = "restocked"
    SOLD_OUT = "sold_out"
    INVENTORY_INCREASE = "inventory_increase"
    INVENTORY_DECREASE = "inventory_decrease"
    COMPARE_AT_PRICE_ADDED = "compare_at_price_added"
    COMPARE_AT_PRICE_REMOVED = "compare_at_price_removed"
    COMPARE_AT_PRICE_CHANGED = "compare_at_price_changed"
    TRENDING_PRODUCT = "trending_product"
    BULK_PRICE_CHANGE = "bulk_price_change"
    INVENTORY_SPIKE = "inventory_spike"
    INVENTORY_DROP = "inventory_drop"


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class AnalyzerEvent:
    event_type: EventType
    message: str
    severity: Severity = Severity.INFO
    product_id: Optional[str] = None
    variant_id: Optional[str] = None
    product_title: Optional[str] = None
    vendor: Optional[str] = None
    product_type: Optional[str] = None
    sku: Optional[str] = None
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    old_number: Optional[float] = None
    new_number: Optional[float] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["event_type"] = self.event_type.value
        d["severity"] = self.severity.value
        return d
