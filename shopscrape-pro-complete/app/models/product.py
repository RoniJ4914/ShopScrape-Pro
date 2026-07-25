"""
Normalized product data model.

Every extractor (GraphQL, REST, embedded JSON, JSON-LD, microdata, HTML)
must convert its source-specific data into these dataclasses. Nothing
downstream of normalization (analyzer, db, alerts, api) should ever know
which platform or extraction method a product came from.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Variant:
    id: str  # store-native variant id if available, else derived
    title: Optional[str] = None
    sku: Optional[str] = None
    price: Optional[float] = None
    currency: Optional[str] = None
    compare_at_price: Optional[float] = None
    available: Optional[bool] = None
    inventory_quantity: Optional[int] = None  # not always exposed publicly
    barcode: Optional[str] = None
    weight: Optional[float] = None
    weight_unit: Optional[str] = None
    options: Dict[str, str] = field(default_factory=dict)  # e.g. {"Size": "L", "Color": "Red"}
    image_url: Optional[str] = None
    url: Optional[str] = None

    def fingerprint(self) -> str:
        """Stable hash used by the Inventory Analyzer to detect changes cheaply."""
        payload = {
            "sku": self.sku,
            "price": self.price,
            "compare_at_price": self.compare_at_price,
            "available": self.available,
            "inventory_quantity": self.inventory_quantity,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode()
        ).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Product:
    id: str  # store-native product id if available, else derived from handle/url
    store_id: str
    handle: Optional[str] = None
    title: str = ""
    vendor: Optional[str] = None
    product_type: Optional[str] = None
    description: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    images: List[str] = field(default_factory=list)
    collections: List[str] = field(default_factory=list)
    url: Optional[str] = None
    rating: Optional[float] = None
    rating_count: Optional[int] = None
    variants: List[Variant] = field(default_factory=list)

    # Provenance metadata (useful for debugging, never consumed downstream logic)
    source_platform: Optional[str] = None  # "shopify", "woocommerce", "generic-html", ...
    source_method: Optional[str] = None    # "graphql", "rest", "embedded_json", "jsonld", "microdata", "html"
    scraped_at: datetime = field(default_factory=now_utc)

    @property
    def min_price(self) -> Optional[float]:
        prices = [v.price for v in self.variants if v.price is not None]
        return min(prices) if prices else None

    @property
    def max_price(self) -> Optional[float]:
        prices = [v.price for v in self.variants if v.price is not None]
        return max(prices) if prices else None

    @property
    def total_available(self) -> bool:
        return any(v.available for v in self.variants if v.available is not None)

    def fingerprint(self) -> str:
        """
        Cheap top-level hash to decide whether ANY downstream diffing is
        needed at all -- lets the pipeline skip untouched products entirely
        (see Performance / incremental updates in the spec).
        """
        payload = {
            "title": self.title,
            "vendor": self.vendor,
            "product_type": self.product_type,
            "tags": sorted(self.tags),
            "variant_fingerprints": sorted(v.fingerprint() for v in self.variants),
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode()
        ).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["scraped_at"] = self.scraped_at.isoformat()
        return d

    @staticmethod
    def stable_id(store_id: str, native_id: Optional[str], handle: Optional[str], url: Optional[str]) -> str:
        """
        Derive a stable internal product id when a platform doesn't expose
        one directly (e.g. generic HTML sites). Prefer native_id, fall back
        to handle, fall back to a hash of the URL.
        """
        if native_id:
            return f"{store_id}:{native_id}"
        basis = handle or url or ""
        digest = hashlib.sha1(basis.encode()).hexdigest()[:16]
        return f"{store_id}:derived:{digest}"


@dataclass
class Snapshot:
    """A full set of products for one store at one point in time."""
    store_id: str
    run_id: str
    taken_at: datetime = field(default_factory=now_utc)
    products: List[Product] = field(default_factory=list)

    def index_by_id(self) -> Dict[str, Product]:
        return {p.id: p for p in self.products}
