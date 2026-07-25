from __future__ import annotations

import asyncio
import logging
from typing import List, Any, Dict, Optional
from urllib.parse import urljoin

import httpx

from app.models.product import Product, Variant
from app.scraper.robots import can_fetch, crawl_delay_for
from .base import BaseExtractor

logger = logging.getLogger(__name__)

CANDIDATE_PATHS = [
    "/products.json",
    "/collections/all/products.json",
    "/api/products",
    "/api/catalog",
    "/search.json",
    "/catalog",
]


def _to_float(v: Any) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


class RestExtractor(BaseExtractor):
    method_name = "rest"

    def __init__(self, store_id: str, base_url: str, client: Optional[httpx.AsyncClient] = None,
                 max_pages: int = 500, page_size: int = 250):
        super().__init__(store_id, base_url)
        self._client = client
        self.max_pages = max_pages
        self.page_size = page_size

    async def extract(self, endpoint_hint: Optional[str] = None) -> List[Product]:
        client_owned = self._client is None
        client = self._client or httpx.AsyncClient(timeout=20.0, follow_redirects=True)
        try:
            path = endpoint_hint or await self._discover_endpoint(client)
            if not path:
                return []
            products = await self._paginate(client, path)
            return self._tag_provenance(products)
        finally:
            if client_owned:
                await client.aclose()

    async def _discover_endpoint(self, client: httpx.AsyncClient) -> Optional[str]:
        for path in CANDIDATE_PATHS:
            url = urljoin(self.base_url, path)
            if not await can_fetch(url, client):
                logger.info("robots.txt disallows %s; skipping this candidate REST path", url)
                continue
            try:
                resp = await client.get(url)
            except httpx.HTTPError:
                continue
            if resp.status_code == 200 and "json" in resp.headers.get("content-type", ""):
                try:
                    data = resp.json()
                except ValueError:
                    continue
                if self._contains_products(data):
                    return path
        return None

    @staticmethod
    def _contains_products(data: Any) -> bool:
        if isinstance(data, dict):
            for key in ("products", "items", "results", "data"):
                if key in data and isinstance(data[key], list):
                    return True
        if isinstance(data, list):
            return True
        return False

    async def _paginate(self, client: httpx.AsyncClient, path: str) -> List[Product]:
        products: List[Product] = []
        page = 1
        seen_ids = set()
        url = urljoin(self.base_url, path)
        delay = await crawl_delay_for(url, client)

        while page <= self.max_pages:
            params = {"page": page, "limit": self.page_size}
            try:
                resp = await client.get(url, params=params)
            except httpx.HTTPError:
                break
            if resp.status_code != 200:
                break
            try:
                data = resp.json()
            except ValueError:
                break

            raw_products = self._extract_list(data)
            if not raw_products:
                break

            new_count = 0
            for raw in raw_products:
                product = self._normalize(raw)
                if product.id not in seen_ids:
                    seen_ids.add(product.id)
                    products.append(product)
                    new_count += 1

            if new_count == 0:
                break  # server ignored pagination params -- stop rather than loop forever

            page += 1
            if delay:
                await asyncio.sleep(delay)

        return products

    @staticmethod
    def _extract_list(data: Any) -> List[Dict[str, Any]]:
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict)]
        if isinstance(data, dict):
            for key in ("products", "items", "results", "data"):
                v = data.get(key)
                if isinstance(v, list):
                    return [d for d in v if isinstance(d, dict)]
        return []

    def _normalize(self, raw: Dict[str, Any]) -> Product:
        native_id = raw.get("id") or raw.get("product_id")
        handle = raw.get("handle") or raw.get("slug")

        raw_variants = raw.get("variants") or []
        variants: List[Variant] = []
        for rv in raw_variants:
            if not isinstance(rv, dict):
                continue
            variants.append(Variant(
                id=str(rv.get("id") or rv.get("sku") or len(variants)),
                title=rv.get("title"),
                sku=rv.get("sku"),
                price=_to_float(rv.get("price")),
                currency=rv.get("currency"),
                compare_at_price=_to_float(rv.get("compare_at_price")),
                available=rv.get("available") if isinstance(rv.get("available"), bool) else None,
                barcode=rv.get("barcode"),
                weight=_to_float(rv.get("weight")),
                options={
                    opt.get("name", f"option{i}"): rv.get(f"option{i+1}")
                    for i, opt in enumerate(raw.get("options", []))
                    if rv.get(f"option{i+1}")
                } if raw.get("options") else {},
            ))
        if not variants:
            variants = [Variant(
                id=str(native_id or "default"),
                price=_to_float(raw.get("price")),
                available=raw.get("available") if isinstance(raw.get("available"), bool) else None,
            )]

        images = raw.get("images") or []
        image_urls = []
        for img in images:
            if isinstance(img, str):
                image_urls.append(img)
            elif isinstance(img, dict) and img.get("src"):
                image_urls.append(img["src"])

        return Product(
            id=Product.stable_id(self.store_id, str(native_id) if native_id else None, handle, None),
            store_id=self.store_id,
            handle=handle,
            title=raw.get("title") or raw.get("name") or "",
            vendor=raw.get("vendor"),
            product_type=raw.get("product_type") or raw.get("type"),
            description=raw.get("body_html") or raw.get("description"),
            tags=(raw.get("tags").split(",") if isinstance(raw.get("tags"), str) else raw.get("tags") or []),
            images=image_urls,
            variants=variants,
        )
