"""
Repository layer -- the only place in the codebase that touches SQLAlchemy
Session objects directly for reads/writes. Everything else (scheduler,
inventory analyzer, API) calls these functions and only ever deals in
plain dataclasses (`app.models.product.Product`/`Variant`) or simple
return values.

This module deliberately does NOT generate InventoryEvents itself -- that
diffing logic belongs to the Inventory Analyzer module. What it provides:

  - store CRUD
  - scrape run lifecycle (start/finish)
  - `get_current_products` -- read the store's current DB state as plain
     Product/Variant dataclasses, for the analyzer to diff against
  - `upsert_snapshot` -- write a freshly-scraped product list into the DB
     as the new current state (soft-deleting anything that disappeared),
     recording price history rows whenever a variant's price changed
  - `record_events` -- bulk-insert InventoryEvent rows (called by the
     analyzer once it has computed them)
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import List, Optional, Dict, Sequence, Tuple

from sqlalchemy import select, func, or_, exists, cast, String
from sqlalchemy.orm import Session

from app.db.models import (
    Store, ProductRow, VariantRow, PriceHistory, InventoryEvent, ScrapeRun, SnapshotRow, AlertDispatch,
)
from app.models.product import Product, Variant


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --- Stores --------------------------------------------------------------

def upsert_store(session: Session, store_id: str, name: str, url: str, platform: Optional[str] = None) -> Store:
    store = session.get(Store, store_id)
    if store is None:
        store = Store(id=store_id, name=name, url=url, platform=platform)
        session.add(store)
    else:
        store.name = name
        store.url = url
        if platform:
            store.platform = platform
    return store


def list_stores(session: Session, active_only: bool = True) -> List[Store]:
    stmt = select(Store)
    if active_only:
        stmt = stmt.where(Store.is_active.is_(True))
    return list(session.scalars(stmt).all())


def set_store_active(session: Session, store_id: str, is_active: bool) -> Optional[Store]:
    """Pause/resume tracking for a store (dashboard activate/deactivate toggle)."""
    store = session.get(Store, store_id)
    if store is None:
        return None
    store.is_active = is_active
    return store


# --- Scrape runs -----------------------------------------------------------

def start_scrape_run(session: Session, store_id: str) -> ScrapeRun:
    run = ScrapeRun(store_id=store_id, status="running")
    session.add(run)
    session.flush()  # populate run.id without committing the outer transaction
    return run


def finish_scrape_run(
    session: Session,
    run: ScrapeRun,
    status: str,
    products_found: int = 0,
    products_new: int = 0,
    products_removed: int = 0,
    events_generated: int = 0,
    method_used: Optional[str] = None,
    error_message: Optional[str] = None,
) -> ScrapeRun:
    run.finished_at = _utcnow()
    run.status = status
    run.products_found = products_found
    run.products_new = products_new
    run.products_removed = products_removed
    run.events_generated = events_generated
    run.method_used = method_used
    run.error_message = error_message

    store = session.get(Store, run.store_id)
    if store:
        store.last_scraped_at = run.finished_at
    return run


# --- Reading current state (for the analyzer to diff against) ------------

def get_current_products(session: Session, store_id: str) -> List[Product]:
    """Return the store's current DB state as plain Product dataclasses."""
    rows = session.scalars(
        select(ProductRow).where(ProductRow.store_id == store_id, ProductRow.is_active.is_(True))
    ).all()

    products: List[Product] = []
    for row in rows:
        variants = [
            Variant(
                id=v.id,
                title=v.title,
                sku=v.sku,
                price=v.price,
                currency=v.currency,
                compare_at_price=v.compare_at_price,
                available=v.available,
                inventory_quantity=v.inventory_quantity,
                barcode=v.barcode,
                weight=v.weight,
                weight_unit=v.weight_unit,
                options=v.options or {},
                image_url=v.image_url,
                url=v.url,
            )
            for v in row.variants if v.is_active
        ]
        products.append(Product(
            id=row.id,
            store_id=row.store_id,
            handle=row.handle,
            title=row.title,
            vendor=row.vendor,
            product_type=row.product_type,
            description=row.description,
            tags=row.tags or [],
            images=row.images or [],
            collections=row.collections or [],
            url=row.url,
            rating=row.rating,
            rating_count=row.rating_count,
            variants=variants,
            source_platform=row.source_platform,
            source_method=row.source_method,
        ))
    return products


