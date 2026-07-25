"""
Response models. Every ORM-backed schema sets `from_attributes=True` so
routers can hand a SQLAlchemy row straight to `Model.model_validate(row)`
(or just return it -- FastAPI does this automatically via
`response_model`) without a manual field-by-field mapping layer.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, Generic, List, Optional, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Paginated(BaseModel, Generic[T]):
    items: List[T]
    total: int
    limit: int
    offset: int


# --- Stores ------------------------------------------------------------------

class StoreOut(ORMModel):
    id: str
    name: str
    url: str
    platform: Optional[str] = None
    is_active: bool
    created_at: datetime
    last_scraped_at: Optional[datetime] = None


class StoreDetailOut(StoreOut):
    product_count: int
    variant_count: int
    last_run: Optional["ScrapeRunOut"] = None


class StoreCreate(BaseModel):
    """Body for `POST /stores` -- register a new store for tracking."""

    id: str
    name: str
    url: str
    platform: Optional[str] = None


class ScrapeTriggerOut(BaseModel):
    """Response for `POST /stores/{store_id}/scrape` -- the run was accepted and
    started; poll `GET /scrape-runs/{id}` for its outcome."""

    run_id: int
    store_id: str
    status: str


# --- Variants ------------------------------------------------------------

class VariantOut(ORMModel):
    id: str
    product_id: str
    title: Optional[str] = None
    sku: Optional[str] = None
    price: Optional[float] = None
    currency: Optional[str] = None
    compare_at_price: Optional[float] = None
    available: Optional[bool] = None
    inventory_quantity: Optional[int] = None
    barcode: Optional[str] = None
    weight: Optional[float] = None
    weight_unit: Optional[str] = None
    options: Dict[str, str] = {}
    image_url: Optional[str] = None
    url: Optional[str] = None
    is_active: bool
    first_seen_at: datetime
    last_seen_at: datetime


# --- Products ------------------------------------------------------------

class ProductOut(ORMModel):
    id: str
    store_id: str
    handle: Optional[str] = None
    title: str
    vendor: Optional[str] = None
    product_type: Optional[str] = None
    tags: List[str] = []
    images: List[str] = []
    collections: List[str] = []
    url: Optional[str] = None
    rating: Optional[float] = None
    rating_count: Optional[int] = None
    source_platform: Optional[str] = None
    source_method: Optional[str] = None
    is_active: bool
    first_seen_at: datetime
    last_seen_at: datetime


class ProductDetailOut(ProductOut):
    description: Optional[str] = None
    variants: List[VariantOut] = []


# --- Events ------------------------------------------------------------------

class EventOut(ORMModel):
    id: int
    store_id: str
    run_id: Optional[int] = None
    product_id: Optional[str] = None
    variant_id: Optional[str] = None
    product_title: Optional[str] = None
    vendor: Optional[str] = None
    product_type: Optional[str] = None
    sku: Optional[str] = None
    event_type: str
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    old_number: Optional[float] = None
    new_number: Optional[float] = None
    severity: str
    message: str
    created_at: datetime


# --- Price history ---------------------------------------------------------

class PriceHistoryOut(ORMModel):
    id: int
    variant_id: str
    store_id: str
    price: Optional[float] = None
    compare_at_price: Optional[float] = None
    currency: Optional[str] = None
    recorded_at: datetime


# --- Scrape runs -----------------------------------------------------------

class ScrapeRunOut(ORMModel):
    id: int
    store_id: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    status: str
    products_found: int
    products_new: int
    products_removed: int
    events_generated: int
    method_used: Optional[str] = None
    error_message: Optional[str] = None


StoreDetailOut.model_rebuild()


# --- Alert dispatch history --------------------------------------------------

class AlertTestRequest(BaseModel):
    """Body for `POST /alerts/test-send` -- verify a channel/destination is
    configured correctly by sending a single synthetic event through it.
    This never touches the `alert_dispatches` rate-limit log -- it's meant
    to be run as often as needed while setting up a new channel."""

    channel: str  # discord | slack | email | webhook
    destination: str  # webhook URL, email address, etc. -- channel-specific
    store_name: str = "Test Store"


class AlertTestResult(BaseModel):
    channel: str
    destination: str
    sent: bool
    detail: Optional[str] = None


class AlertDispatchOut(ORMModel):
    id: int
    store_id: str
    channel: str
    rule_key: str
    event_count: int
    sent_at: datetime


# --- Analytics ---------------------------------------------------------------

class VendorCount(BaseModel):
    vendor: str
    product_count: int


class ActiveProduct(BaseModel):
    product_id: str
    product_title: Optional[str] = None
    event_count: int


class AnalyticsOverviewOut(BaseModel):
    store_count: int
    product_count: int
    variant_count: int
    events_by_type: Dict[str, int]
    events_by_severity: Dict[str, int]
    top_vendors: List[VendorCount]
    avg_price_change_pct: Optional[float] = None
    most_active_products: List[ActiveProduct]
    scrape_runs_by_status: Dict[str, int]
    window_since: Optional[datetime] = None


# --- Search --------------------------------------------------------------

class SearchResultsOut(BaseModel):
    stores: List[StoreOut]
    products: List[ProductOut]
    variants: List[VariantOut]


# --- Errors ------------------------------------------------------------------

class ErrorOut(BaseModel):
    detail: str
