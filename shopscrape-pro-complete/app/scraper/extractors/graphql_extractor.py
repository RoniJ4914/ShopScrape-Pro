from __future__ import annotations

from typing import List, Any, Dict, Optional

import httpx

from app.models.product import Product, Variant
from .base import BaseExtractor

# Generic query shape that works against most storefront-style GraphQL APIs
# that expose a Relay-style `products(first, after)` connection. Endpoints
# that use a different schema will simply fail this query and the pipeline
# falls back to the next method in priority order (see platform_detection).
_GENERIC_PRODUCTS_QUERY = """
query Products($first: Int!, $after: String) {
  products(first: $first, after: $after) {
    edges {
      cursor
      node {
        id
        title
        handle
        vendor
        productType
        description
        tags
        images(first: 10) { edges { node { url } } }
        variants(first: 50) {
          edges {
            node {
              id
              title
              sku
              availableForSale
              price { amount currencyCode }
              compareAtPrice { amount currencyCode }
            }
          }
        }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""


def _to_float(v: Any) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


class GraphQLExtractor(BaseExtractor):
    method_name = "graphql"

    def __init__(self, store_id: str, base_url: str, endpoint: str,
                 client: Optional[httpx.AsyncClient] = None,
                 headers: Optional[Dict[str, str]] = None,
                 page_size: int = 50, max_pages: int = 1000):
        super().__init__(store_id, base_url)
        self.endpoint = endpoint
        self._client = client
        self.headers = headers or {}
        self.page_size = page_size
        self.max_pages = max_pages

    async def extract(self, query: Optional[str] = None) -> List[Product]:
        client_owned = self._client is None
        client = self._client or httpx.AsyncClient(timeout=20.0)
        query = query or _GENERIC_PRODUCTS_QUERY
        try:
            products = await self._paginate(client, query)
            return self._tag_provenance(products)
        finally:
            if client_owned:
                await client.aclose()

    async def _paginate(self, client: httpx.AsyncClient, query: str) -> List[Product]:
        products: List[Product] = []
        after = None
        page = 0

        while page < self.max_pages:
            variables = {"first": self.page_size, "after": after}
            try:
                resp = await client.post(
                    self.endpoint,
                    json={"query": query, "variables": variables},
                    headers=self.headers,
                )
            except httpx.HTTPError:
                break
            if resp.status_code != 200:
                break
            try:
                payload = resp.json()
            except ValueError:
                break

            data = (payload or {}).get("data", {})
            connection = data.get("products")
            if not connection:
                break

            edges = connection.get("edges", [])
            for edge in edges:
                node = edge.get("node")
                if node:
                    products.append(self._normalize(node))

            page_info = connection.get("pageInfo", {})
            if not page_info.get("hasNextPage") or not edges:
                break
            after = page_info.get("endCursor")
            page += 1

        return products

    def _normalize(self, node: Dict[str, Any]) -> Product:
        variants_edges = ((node.get("variants") or {}).get("edges")) or []
        variants: List[Variant] = []
        for ve in variants_edges:
            vn = ve.get("node", {})
            price_obj = vn.get("price") or {}
            compare_obj = vn.get("compareAtPrice") or {}
            variants.append(Variant(
                id=str(vn.get("id", len(variants))),
                title=vn.get("title"),
                sku=vn.get("sku"),
                price=_to_float(price_obj.get("amount")),
                currency=price_obj.get("currencyCode"),
                compare_at_price=_to_float(compare_obj.get("amount")),
                available=vn.get("availableForSale"),
            ))
        if not variants:
            variants = [Variant(id="default")]

        image_edges = ((node.get("images") or {}).get("edges")) or []
        images = [e.get("node", {}).get("url") for e in image_edges if e.get("node", {}).get("url")]

        return Product(
            id=Product.stable_id(self.store_id, str(node.get("id")) if node.get("id") else None,
                                  node.get("handle"), None),
            store_id=self.store_id,
            handle=node.get("handle"),
            title=node.get("title", ""),
            vendor=node.get("vendor"),
            product_type=node.get("productType"),
            description=node.get("description"),
            tags=node.get("tags") or [],
            images=images,
            variants=variants,
        )