# --- Writing a fresh scrape as the new current state ----------------------

def upsert_snapshot(session: Session, store_id: str, run_id: int, products: List[Product]) -> Dict[str, int]:
    """
    Upsert freshly-scraped products/variants as the current state. Products
    that existed before but are absent from this scrape are soft-deleted
    (is_active=False) rather than removed, preserving their event/price
    history. Returns simple counts for the ScrapeRun record.
    """
    seen_product_ids = set()
    seen_variant_ids = set()
    new_count = 0

    for product in products:
        seen_product_ids.add(product.id)
        fp = product.fingerprint()
        row = session.get(ProductRow, product.id)

        if row is None:
            row = ProductRow(id=product.id, store_id=store_id, title=product.title)
            session.add(row)
            new_count += 1

        row.store_id = store_id
        row.handle = product.handle
        row.title = product.title
        row.vendor = product.vendor
        row.product_type = product.product_type
        row.description = product.description
        row.tags = product.tags or []
        row.images = product.images or []
        row.collections = product.collections or []
        row.url = product.url
        row.rating = product.rating
        row.rating_count = product.rating_count
        row.source_platform = product.source_platform
        row.source_method = product.source_method
        row.fingerprint = fp
        row.is_active = True
        row.last_seen_at = _utcnow()

        for variant in product.variants:
            seen_variant_ids.add(variant.id)
            vfp = variant.fingerprint()
            vrow = session.get(VariantRow, variant.id)
            price_changed = vrow is not None and vrow.price != variant.price

            if vrow is None:
                vrow = VariantRow(id=variant.id, product_id=product.id)
                session.add(vrow)

            vrow.product_id = product.id
            vrow.title = variant.title
            vrow.sku = variant.sku
            vrow.currency = variant.currency
            vrow.available = variant.available
            vrow.inventory_quantity = variant.inventory_quantity
            vrow.barcode = variant.barcode
            vrow.weight = variant.weight
            vrow.weight_unit = variant.weight_unit
            vrow.options = variant.options or {}
            vrow.image_url = variant.image_url
            vrow.url = variant.url
            vrow.is_active = True
            vrow.last_seen_at = _utcnow()

            if vrow.fingerprint != vfp or price_changed:
                # Record a price-history row whenever the variant's economic
                # fields actually changed -- not every scrape, to avoid
                # bloating this table with identical duplicate rows.
                session.add(PriceHistory(
                    variant_id=variant.id,
                    store_id=store_id,
                    price=variant.price,
                    compare_at_price=variant.compare_at_price,
                    currency=variant.currency,
                ))

            vrow.price = variant.price
            vrow.compare_at_price = variant.compare_at_price
            vrow.fingerprint = vfp

    # Soft-delete products that vanished from this scrape
    existing_rows = session.scalars(
        select(ProductRow).where(ProductRow.store_id == store_id, ProductRow.is_active.is_(True))
    ).all()
    removed_count = 0
    for row in existing_rows:
        if row.id not in seen_product_ids:
            row.is_active = False
            removed_count += 1
            for vrow in row.variants:
                vrow.is_active = False

    session.flush()

    snapshot_hash = hashlib.sha256(
        "|".join(sorted(p.fingerprint() for p in products)).encode()
    ).hexdigest()
    session.add(SnapshotRow(
        store_id=store_id,
        run_id=run_id,
        product_count=len(products),
        variant_count=sum(len(p.variants) for p in products),
        snapshot_hash=snapshot_hash,
    ))

    return {
        "products_found": len(products),
        "products_new": new_count,
        "products_removed": removed_count,
    }


