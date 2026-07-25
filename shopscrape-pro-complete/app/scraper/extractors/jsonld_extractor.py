from __future__ import annotations

import json
import re
from typing import List, Any, Dict, Optional

from bs4 import BeautifulSoup

from app.models.product import Product, Variant
from .base import BaseExtractor

_LD_JSON_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _parse_offer(offer: Dict[str, Any]) -> Variant:
    price = offer.get("price")
    try:
        price = float(price) if price is not None else None
    except (TypeError, ValueError):
        price = None

    availability = (offer.get("availability") or "").lower()
    available = None
    if availability:
        available = "instock" in availability or "in_stock" in availability

    return Variant(
        id=str(offer.get("sku") or offer.get("@id") or offer.get("url") or ""),
        sku=offer.get("sku"),
        price=price,
        currency=offer.get("priceCurrency"),
        available=available,
        url=offer.get("url"),
    )


class JsonLdExtractor(BaseExtractor):
    method_name = "jsonld"

    async def extract(self, html: str, page_url: Optional[str] = None) -> List[Product]:
        products: List[Product] = []

        blocks = _LD_JSON_RE.findall(html)
        for block in blocks:
            block = block.strip()
            if not block:
                continue
            try:
                data = json.loads(block)
            except json.JSONDecodeError:
                # Some sites emit multiple JSON objects concatenated or with
                # trailing commas; try a best-effort cleanup once.
                try:
                    data = json.loads(block.rstrip(","))
                except json.JSONDecodeError:
                    continue

            for entry in _as_list(data):
                products.extend(self._parse_entry(entry, page_url))

        return self._tag_provenance(products)

    def _parse_entry(self, entry: Dict[str, Any], page_url: Optional[str]) -> List[Product]:
        if not isinstance(entry, dict):
            return []

        entry_type = entry.get("@type")
        types = _as_list(entry_type)

        # Handle @graph wrappers
        if "@graph" in entry:
            out = []
            for sub in _as_list(entry["@graph"]):
                out.extend(self._parse_entry(sub, page_url))
            return out

        if not any(t == "Product" for t in types if isinstance(t, str)):
            return []

        offers = _as_list(entry.get("offers"))
        variants = [_parse_offer(o) for o in offers if isinstance(o, dict)]
        if not variants:
            # Product with no offers block still has a nominal single variant
            variants = [Variant(id="default")]

        brand = entry.get("brand")
        if isinstance(brand, dict):
            brand = brand.get("name")

        rating = None
        rating_count = None
        agg = entry.get("aggregateRating")
        if isinstance(agg, dict):
            try:
                rating = float(agg.get("ratingValue")) if agg.get("ratingValue") else None
            except (TypeError, ValueError):
                rating = None
            try:
                rating_count = int(agg.get("reviewCount") or agg.get("ratingCount") or 0) or None
            except (TypeError, ValueError):
                rating_count = None

        images = entry.get("image")
        if isinstance(images, str):
            images = [images]
        elif isinstance(images, list):
            images = [i for i in images if isinstance(i, str)]
        else:
            images = []

        url = entry.get("url") or page_url
        native_id = entry.get("sku") or entry.get("productID") or entry.get("@id")

        product = Product(
            id=Product.stable_id(self.store_id, str(native_id) if native_id else None, None, url),
            store_id=self.store_id,
            title=entry.get("name", ""),
            vendor=brand,
            description=entry.get("description"),
            images=images,
            url=url,
            rating=rating,
            rating_count=rating_count,
            variants=variants,
        )
        return [product]
