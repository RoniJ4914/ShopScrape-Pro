from __future__ import annotations

from typing import List, Optional, Any

from bs4 import BeautifulSoup

from app.models.product import Product, Variant
from .base import BaseExtractor


def _to_float(v: Any) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


class MicrodataExtractor(BaseExtractor):
    method_name = "microdata"

    async def extract(self, html: str, page_url: Optional[str] = None) -> List[Product]:
        soup = BeautifulSoup(html, "html.parser")
        products: List[Product] = []

        for node in soup.select('[itemtype*="schema.org/Product"]'):
            title_el = node.select_one('[itemprop="name"]')
            title = title_el.get_text(strip=True) if title_el else None
            if not title:
                continue

            sku_el = node.select_one('[itemprop="sku"]')
            sku = sku_el.get_text(strip=True) if sku_el else None

            brand_el = node.select_one('[itemprop="brand"]')
            vendor = None
            if brand_el:
                vendor = brand_el.get("content") or brand_el.get_text(strip=True)

            desc_el = node.select_one('[itemprop="description"]')
            description = desc_el.get_text(strip=True) if desc_el else None

            images = []
            for img_el in node.select('[itemprop="image"]'):
                src = img_el.get("content") or img_el.get("src")
                if src:
                    images.append(src)

            variants = []
            for offer_el in node.select('[itemprop="offers"]'):
                price_el = offer_el.select_one('[itemprop="price"]')
                price = None
                if price_el:
                    price = price_el.get("content") or price_el.get_text(strip=True)
                currency_el = offer_el.select_one('[itemprop="priceCurrency"]')
                currency = currency_el.get("content") if currency_el else None
                avail_el = offer_el.select_one('[itemprop="availability"]')
                available = None
                if avail_el:
                    avail_text = (avail_el.get("href") or avail_el.get("content") or avail_el.get_text()).lower()
                    available = "instock" in avail_text

                variants.append(Variant(
                    id=sku or "default",
                    sku=sku,
                    price=_to_float(price),
                    currency=currency,
                    available=available,
                ))

            if not variants:
                variants = [Variant(id=sku or "default", sku=sku)]

            products.append(Product(
                id=Product.stable_id(self.store_id, sku, None, page_url),
                store_id=self.store_id,
                title=title,
                vendor=vendor,
                description=description,
                images=images,
                url=page_url,
                variants=variants,
            ))

        return self._tag_provenance(products)