# --- Alert dispatch / rate limiting ----------------------------------------

def get_last_dispatch_time(session: Session, store_id: str, channel: str, rule_key: str) -> Optional[datetime]:
    row = session.scalars(
        select(AlertDispatch)
        .where(
            AlertDispatch.store_id == store_id,
            AlertDispatch.channel == channel,
            AlertDispatch.rule_key == rule_key,
        )
        .order_by(AlertDispatch.sent_at.desc())
        .limit(1)
    ).first()
    return row.sent_at if row else None


def record_alert_dispatch(session: Session, store_id: str, channel: str, rule_key: str, event_count: int) -> AlertDispatch:
    dispatch = AlertDispatch(store_id=store_id, channel=channel, rule_key=rule_key, event_count=event_count)
    session.add(dispatch)
    return dispatch

def record_events(session: Session, store_id: str, run_id: Optional[int], events: List[dict]) -> int:
    """
    Bulk insert InventoryEvent rows. `events` is a list of plain dicts
    matching the Event Model fields from the spec -- produced by the
    Inventory Analyzer, kept as dicts here rather than importing an
    analyzer-specific type to avoid a circular dependency between modules.
    """
    for e in events:
        session.add(InventoryEvent(
            store_id=store_id,
            run_id=run_id,
            product_id=e.get("product_id"),
            variant_id=e.get("variant_id"),
            product_title=e.get("product_title"),
            vendor=e.get("vendor"),
            product_type=e.get("product_type"),
            sku=e.get("sku"),
            event_type=e["event_type"],
            old_value=e.get("old_value"),
            new_value=e.get("new_value"),
            old_number=e.get("old_number"),
            new_number=e.get("new_number"),
            severity=e.get("severity", "info"),
            message=e["message"],
        ))
    return len(events)


# =====================================================================
# Dashboard API reads (Module 5 -- app/api/)
# =====================================================================
#
# Everything below is read-only support for the Dashboard API. It follows
# the same rule as the rest of this module: it's the only code that
# builds SQLAlchemy queries against these tables -- the API routers call
# these functions and get back either ORM rows (which their Pydantic
# response models know how to serialize `from_attributes`) or plain
# dicts/tuples, never a `Session` or a `select()` statement.
#
# Every `list_*` function returns `(items, total_count)` so a router can
# build a `{items, total, limit, offset}` response without a second
# round-trip to count matching rows.
#
# Filters are plain keyword args (mostly `Optional`, `None` = "don't
# filter on this"), which keeps this file the single place that knows
# how a query-string filter maps to a `WHERE` clause -- routers just
# forward what they parsed from the request.

_PRODUCT_SORT_COLUMNS = {
    "title": ProductRow.title,
    "vendor": ProductRow.vendor,
    "first_seen_at": ProductRow.first_seen_at,
    "last_seen_at": ProductRow.last_seen_at,
}
_VARIANT_SORT_COLUMNS = {
    "price": VariantRow.price,
    "inventory_quantity": VariantRow.inventory_quantity,
    "last_seen_at": VariantRow.last_seen_at,
}
_EVENT_SORT_COLUMNS = {
    "created_at": InventoryEvent.created_at,
    "severity": InventoryEvent.severity,
}
_RUN_SORT_COLUMNS = {
    "started_at": ScrapeRun.started_at,
    "finished_at": ScrapeRun.finished_at,
}


def _apply_sort(stmt, sort: Optional[str], columns: Dict[str, object], default_col):
    """
    `sort` is a query-param string like "title" (ascending) or "-title"
    (descending). Falls back to `default_col` descending if `sort` is
    None or not a recognized column, so a typo'd sort param degrades
    gracefully instead of 500ing.
    """
    if not sort:
        return stmt.order_by(default_col.desc())
    descending = sort.startswith("-")
    key = sort[1:] if descending else sort
    col = columns.get(key, default_col)
    return stmt.order_by(col.desc() if descending else col.asc())


