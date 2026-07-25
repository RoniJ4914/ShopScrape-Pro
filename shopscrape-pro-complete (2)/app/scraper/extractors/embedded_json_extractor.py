from __future__ import annotations

import json
import re
from typing import List, Any, Dict, Optional, Iterable

from app.models.product import Product, Variant
from .base import BaseExtractor

# Each pattern captures the raw JSON payload assigned to a well-known global.
_MARKERS = {
    "__NEXT_DATA__": re.compile(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', re.DOTALL
    ),
    "__NUXT__": re.compile(r"window\.__NUXT__\s*=\s*(\{.*?\});?\s*(?:</script>|$)", re.DOTALL),
    "__INITIAL_STATE__": re.compile(
        r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\});?\s*(?:</script>|$)", re.DOTALL
    ),
    "__APOLLO_STATE__": re.compile(
        r"window\.__APOLLO_STATE__\s*=\s*(\{.*?\});?\s*(?:</script>|$)", re.DOTALL
    ),
    "__PRELOADED_STATE__": re.compile(
        r"window\.__PRELOADED_STATE__\s*=\s*(\{.*?\});?\s*(?:</script>|$)", re.DOTALL
    ),
}

# Keys that, when found on a dict, strongly suggest "this dict is a product".
_PRODUCT_SHAPE_KEYS = {
    frozenset({"title", "price"}),
    frozenset({"name", "price"}),
    frozenset({"title", "variants"}),
    frozenset({"handle", "variants"}),
    frozenset({"sku", "price"}),
}


def _looks_like_product(d: Dict[str, Any]) -> bool:
    keys = set(k.lower() for k in d.keys())
    for shape in _PRODUCT_SHAPE_KEYS:
        if shape.issubset(keys):
            return True
    return False


def _walk(obj: Any) -> Iterable[Dict[str, Any]]:
    """Recursively walk an arbitrary JSON blob, yielding dicts that look like products."""
    if isinstance(obj, dict):
        if _looks_like_product(obj):
            yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk(item)


def _to_float(v: Any) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


class EmbeddedJsonExtractor(BaseExtractor):
    method_name = "embedded_json"

    async def extract(self, html: str, page_url: Optional[str] = None) -> List[Product]:
        products: List[Product] = []
        seen_ids = set()

        for marker, pattern in _MARKERS.items():
            match = pattern.search(html)
            if not match:
                continue
            raw = match.group(1).strip()
            # __NEXT_DATA__ script tags contain pure JSON (no trailing semicolon issues)
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue

            for candidate in _walk(data):
                product = self._normalize_candidate(candidate, page_url)
                if product and product.id not in seen_ids:
                    seen_ids.add(product.id)
                    products.append(product)

        return self._tag_provenance(products)

    def _normalize_candidate(self, d: Dict[str, Any], page_url: Optional[str]) -> Optional[Product]:
        title = d.get("title") or d.get("name")
        if not title:
            return None

        native_id = d.get("id") or d.get("productId") or d.get("sku")
        handle = d.get("handle") or d.get("slug")

        raw_variants = d.get("variants") or d.get("skus") or []
        variants: List[Variant] = []
        if isinstance(raw_variants, list) and raw_variants:
            for rv in raw_variants:
                if not isinstance(rv, dict):
                    continue
                variants.append(Variant(
                    id=str(rv.get("id") or rv.get("sku") or len(variants)),
                    title=rv.get("title"),
                    sku=rv.get("sku"),
                    price=_to_float(rv.get("price")),
                    currency=rv.get("currency") or d.get("currency"),
                    compare_at_price=_to_float(rv.get("compareAtPrice") or rv.get("compare_at_price")),
                    available=rv.get("available") if isinstance(rv.get("available"), bool) else None,
                    barcode=rv.get("barcode"),
                    weight=_to_float(rv.get("weight")),
                    options={k: v for k, v in (rv.get("options") or {}).items()} if isinstance(rv.get("options"), dict) else {},
                ))
        else:
            # Flat product with price directly on it
            variants.append(Variant(
                id=str(native_id or "default"),
                price=_to_float(d.get("price")),
                currency=d.get("currency"),
                compare_at_price=_to_float(d.get("compareAtPrice") or d.get("compare_at_price")),
                available=d.get("available") if isinstance(d.get("available"), bool) else None,
            ))

        images = d.get("images")
        if isinstance(images, list):
            images = [i if isinstance(i, str) else i.get("src") for i in images if i]
            images = [i for i in images if isinstance(i, str)]
        else:
            images = []

        return Product(
            id=Product.stable_id(self.store_id, str(native_id) if native_id else None, handle, page_url),
            store_id=self.store_id,
            handle=handle,
            title=str(title),
            vendor=d.get("vendor") or d.get("brand"),
            product_type=d.get("productType") or d.get("type"),
            description=d.get("description"),
            tags=d.get("tags") if isinstance(d.get("tags"), list) else [],
            images=images,
            url=page_url,
            variants=variants,
        )
