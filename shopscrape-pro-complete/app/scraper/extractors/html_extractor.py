from __future__ import annotations

import re
from typing import List, Optional

from bs4 import BeautifulSoup

from app.models.product import Product, Variant
from .base import BaseExtractor

_PRICE_RE = re.compile(r"[\$£€]\s?(\d+[.,]?\d*)")

# Common product-card container selectors across popular themes/builders.
# This is intentionally broad and best-effort -- it is the LAST resort in
# the detection order, used only when no structured data source exists.
_CARD_SELECTORS = [
    ".product-card", ".product-item", ".product", ".product-grid-item",
    "[data-product-id]", "[data-product]", ".grid-product", ".product-tile",
]

_TITLE_SELECTORS = [".product-title", ".product-name", "h2", "h3", ".title"]
_PRICE_SELECTORS = [".price", ".product-price", ".money", "[data-price]"]
_AVAILABILITY_SELECTORS = [".availability", ".stock-status", ".in-stock", ".sold-out", ".out-of-stock"]
_IMAGE_SELECTORS = ["img"]


def _first_text(node, selectors: List[str]) -> Optional[str]:
    for sel in selectors:
        el = node.select_one(sel)
        if el:
            text = el.get_text(strip=True)
            if text:
                return text
    return None


def _parse_price(text: Optional[str]) -> Optional[float]:
    if not text:
        return None
    match = _PRICE_RE.search(text)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


class HtmlExtractor(BaseExtractor):
    """
    Last-resort extractor: parse rendered HTML using common ecommerce
    patterns. Lower confidence/precision than structured sources by design
    -- the pipeline only reaches here when nothing else was detected.
    """

    method_name = "html"

    async def extract(self, html: str, page_url: Optional[str] = None) -> List[Product]:
        soup = BeautifulSoup(html, "html.parser")
        products: List[Product] = []

        cards = []
        for sel in _CARD_SELECTORS:
            found = soup.select(sel)
            if found:
                cards = found
                break

        for idx, card in enumerate(cards):
            title = _first_text(card, _TITLE_SELECTORS)
            if not title:
                continue

            price_text = _first_text(card, _PRICE_SELECTORS)
            price = _parse_price(price_text)

            availability_text = _first_text(card, _AVAILABILITY_SELECTORS)
            available = None
            if availability_text:
                lowered = availability_text.lower()
                if "sold out" in lowered or "out of stock" in lowered:
                    available = False
                elif "in stock" in lowered or "available" in lowered:
                    available = True

            link_el = card.select_one("a[href]")
            url = link_el["href"] if link_el else None

            img_el = card.select_one("img")
            image = None
            if img_el:
                image = img_el.get("src") or img_el.get("data-src")

            native_id = card.get("data-product-id") or card.get("data-product")

            products.append(Product(
                id=Product.stable_id(self.store_id, native_id, None, url or f"{page_url}#{idx}"),
                store_id=self.store_id,
                title=title,
                url=url,
                images=[image] if image else [],
                variants=[Variant(
                    id=native_id or "default",
                    price=price,
                    available=available,
                )],
            ))

        return self._tag_provenance(products)