def _count(session: Session, stmt) -> int:
    """Counts matching rows for `stmt` -- always run before .limit()/.offset() are applied."""
    return session.scalar(select(func.count()).select_from(stmt.subquery())) or 0


# --- Stores ----------------------------------------------------------------

def get_store(session: Session, store_id: str) -> Optional[Store]:
    return session.get(Store, store_id)


def get_store_stats(session: Session, store_id: str) -> Dict[str, object]:
    """Cheap summary counts for a store's dashboard header/detail card."""
    product_count = session.scalar(
        select(func.count()).select_from(ProductRow)
        .where(ProductRow.store_id == store_id, ProductRow.is_active.is_(True))
    ) or 0
    variant_count = session.scalar(
        select(func.count()).select_from(VariantRow)
        .join(ProductRow, VariantRow.product_id == ProductRow.id)
        .where(ProductRow.store_id == store_id, VariantRow.is_active.is_(True))
    ) or 0
    last_run = session.scalars(
        select(ScrapeRun).where(ScrapeRun.store_id == store_id)
        .order_by(ScrapeRun.started_at.desc()).limit(1)
    ).first()
    return {
        "product_count": product_count,
        "variant_count": variant_count,
        "last_run": last_run,
    }


# --- Products ----------------------------------------------------------------

def list_products(
    session: Session,
    *,
    store_id: Optional[str] = None,
    vendor: Optional[str] = None,
    product_type: Optional[str] = None,
    tag: Optional[str] = None,
    q: Optional[str] = None,
    is_active: Optional[bool] = True,
    in_stock: Optional[bool] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    sort: Optional[str] = "-last_seen_at",
    limit: int = 50,
    offset: int = 0,
) -> Tuple[Sequence[ProductRow], int]:
    conditions = []
    if store_id:
        conditions.append(ProductRow.store_id == store_id)
    if vendor:
        conditions.append(ProductRow.vendor == vendor)
    if product_type:
        conditions.append(ProductRow.product_type == product_type)
    if is_active is not None:
        conditions.append(ProductRow.is_active.is_(is_active))
    if tag:
        # tags is a JSON list column; portable "contains" check without
        # relying on a Postgres-only JSON operator (works on SQLite too).
        conditions.append(cast(ProductRow.tags, String).like(f'%"{tag}"%'))
    if q:
        like = f"%{q}%"
        conditions.append(or_(
            ProductRow.title.ilike(like),
            ProductRow.vendor.ilike(like),
            ProductRow.handle.ilike(like),
            exists().where(VariantRow.product_id == ProductRow.id, VariantRow.sku.ilike(like)),
        ))
    if in_stock is not None:
        conditions.append(exists().where(
            VariantRow.product_id == ProductRow.id,
            VariantRow.is_active.is_(True),
            VariantRow.available.is_(in_stock),
        ))
    if min_price is not None or max_price is not None:
        price_conditions = [VariantRow.product_id == ProductRow.id, VariantRow.is_active.is_(True)]
        if min_price is not None:
            price_conditions.append(VariantRow.price >= min_price)
        if max_price is not None:
            price_conditions.append(VariantRow.price <= max_price)
        conditions.append(exists().where(*price_conditions))

    base_stmt = select(ProductRow).where(*conditions)
    total = _count(session, base_stmt)
    stmt = _apply_sort(base_stmt, sort, _PRODUCT_SORT_COLUMNS, ProductRow.last_seen_at)
    stmt = stmt.limit(limit).offset(offset)
    rows = session.scalars(stmt).unique().all()
    return rows, total


def get_product(session: Session, product_id: str) -> Optional[ProductRow]:
    return session.get(ProductRow, product_id)


# --- Variants ----------------------------------------------------------------

