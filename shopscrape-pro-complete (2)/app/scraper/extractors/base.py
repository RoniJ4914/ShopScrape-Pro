"""
NOT part of the original upload -- every extractor
(`embedded_json_extractor.py`, `graphql_extractor.py`, `html_extractor.py`,
`jsonld_extractor.py`, `microdata_extractor.py`, `rest_extractor.py`) does
`from .base import BaseExtractor` and relies on three things this class
provides: `self.store_id` / `self.base_url` set in `__init__`, a
`method_name` class attribute each subclass overrides, and
`self._tag_provenance(products)` to stamp `source_platform`/`source_method`
on every returned `Product` before handing the list back to the pipeline.
This is a minimal reconstruction of that shared contract, inferred from how
every extractor already uses it. If your real repo already has this file,
use yours instead -- this one is inferred, not original.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from app.models.product import Product


class BaseExtractor(ABC):
    # Overridden per subclass -- "graphql" | "rest" | "embedded_json" |
    # "jsonld" | "microdata" | "html". Stamped onto every Product this
    # extractor returns, via `_tag_provenance`.
    method_name: str = ""

    def __init__(self, store_id: str, base_url: str, platform: Optional[str] = None):
        self.store_id = store_id
        self.base_url = base_url
        # Best-effort platform label (e.g. "shopify") from platform_detection --
        # informational only, never branched on inside an extractor.
        self.platform = platform

    @abstractmethod
    async def extract(self, *args, **kwargs) -> List[Product]:
        """
        Return a list of normalized `Product`s. Signature varies by
        subclass (some take `html`/`page_url`, REST/GraphQL fetch their
        own data and take an optional endpoint/query hint instead) --
        `extract()` is the one contract every extractor shares, not a
        fixed argument list.
        """
        raise NotImplementedError

    def _tag_provenance(self, products: List[Product]) -> List[Product]:
        for product in products:
            product.source_platform = self.platform
            product.source_method = self.method_name
        return products
