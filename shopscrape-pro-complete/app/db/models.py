"""
ORM models for the Historical Database.

Tables (per spec): Stores, Products, Variants, Inventory Events,
Price History, Scrape Runs, Snapshots.

Design notes:
  - Product/Variant rows represent CURRENT state (upserted every scrape).
    Full point-in-time history lives in PriceHistory + InventoryEvent, not
    by duplicating entire product rows per scrape -- that would blow up
    storage at "millions of products" scale for no analytical benefit.
  - `is_active` soft-deletes products/variants that disappear from a store
    without losing historical rows that reference them (events, price
    history, foreign keys).
  - `fingerprint` columns cache Product.fingerprint()/Variant.fingerprint()
    so the Inventory Analyzer can skip untouched products with a single
    indexed string comparison instead of re-diffing every field.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, List

from sqlalchemy import (
    String, Float, Integer, Boolean, DateTime, ForeignKey, JSON, Text, Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Store(Base):
    __tablename__ = "stores"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(String(1024))
    platform: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_scraped_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    products: Mapped[List["ProductRow"]] = relationship(back_populates="store", cascade="all, delete-orphan")
    scrape_runs: Mapped[List["ScrapeRun"]] = relationship(back_populates="store", cascade="all, delete-orphan")


class ProductRow(Base):
    __tablename__ = "products"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)  # matches Product.id from the scraper
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), index=True)
    handle: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    title: Mapped[str] = mapped_column(String(1024))
    vendor: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    product_type: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    images: Mapped[list] = mapped_column(JSON, default=list)
    collections: Mapped[list] = mapped_column(JSON, default=list)
    url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    rating: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    rating_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    source_platform: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    source_method: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    fingerprint: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    store: Mapped["Store"] = relationship(back_populates="products")
    variants: Mapped[List["VariantRow"]] = relationship(back_populates="product", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_products_store_vendor", "store_id", "vendor"),
        Index("ix_products_store_type", "store_id", "product_type"),
    )


class VariantRow(Base):
    __tablename__ = "variants"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True)
    title: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    sku: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    compare_at_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    available: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    inventory_quantity: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    barcode: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    weight: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    weight_unit: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    image_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)

    fingerprint: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    product: Mapped["ProductRow"] = relationship(back_populates="variants")


class PriceHistory(Base):
    __tablename__ = "price_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    variant_id: Mapped[str] = mapped_column(ForeignKey("variants.id"), index=True)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), index=True)
    price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    compare_at_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)

    __table_args__ = (
        Index("ix_price_history_variant_time", "variant_id", "recorded_at"),
    )


class InventoryEvent(Base):
    __tablename__ = "inventory_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), index=True)
    run_id: Mapped[Optional[int]] = mapped_column(ForeignKey("scrape_runs.id"), nullable=True, index=True)
    product_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    variant_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)

    product_title: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    vendor: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    product_type: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    sku: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    event_type: Mapped[str] = mapped_column(String(64), index=True)
    # e.g. new_product, removed_product, new_variant, removed_variant,
    #      price_increase, price_decrease, restocked, sold_out,
    #      inventory_increase, inventory_decrease,
    #      compare_at_price_added/removed/changed

    old_value: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    new_value: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    old_number: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    new_number: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    severity: Mapped[str] = mapped_column(String(16), default="info", index=True)  # info | warning | critical
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)

    __table_args__ = (
        Index("ix_events_store_time", "store_id", "created_at"),
        Index("ix_events_store_type_time", "store_id", "event_type", "created_at"),
    )


class ScrapeRun(Base):
    __tablename__ = "scrape_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="running")  # running | success | failed | partial
    products_found: Mapped[int] = mapped_column(Integer, default=0)
    products_new: Mapped[int] = mapped_column(Integer, default=0)
    products_removed: Mapped[int] = mapped_column(Integer, default=0)
    events_generated: Mapped[int] = mapped_column(Integer, default=0)
    method_used: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    store: Mapped["Store"] = relationship(back_populates="scrape_runs")


class AlertDispatch(Base):
    """
    Records every alert digest actually sent out, purely so the Alert
    Engine can rate-limit -- "don't send another Discord digest for this
    store within N minutes" -- without needing an in-memory cache that
    would reset on every deploy/restart.
    """
    __tablename__ = "alert_dispatches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), index=True)
    channel: Mapped[str] = mapped_column(String(32), index=True)  # discord | email | slack | webhook | sms
    rule_key: Mapped[str] = mapped_column(String(255), index=True)
    # identifies which alert preference/rule triggered this send, so
    # different rules for the same store+channel rate-limit independently
    event_count: Mapped[int] = mapped_column(Integer, default=0)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)

    __table_args__ = (
        Index("ix_alert_dispatch_lookup", "store_id", "channel", "rule_key", "sent_at"),
    )


class SnapshotRow(Base):
    """
    Lightweight metadata record of a completed scrape's resulting state --
    NOT a duplicate of every product row (that lives in ProductRow/VariantRow,
    upserted in place). `snapshot_hash` is the hash of all product
    fingerprints combined, letting the analyzer/API cheaply confirm whether
    two runs produced an identical store state without re-reading every row.
    """
    __tablename__ = "snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_id: Mapped[str] = mapped_column(ForeignKey("stores.id"), index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("scrape_runs.id"), index=True)
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
    product_count: Mapped[int] = mapped_column(Integer, default=0)
    variant_count: Mapped[int] = mapped_column(Integer, default=0)
    snapshot_hash: Mapped[str] = mapped_column(String(64))

    __table_args__ = (
        Index("ix_snapshots_store_time", "store_id", "taken_at"),
    )