def list_variants(
    session: Session,
    *,
    store_id: Optional[str] = None,
    product_id: Optional[str] = None,
    sku: Optional[str] = None,
    available: Optional[bool] = None,
    is_active: Optional[bool] = True,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    sort: Optional[str] = "-last_seen_at",
    limit: int = 50,
    offset: int = 0,
) -> Tuple[Sequence[VariantRow], int]:
    conditions = []
    joins_product = store_id is not None
    if product_id:
        conditions.append(VariantRow.product_id == product_id)
    if sku:
        conditions.append(VariantRow.sku == sku)
    if available is not None:
        conditions.append(VariantRow.available.is_(available))
    if is_active is not None:
        conditions.append(VariantRow.is_active.is_(is_active))
    if min_price is not None:
        conditions.append(VariantRow.price >= min_price)
    if max_price is not None:
        conditions.append(VariantRow.price <= max_price)

    base_stmt = select(VariantRow)
    if joins_product:
        base_stmt = base_stmt.join(ProductRow, VariantRow.product_id == ProductRow.id).where(
            ProductRow.store_id == store_id, *conditions
        )
    else:
        base_stmt = base_stmt.where(*conditions)

    total = _count(session, base_stmt)
    stmt = _apply_sort(base_stmt, sort, _VARIANT_SORT_COLUMNS, VariantRow.last_seen_at)
    stmt = stmt.limit(limit).offset(offset)
    rows = session.scalars(stmt).unique().all()
    return rows, total


def get_variant(session: Session, variant_id: str) -> Optional[VariantRow]:
    return session.get(VariantRow, variant_id)


# --- Events ------------------------------------------------------------------

def list_events(
    session: Session,
    *,
    store_id: Optional[str] = None,
    event_types: Optional[Sequence[str]] = None,
    severities: Optional[Sequence[str]] = None,
    vendor: Optional[str] = None,
    product_id: Optional[str] = None,
    variant_id: Optional[str] = None,
    sku: Optional[str] = None,
    run_id: Optional[int] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    sort: Optional[str] = "-created_at",
    limit: int = 50,
    offset: int = 0,
) -> Tuple[Sequence[InventoryEvent], int]:
    conditions = []
    if store_id:
        conditions.append(InventoryEvent.store_id == store_id)
    if event_types:
        conditions.append(InventoryEvent.event_type.in_(event_types))
    if severities:
        conditions.append(InventoryEvent.severity.in_(severities))
    if vendor:
        conditions.append(InventoryEvent.vendor == vendor)
    if product_id:
        conditions.append(InventoryEvent.product_id == product_id)
    if variant_id:
        conditions.append(InventoryEvent.variant_id == variant_id)
    if sku:
        conditions.append(InventoryEvent.sku == sku)
    if run_id is not None:
        conditions.append(InventoryEvent.run_id == run_id)
    if since:
        conditions.append(InventoryEvent.created_at >= since)
    if until:
        conditions.append(InventoryEvent.created_at <= until)

    base_stmt = select(InventoryEvent).where(*conditions)
    total = _count(session, base_stmt)
    stmt = _apply_sort(base_stmt, sort, _EVENT_SORT_COLUMNS, InventoryEvent.created_at)
    stmt = stmt.limit(limit).offset(offset)
    rows = session.scalars(stmt).all()
    return rows, total


# --- Price history -------------------------------------------------------

def list_price_history(
    session: Session,
    *,
    variant_id: Optional[str] = None,
    product_id: Optional[str] = None,
    store_id: Optional[str] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    limit: int = 100,
    offset: int = 0,
) -> Tuple[Sequence[PriceHistory], int]:
    conditions = []
    if variant_id:
        conditions.append(PriceHistory.variant_id == variant_id)
    if store_id:
        conditions.append(PriceHistory.store_id == store_id)
    if since:
        conditions.append(PriceHistory.recorded_at >= since)
    if until:
        conditions.append(PriceHistory.recorded_at <= until)

    base_stmt = select(PriceHistory)
    if product_id:
        base_stmt = base_stmt.join(VariantRow, PriceHistory.variant_id == VariantRow.id).where(
            VariantRow.product_id == product_id, *conditions
        )
    else:
        base_stmt = base_stmt.where(*conditions)

    total = _count(session, base_stmt)
    stmt = base_stmt.order_by(PriceHistory.recorded_at.desc()).limit(limit).offset(offset)
    rows = session.scalars(stmt).all()
    return rows, total


