"""
Browser rendering engine.

Responsibilities:
  1. Fetch raw HTML via plain HTTP first (cheap, fast).
  2. Decide whether JS rendering is actually needed.
  3. If needed, render with Playwright (Chromium) and capture every network
     request the page makes -- this is how we discover GraphQL/REST APIs
     that the static HTML never reveals.

This module has automatic fallback: if Playwright/Chromium isn't
installed or fails for any reason, we degrade gracefully to the raw HTML
we already fetched rather than hard-failing the whole scrape.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

import httpx

logger = logging.getLogger(__name__)

# Signals that strongly suggest the page needs JS execution to show real
# product content (SPA shells, hydration markers, empty-looking body).
_JS_REQUIRED_SIGNALS = [
    re.compile(r'<div id="root">\s*</div>'),
    re.compile(r'<div id="__next">\s*</div>'),
    re.compile(r"__NEXT_DATA__"),
    re.compile(r"__NUXT__"),
    re.compile(r"data-server-rendered=\"false\""),
]


@dataclass
class FetchResult:
    html: str
    status_code: int
    headers: Dict[str, str] = field(default_factory=dict)
    network_requests: List[Dict[str, Any]] = field(default_factory=list)
    rendered: bool = False  # True if Playwright was used


def needs_js_rendering(html: str) -> bool:
    if len(html.strip()) < 500:
        return True
    return any(pattern.search(html) for pattern in _JS_REQUIRED_SIGNALS)


async def fetch_static(url: str, client: Optional[httpx.AsyncClient] = None) -> FetchResult:
    owned = client is None
    client = client or httpx.AsyncClient(timeout=20.0, follow_redirects=True,
                                          headers={"User-Agent": "ShopScrapePro/1.0 (+https://shopscrapepro.com/bot)"})
    try:
        resp = await client.get(url)
        return FetchResult(
            html=resp.text,
            status_code=resp.status_code,
            headers=dict(resp.headers),
        )
    finally:
        if owned:
            await client.aclose()


_RELEVANT_RESOURCE_TYPES = {"xhr", "fetch", "document"}
_JSON_LIKE_RE = re.compile(r"\.json($|\?)|/graphql|/api/")


async def fetch_rendered(url: str, timeout_ms: int = 20000) -> FetchResult:
    """
    Render the page with Playwright and capture network traffic. Falls back
    to static fetch if Playwright is unavailable or errors out -- rendering
    failures should never take down the whole pipeline.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.warning("Playwright not installed; falling back to static fetch for %s", url)
        return await fetch_static(url)

    network_requests: List[Dict[str, Any]] = []

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                context = await browser.new_context(
                    user_agent="ShopScrapePro/1.0 (+https://shopscrapepro.com/bot)"
                )
                page = await context.new_page()

                def on_request(request):
                    if request.resource_type in _RELEVANT_RESOURCE_TYPES or _JSON_LIKE_RE.search(request.url):
                        network_requests.append({
                            "url": request.url,
                            "method": request.method,
                            "type": request.resource_type,
                        })

                page.on("request", on_request)

                response = await page.goto(url, timeout=timeout_ms, wait_until="networkidle")
                html = await page.content()
                status = response.status if response else 200

                return FetchResult(
                    html=html,
                    status_code=status,
                    network_requests=network_requests,
                    rendered=True,
                )
            finally:
                await browser.close()
    except Exception as exc:  # noqa: BLE001 -- deliberately broad: rendering must degrade, not crash the pipeline
        logger.warning("Playwright rendering failed for %s (%s); falling back to static fetch", url, exc)
        return await fetch_static(url)


async def fetch(url: str, force_render: bool = False) -> FetchResult:
    """
    Main entry point: fetch static HTML first, escalate to full rendering
    only if needed (or explicitly forced). This keeps the common case cheap
    while still supporting JS-heavy storefronts automatically.
    """
    static_result = await fetch_static(url)

    if not force_render and not needs_js_rendering(static_result.html):
        return static_result

    return await fetch_rendered(url)
