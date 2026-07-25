"""
Core snapshot diffing.

`diff_products(old, new)` is a pure function: two lists of `Product`
dataclasses in, a list of `AnalyzerEvent`s out. No DB session, no I/O --
this is deliberate so it can be unit tested with plain fixtures and
reused identically whether the "old" state came from Postgres, SQLite,
or a JSON fixture file.

Performance note: per the spec's "avoid reprocessing unchanged data"
requirement, products whose top-level fingerprint hasn't changed are
skipped entirely before any per-field comparison happens.
"""

from __future__ import annotations

from typing import List, Dict, Optional

from app.models.product import Product, Variant
from .events import AnalyzerEvent, EventType, Severity
from .severity import price_change_severity, inventory_change_severity


def diff_products(old: List[Product], new: List[Product]) -> List[AnalyzerEvent]:
    old_index: Dict[str, Product] = {p.id: p for p in old}
    new_index: Dict[str, Product] = {p.id: p for p in new}

    events: List[AnalyzerEvent] = []

    # --- Removed products: existed before, absent now ---
    for product_id, product in old_index.items():
        if product_id not in new_index:
            events.append(AnalyzerEvent(
                event_type=EventType.REMOVED_PRODUCT,
                severity=Severity.INFO,
                product_id=product.id,
                product_title=product.title,
                vendor=product.vendor,
                product_type=product.product_type,
                message=f'"{product.title}" was removed from the store.',
            ))

    # --- New + changed products ---
    for product_id, product in new_index.items():
        prior = old_index.get(product_id)

        if prior is None:
            events.append(AnalyzerEvent(
                event_type=EventType.NEW_PRODUCT,
                severity=Severity.INFO,
                product_id=product.id,
                product_title=product.title,
                vendor=product.vendor,
                product_type=product.product_type,
                message=f'New product listed: "{product.title}".',
            ))
            continue

        # Skip untouched products entirely -- cheap fingerprint comparison
        # avoids per-field diffing on the vast majority of products in a
        # store where nothing changed between runs.
        if product.fingerprint() == prior.fingerprint():
            continue

        events.extend(_diff_variants(product, prior))

    return events


def _diff_variants(product: Product, prior: Product) -> List[AnalyzerEvent]:
    events: List[AnalyzerEvent] = []
    old_variants: Dict[str, Variant] = {v.id: v for v in prior.variants}
    new_variants: Dict[str, Variant] = {v.id: v for v in product.variants}

    common_fields = dict(
        product_id=product.id,
        product_title=product.title,
        vendor=product.vendor,
        product_type=product.product_type,
    )

    # Removed variants
    for variant_id, variant in old_variants.items():
        if variant_id not in new_variants:
            events.append(AnalyzerEvent(
                event_type=EventType.REMOVED_VARIANT,
                severity=Severity.INFO,
                variant_id=variant.id,
                sku=variant.sku,
                message=f'Variant "{variant.title or variant.sku or variant.id}" removed from "{product.title}".',
                **common_fields,
            ))

    for variant_id, variant in new_variants.items():
        old_variant = old_variants.get(variant_id)

        if old_variant is None:
            events.append(AnalyzerEvent(
                event_type=EventType.NEW_VARIANT,
                severity=Severity.INFO,
                variant_id=variant.id,
                sku=variant.sku,
                message=f'New variant "{variant.title or variant.sku or variant.id}" added to "{product.title}".',
                **common_fields,
            ))
            continue

        if old_variant.fingerprint() == variant.fingerprint():
            continue

        events.extend(_diff_variant_fields(product, old_variant, variant, common_fields))

    return events


def _diff_variant_fields(product: Product, old: Variant, new: Variant, common_fields: dict) -> List[AnalyzerEvent]:
    events: List[AnalyzerEvent] = []
    variant_label = new.title or new.sku or new.id
    variant_fields = dict(common_fields, variant_id=new.id, sku=new.sku)

    # --- Price change ---
    if old.price is not None and new.price is not None and old.price != new.price:
        event_type = EventType.PRICE_INCREASE if new.price > old.price else EventType.PRICE_DECREASE
        severity = price_change_severity(old.price, new.price)
        direction = "increased" if new.price > old.price else "decreased"
        events.append(AnalyzerEvent(
            event_type=event_type,
            severity=severity,
            old_value=f"{old.price}",
            new_value=f"{new.price}",
            old_number=old.price,
            new_number=new.price,
            message=(
                f'Price {direction} for "{product.title}" ({variant_label}): '
                f'{old.price} -> {new.price} {new.currency or ""}'.strip()
            ),
            **variant_fields,
        ))

    # --- Availability: restocked / sold out ---
    if old.available is not None and new.available is not None and old.available != new.available:
        if new.available:
            events.append(AnalyzerEvent(
                event_type=EventType.RESTOCKED,
                severity=Severity.INFO,
                old_value="sold_out",
                new_value="in_stock",
                message=f'"{product.title}" ({variant_label}) is back in stock.',
                **variant_fields,
            ))
        else:
            events.append(AnalyzerEvent(
                event_type=EventType.SOLD_OUT,
                severity=Severity.WARNING,
                old_value="in_stock",
                new_value="sold_out",
                message=f'"{product.title}" ({variant_label}) just sold out.',
                **variant_fields,
            ))

    # --- Inventory quantity (only when the source actually exposes it) ---
    if old.inventory_quantity is not None and new.inventory_quantity is not None \
            and old.inventory_quantity != new.inventory_quantity:
        event_type = (
            EventType.INVENTORY_INCREASE if new.inventory_quantity > old.inventory_quantity
            else EventType.INVENTORY_DECREASE
        )
        severity = inventory_change_severity(old.inventory_quantity, new.inventory_quantity)
        events.append(AnalyzerEvent(
            event_type=event_type,
            severity=severity,
            old_number=float(old.inventory_quantity),
            new_number=float(new.inventory_quantity),
            message=(
                f'Inventory for "{product.title}" ({variant_label}) changed: '
                f'{old.inventory_quantity} -> {new.inventory_quantity} units.'
            ),
            **variant_fields,
        ))

    # --- Compare-at price (sale pricing signals) ---
    if old.compare_at_price is None and new.compare_at_price is not None:
        events.append(AnalyzerEvent(
            event_type=EventType.COMPARE_AT_PRICE_ADDED,
            severity=Severity.INFO,
            new_value=f"{new.compare_at_price}",
            new_number=new.compare_at_price,
            message=f'"{product.title}" ({variant_label}) now shows a compare-at price of {new.compare_at_price}.',
            **variant_fields,
        ))
    elif old.compare_at_price is not None and new.compare_at_price is None:
        events.append(AnalyzerEvent(
            event_type=EventType.COMPARE_AT_PRICE_REMOVED,
            severity=Severity.INFO,
            old_value=f"{old.compare_at_price}",
            old_number=old.compare_at_price,
            message=f'"{product.title}" ({variant_label}) no longer shows a compare-at price.',
            **variant_fields,
        ))
    elif old.compare_at_price is not None and new.compare_at_price is not None \
            and old.compare_at_price != new.compare_at_price:
        events.append(AnalyzerEvent(
            event_type=EventType.COMPARE_AT_PRICE_CHANGED,
            severity=Severity.INFO,
            old_value=f"{old.compare_at_price}",
            new_value=f"{new.compare_at_price}",
            old_number=old.compare_at_price,
            new_number=new.compare_at_price,
            message=(
                f'Compare-at price for "{product.title}" ({variant_label}) changed: '
                f'{old.compare_at_price} -> {new.compare_at_price}.'
            ),
            **variant_fields,
        ))

    return events