# --- Scrape runs -----------------------------------------------------------

def list_scrape_runs(
    session: Session,
    *,
    store_id: Optional[str] = None,
    status: Optional[str] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    sort: Optional[str] = "-started_at",
    limit: int = 50,
    offset: int = 0,
) -> Tuple[Sequence[ScrapeRun], int]:
    conditions = []
    if store_id:
        conditions.append(ScrapeRun.store_id == store_id)
    if status:
        conditions.append(ScrapeRun.status == status)
    if since:
        conditions.append(ScrapeRun.started_at >= since)
    if until:
        conditions.append(ScrapeRun.started_at <= until)

    base_stmt = select(ScrapeRun).where(*conditions)
    total = _count(session, base_stmt)
    stmt = _apply_sort(base_stmt, sort, _RUN_SORT_COLUMNS, ScrapeRun.started_at)
    stmt = stmt.limit(limit).offset(offset)
    rows = session.scalars(stmt).all()
    return rows, total


def get_scrape_run(session: Session, run_id: int) -> Optional[ScrapeRun]:
    return session.get(ScrapeRun, run_id)


# --- Alert dispatch history --------------------------------------------------

def list_alert_dispatches(
    session: Session,
    *,
    store_id: Optional[str] = None,
    channel: Optional[str] = None,
    rule_key: Optional[str] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    limit: int = 50,
    offset: int = 0,
) -> Tuple[Sequence[AlertDispatch], int]:
    conditions = []
    if store_id:
        conditions.append(AlertDispatch.store_id == store_id)
    if channel:
        conditions.append(AlertDispatch.channel == channel)
    if rule_key:
        conditions.append(AlertDispatch.rule_key == rule_key)
    if since:
        conditions.append(AlertDispatch.sent_at >= since)
    if until:
        conditions.append(AlertDispatch.sent_at <= until)

    base_stmt = select(AlertDispatch).where(*conditions)
    total = _count(session, base_stmt)
    stmt = base_stmt.order_by(AlertDispatch.sent_at.desc()).limit(limit).offset(offset)
    rows = session.scalars(stmt).all()
    return rows, total


# --- Analytics ---------------------------------------------------------------

