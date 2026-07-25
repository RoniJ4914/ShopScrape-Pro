"""
Platform detection.

Given raw HTML (and optionally captured network requests), figure out:
  1. which ecommerce platform this store is running (best-effort, informational)
  2. which extraction METHOD to use, following the priority order from spec:
       graphql > rest > embedded_json > jsonld > microdata > html

This module never assumes Shopify. Every signature is one of several,
checked in parallel, and platform identity is separate from extraction
method -- a WooCommerce store might still be best scraped via JSON-LD,
a "generic React store" might expose a GraphQL endpoint, etc.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional, List, Dict, Any


class Platform(str, Enum):
    SHOPIFY = "shopify"
    WOOCOMMERCE = "woocommerce"
    MAGENTO = "magento"
    BIGCOMMERCE = "bigcommerce"
    WIX = "wix"
    SQUARESPACE = "squarespace"
    SALESFORCE_CC = "salesforce_commerce_cloud"
    HEADLESS = "headless"
    GENERIC_REACT = "generic_react"
    GENERIC_NEXTJS = "generic_nextjs"
    GENERIC_NUXT = "generic_nuxt"
    GENERIC_HTML = "generic_html"
    UNKNOWN = "unknown"


class ExtractionMethod(str, Enum):
    GRAPHQL = "graphql"
    REST = "rest"
    EMBEDDED_JSON = "embedded_json"
    JSONLD = "jsonld"
    MICRODATA = "microdata"
    HTML = "html"


# Ordered priority -- first viable method wins.
METHOD_PRIORITY: List[ExtractionMethod] = [
    ExtractionMethod.GRAPHQL,
    ExtractionMethod.REST,
    ExtractionMethod.EMBEDDED_JSON,
    ExtractionMethod.JSONLD,
    ExtractionMethod.MICRODATA,
    ExtractionMethod.HTML,
]


@dataclass
class DetectionResult:
    platform: Platform
    platform_confidence: float  # 0..1, informational only
    available_methods: List[ExtractionMethod]
    chosen_method: ExtractionMethod
    signals: Dict[str, Any]  # debug info: what matched


# --- Platform signatures -----------------------------------------------
# Each entry: (Platform, [regex signals to check against html/headers])
_PLATFORM_SIGNATURES = [
    (Platform.SHOPIFY, [
        r"cdn\.shopify\.com",
        r"Shopify\.theme",
        r"window\.Shopify\s*=",
        r"/cdn/shop/",
    ]),
    (Platform.WOOCOMMERCE, [
        r"woocommerce",
        r"wp-content/plugins/woocommerce",
        r"wc-ajax",
    ]),
    (Platform.MAGENTO, [
        r"Magento_",
        r"/static/version\d+/frontend/",
        r"mage/cookies",
    ]),
    (Platform.BIGCOMMERCE, [
        r"cdn\d*\.bigcommerce\.com",
        r"bigcommerce\.com/s-",
        r"stencil-utils",
    ]),
    (Platform.WIX, [
        r"static\.wixstatic\.com",
        r"wix-warmup-data",
        r"X-Wix-",
    ]),
    (Platform.SQUARESPACE, [
        r"squarespace\.com/universal",
        r"Static\.SQUARESPACE_CONTEXT",
        r"squarespace-cdn\.com",
    ]),
    (Platform.SALESFORCE_CC, [
        r"demandware\.static",
        r"/on/demandware\.store/",
        r"dwsid=",
    ]),
]

_NEXTJS_SIGNAL = re.compile(r"__NEXT_DATA__|/_next/static/")
_NUXT_SIGNAL = re.compile(r"__NUXT__|/_nuxt/")
_REACT_SIGNAL = re.compile(r"id=\"root\"|data-reactroot|react-dom")
_APOLLO_SIGNAL = re.compile(r"__APOLLO_STATE__")
_GRAPHQL_ENDPOINT_HINT = re.compile(r"/graphql|/api/graphql|graphql\.json")

_REST_CANDIDATE_PATHS = [
    "/products.json",
    "/api/products",
    "/api/catalog",
    "/collections/all/products.json",
    "/search.json",
    "/catalog",
]

_EMBEDDED_JSON_MARKERS = [
    "__NEXT_DATA__",
    "__NUXT__",
    "window.__INITIAL_STATE__",
    "window.__APOLLO_STATE__",
    "window.__PRELOADED_STATE__",
]

# Platforms where a REST product-listing endpoint is a guaranteed
# platform-level convention, not something a theme happens to reference.
# Shopify's `/products.json` works on every storefront regardless of
# whether anything in the rendered HTML/JS ever calls it -- most themes
# never do, since it's served by the platform, not the theme. Relying
# solely on the HTML/network signal below under-detects REST for
# essentially every standard Shopify store.
_PLATFORM_GUARANTEED_METHODS: Dict[Platform, List[ExtractionMethod]] = {
    Platform.SHOPIFY: [ExtractionMethod.REST],
}


def detect_platform(html: str, headers: Optional[Dict[str, str]] = None) -> (Platform, float, Dict[str, Any]):
    headers = headers or {}
    header_blob = " ".join(f"{k}: {v}" for k, v in headers.items())
    haystack = html + "\n" + header_blob

    best_platform = Platform.UNKNOWN
    best_score = 0.0
    signals: Dict[str, Any] = {}

    for platform, patterns in _PLATFORM_SIGNATURES:
        matches = [p for p in patterns if re.search(p, haystack, re.IGNORECASE)]
        if matches:
            score = len(matches) / len(patterns)
            signals[platform.value] = matches
            if score > best_score:
                best_score = score
                best_platform = platform

    if best_platform is Platform.UNKNOWN:
        if _NEXTJS_SIGNAL.search(haystack):
            best_platform, best_score = Platform.GENERIC_NEXTJS, 0.5
            signals["generic_nextjs"] = ["__NEXT_DATA__ or /_next/static/"]
        elif _NUXT_SIGNAL.search(haystack):
            best_platform, best_score = Platform.GENERIC_NUXT, 0.5
            signals["generic_nuxt"] = ["__NUXT__ or /_nuxt/"]
        elif _REACT_SIGNAL.search(haystack):
            best_platform, best_score = Platform.GENERIC_REACT, 0.3
            signals["generic_react"] = ["react root markers"]
        else:
            best_platform, best_score = Platform.GENERIC_HTML, 0.2

    return best_platform, best_score, signals


def detect_available_methods(
    html: str,
    network_requests: Optional[List[Dict[str, Any]]] = None,
    platform: Optional[Platform] = None,
) -> List[ExtractionMethod]:
    """
    Inspect page HTML + (optionally) captured network traffic to figure out
    which extraction methods are actually viable for this store, in priority
    order. `network_requests` is a list of {"url":..., "method":..., "type":...}
    dicts, typically produced by the browser rendering engine's network capture.

    `platform`, if given (from `detect_platform`), adds any methods that
    platform guarantees regardless of what this specific page's HTML shows
    -- see `_PLATFORM_GUARANTEED_METHODS`.
    """
    available: List[ExtractionMethod] = []
    network_requests = network_requests or []

    # 1. GraphQL -- either an explicit endpoint was hit, or one is hinted at in HTML
    graphql_hit = any(
        _GRAPHQL_ENDPOINT_HINT.search(req.get("url", "")) for req in network_requests
    ) or bool(_GRAPHQL_ENDPOINT_HINT.search(html)) or bool(_APOLLO_SIGNAL.search(html))
    if graphql_hit:
        available.append(ExtractionMethod.GRAPHQL)

    # 2. REST -- a network request matched a known product-API shape, or a
    #    known REST path pattern appears referenced in HTML/JS
    rest_hit = any(
        any(path in req.get("url", "") for path in _REST_CANDIDATE_PATHS)
        for req in network_requests
    ) or any(path in html for path in _REST_CANDIDATE_PATHS)
    if rest_hit:
        available.append(ExtractionMethod.REST)

    # 3. Embedded JSON blobs
    if any(marker in html for marker in _EMBEDDED_JSON_MARKERS):
        available.append(ExtractionMethod.EMBEDDED_JSON)

    # 4. JSON-LD
    if "application/ld+json" in html:
        available.append(ExtractionMethod.JSONLD)

    # 5. Microdata
    if re.search(r'itemtype=["\']https?://schema\.org/Product', html):
        available.append(ExtractionMethod.MICRODATA)

    # 5b. Platform-guaranteed methods (e.g. Shopify's /products.json) --
    # added even if nothing on *this* page hinted at them.
    if platform is not None:
        for method in _PLATFORM_GUARANTEED_METHODS.get(platform, []):
            if method not in available:
                available.append(method)

    # 6. HTML always works as the final fallback
    available.append(ExtractionMethod.HTML)

    return available


def choose_method(available: List[ExtractionMethod]) -> ExtractionMethod:
    for method in METHOD_PRIORITY:
        if method in available:
            return method
    return ExtractionMethod.HTML


async def detect(
    html: str,
    headers: Optional[Dict[str, str]] = None,
    network_requests: Optional[List[Dict[str, Any]]] = None,
) -> DetectionResult:
    platform, confidence, signals = detect_platform(html, headers)
    available = detect_available_methods(html, network_requests, platform=platform)
    chosen = choose_method(available)
    return DetectionResult(
        platform=platform,
        platform_confidence=confidence,
        available_methods=available,
        chosen_method=chosen,
        signals=signals,
    )
