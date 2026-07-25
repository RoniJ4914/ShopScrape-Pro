"""
Universal Scraper pipeline.

    fetch (static, escalate to Playwright if needed)
            |
            v
    detect_platform + detect_available_methods  (app/scraper/platform_detection.py)
            |
            v
    choose_method                     --> GraphQL > REST > Embedded JSON >
            |                                JSON-LD > Microdata > HTML
            v
    extractor.extract()  -- if this raises, walk the *other* detected
            |                methods in the same priority order before
            |                giving up (defensive fallback chain, not a
            |                single shot)
            v
    List[Product], method actually used

`scrape_store(store_id, url)` is the single public entry point -- the one
this module's docstring (and the README) has always promised. Nothing
outside this file should import an individual extractor directly.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

from app.models.product import Product
from app.scraper.browser.renderer import FetchResult, fetch
from app.scraper.extractors.base import BaseExtractor
from app.scraper.extractors.embedded_json_extractor import EmbeddedJsonExtractor
from app.scraper.extractors.graphql_extractor import GraphQLExtractor
from app.scraper.extractors.html_extractor import HtmlExtractor
from app.scraper.extractors.jsonld_extractor import JsonLdExtractor
from app.scraper.extractors.microdata_extractor import MicrodataExtractor
from app.scraper.extractors.rest_extractor import RestExtractor
from app.scraper.platform_detection import (
    ExtractionMethod,
    METHOD_PRIORITY,
    choose_method,
    detect_available_methods,
    detect_platform,
)

logger = logging.getLogger(__name__)

# Only used to narrow down *which* GraphQL path to hit once detection has
# already decided GraphQL is available -- see `_discover_graphql_endpoint`.
_GRAPHQL_URL_HINT = re.compile(r"/graphql|/api/graphql|graphql\.json", re.IGNORECASE)
_GRAPHQL_HTML_HINT = re.compile(r"""["'](/[^"']*graphql[^"']*)["']""", re.IGNORECASE)


def _discover_graphql_endpoint(base_url: str, html: str, network_requests: List[Dict[str, Any]]) -> str:
    """
    Best-effort endpoint resolution: prefer a GraphQL URL actually observed
    in captured network traffic (most reliable -- that's a real request the
    page made), then one referenced literally in the page's HTML/JS, then
    fall back to the common `/graphql` convention. We only get here because
    `detect_available_methods` already found a GraphQL signal, so this is
    about *where*, not *whether*.
    """
    for req in network_requests:
        req_url = req.get("url", "")
        if _GRAPHQL_URL_HINT.search(req_url):
            return req_url
    match = _GRAPHQL_HTML_HINT.search(html)
    if match:
        return urljoin(base_url, match.group(1))
    return urljoin(base_url, "/graphql")


def _build_extractor(
    method: ExtractionMethod,
    store_id: str,
    base_url: str,
    html: str,
    network_requests: List[Dict[str, Any]],
    platform_value: Optional[str],
) -> BaseExtractor:
    if method is ExtractionMethod.GRAPHQL:
        endpoint = _discover_graphql_endpoint(base_url, html, network_requests)
        extractor: BaseExtractor = GraphQLExtractor(store_id, base_url, endpoint)
    elif method is ExtractionMethod.REST:
        extractor = RestExtractor(store_id, base_url)
    elif method is ExtractionMethod.EMBEDDED_JSON:
        extractor = EmbeddedJsonExtractor(store_id, base_url)
    elif method is ExtractionMethod.JSONLD:
        extractor = JsonLdExtractor(store_id, base_url)
    elif method is ExtractionMethod.MICRODATA:
        extractor = MicrodataExtractor(store_id, base_url)
    else:  # ExtractionMethod.HTML -- universal last resort
        extractor = HtmlExtractor(store_id, base_url)

    # GraphQLExtractor/RestExtractor don't forward `platform` through their
    # overridden __init__ (they add their own required/optional args
    # instead) -- set it directly here so `_tag_provenance` still stamps it
    # on every returned Product regardless of which extractor ran.
    extractor.platform = platform_value
    return extractor


async def _run_method(
    method: ExtractionMethod,
    store_id: str,
    base_url: str,
    page_url: str,
    html: str,
    network_requests: List[Dict[str, Any]],
    platform_value: Optional[str],
) -> List[Product]:
    extractor = _build_extractor(method, store_id, base_url, html, network_requests, platform_value)

    # REST/GraphQL fetch their own data over the network and don't take the
    # already-fetched HTML; the rest parse the page we already have.
    if method in (ExtractionMethod.GRAPHQL, ExtractionMethod.REST):
        return await extractor.extract()
    return await extractor.extract(html, page_url=page_url)


async def scrape_store(store_id: str, url: str) -> Tuple[List[Product], str]:
    """
    Scrape one store end to end: fetch -> detect -> extract.

    If the chosen method raises at runtime, walk the *other* methods
    `detect_available_methods` found, in spec priority order (HTML is
    always available, so this never runs out of options) -- only raises
    if every single detected method fails. Returns `(products, method)`,
    where `method` is whichever one actually produced the result (not
    necessarily the first choice).
    """
    fetch_result: FetchResult = await fetch(url)
    platform, _confidence, _signals = detect_platform(fetch_result.html, fetch_result.headers)
    available = detect_available_methods(fetch_result.html, fetch_result.network_requests)
    chosen = choose_method(available)

    ordered_methods = [chosen] + [m for m in METHOD_PRIORITY if m in available and m != chosen]

    last_error: Optional[Exception] = None
    for method in ordered_methods:
        try:
            products = await _run_method(
                method, store_id, url, url,
                fetch_result.html, fetch_result.network_requests, platform.value,
            )
            return products, method.value
        except Exception as exc:  # a bad method must fall through, not crash the scrape
            logger.warning(
                "Extraction method '%s' failed for store %s (%s); trying next detected method",
                method.value, store_id, exc,
            )
            last_error = exc
            continue

    raise RuntimeError(
        f"All extraction methods {[m.value for m in ordered_methods]} failed for "
        f"store {store_id} ({url}); last error: {last_error}"
    ) from last_error