def get_analytics_overview(
    session: Session,
    *,
    store_id: Optional[str] = None,
    since: Optional[datetime] = None,
) -> Dict[str, object]:
    """
    One aggregate query set backing `GET /analytics`. `since` bounds the
    "in this window" numbers (event/severity/price-change breakdowns,
    scrape success rate); store/product/variant totals are always
    current-state (unbounded by time -- "how big is this catalog right
    now" isn't a windowed question).
    """
    store_scope = [Store.id == store_id] if store_id else []
    product_scope = [ProductRow.store_id == store_id] if store_id else []
    event_scope = [InventoryEvent.store_id == store_id] if store_id else []
    run_scope = [ScrapeRun.store_id == store_id] if store_id else []

    store_count = session.scalar(
        select(func.count()).select_from(Store).where(Store.is_active.is_(True), *store_scope)
    ) or 0
    product_count = session.scalar(
        select(func.count()).select_from(ProductRow)
        .where(ProductRow.is_active.is_(True), *product_scope)
    ) or 0
    variant_count = session.scalar(
        select(func.count()).select_from(VariantRow).join(ProductRow, VariantRow.product_id == ProductRow.id)
        .where(VariantRow.is_active.is_(True), *product_scope)
    ) or 0

    event_time_scope = list(event_scope)
    if since:
        event_time_scope.append(InventoryEvent.created_at >= since)

    events_by_type_rows = session.execute(
        select(InventoryEvent.event_type, func.count())
        .where(*event_time_scope)
        .group_by(InventoryEvent.event_type)
    ).all()
    events_by_severity_rows = session.execute(
        select(InventoryEvent.severity, func.count())
        .where(*event_time_scope)
        .group_by(InventoryEvent.severity)
    ).all()

    top_vendor_rows = session.execute(
        select(ProductRow.vendor, func.count())
        .where(ProductRow.is_active.is_(True), ProductRow.vendor.is_not(None), *product_scope)
        .group_by(ProductRow.vendor)
        .order_by(func.count().desc())
        .limit(10)
    ).all()

    price_change_condition = InventoryEvent.event_type.in_(["price_increase", "price_decrease"])
    avg_pct_change = session.scalar(
        select(func.avg(func.abs(InventoryEvent.new_number - InventoryEvent.old_number) / InventoryEvent.old_number))
        .where(
            price_change_condition, InventoryEvent.old_number.is_not(None),
            InventoryEvent.old_number != 0, InventoryEvent.new_number.is_not(None),
            *event_time_scope,
        )
    )

    most_active_rows = session.execute(
        select(InventoryEvent.product_id, InventoryEvent.product_title, func.count())
        .where(InventoryEvent.product_id.is_not(None), *event_time_scope)
        .group_by(InventoryEvent.product_id, InventoryEvent.product_title)
        .order_by(func.count().desc())
        .limit(10)
    ).all()

    run_time_scope = list(run_scope)
    if since:
        run_time_scope.append(ScrapeRun.started_at >= since)
    run_status_rows = session.execute(
        select(ScrapeRun.status, func.count())
        .where(*run_time_scope)
        .group_by(ScrapeRun.status)
    ).all()

    return {
        "store_count": store_count,
        "product_count": product_count,
        "variant_count": variant_count,
        "events_by_type": {row[0]: row[1] for row in events_by_type_rows},
        "events_by_severity": {row[0]: row[1] for row in events_by_severity_rows},
        "top_vendors": [{"vendor": row[0], "product_count": row[1]} for row in top_vendor_rows],
        "avg_price_change_pct": round(float(avg_pct_change), 4) if avg_pct_change is not None else None,
        "most_active_products": [
            {"product_id": row[0], "product_title": row[1], "event_count": row[2]}
            for row in most_active_rows
        ],
        "scrape_runs_by_status": {row[0]: row[1] for row in run_status_rows},
    }


# --- Cross-entity search -----------------------------------------------------

def search_all(
    session: Session,
    q: str,
    *,
    store_id: Optional[str] = None,
    limit: int = 10,
) -> Dict[str, list]:
    """
    Powers `GET /search?q=...`. Matches store name, product title/vendor/
    handle, and variant SKU -- the fields the spec calls out ("search
    across store/vendor/title/SKU/tags"). Each entity type is capped at
    `limit` independently rather than sharing one global limit, so a
    broad query doesn't let one entity type crowd out the others.
    """
    like = f"%{q}%"

    store_conditions = [or_(Store.name.ilike(like), Store.url.ilike(like))]
    if store_id:
        store_conditions.append(Store.id == store_id)
    stores = session.scalars(
        select(Store).where(*store_conditions).limit(limit)
    ).all()

    product_conditions = [
        ProductRow.is_active.is_(True),
        or_(
            ProductRow.title.ilike(like),
            ProductRow.vendor.ilike(like),
            ProductRow.handle.ilike(like),
            cast(ProductRow.tags, String).like(f'%"{q}"%'),
        ),
    ]
    if store_id:
        product_conditions.append(ProductRow.store_id == store_id)
    products = session.scalars(
        select(ProductRow).where(*product_conditions).limit(limit)
    ).all()

    variant_conditions = [VariantRow.is_active.is_(True), VariantRow.sku.ilike(like)]
    variant_stmt = select(VariantRow).where(*variant_conditions)
    if store_id:
        variant_stmt = variant_stmt.join(ProductRow, VariantRow.product_id == ProductRow.id).where(
            ProductRow.store_id == store_id
        )
    variants = session.scalars(variant_stmt.limit(limit)).all()

    return {"stores": stores, "products": products, "variants": variants}
