"""
robots.txt compliance.

Every URL this scraper actually requests routes through `can_fetch()`
before the request goes out: the initial page fetch in
`app/scraper/pipeline.py`, each candidate path `RestExtractor` probes,
and the endpoint `GraphQLExtractor` posts to. This is the only place
that fetches or parses a robots.txt file -- nothing else should.

Fails *closed*: if a store's robots.txt can't be retrieved or parsed for
some reason (network error, 5xx, malformed response), we treat that
domain as fully disallowed until the next check, rather than assuming
we're free to scrape just because we couldn't confirm otherwise.
robots.txt is how a site opts out of crawling; we don't get to treat an
inability to check it as an opt-in.

A 404 for robots.txt itself *is* treated as "no restrictions" -- that's
the standard convention (RFC 9309): a missing robots.txt means the site
never published any crawling rules, not that it disallows everything.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Dict, Optional
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

logger = logging.getLogger(__name__)

# Must match the product token in the User-Agent header the scraper
# actually sends (see app/scraper/browser/renderer.py) -- robots.txt
# rules are matched against this, falling back to "*" if a site doesn't
# have a group specifically for us.
USER_AGENT = "ShopScrapePro"

DEFAULT_CRAWL_DELAY = 0.0
_CACHE_TTL_SECONDS = 3600  # re-fetch robots.txt at most once an hour per domain


class RobotsDisallowed(Exception):
    """Raised when robots.txt disallows fetching a URL we were asked to scrape."""


@dataclass
class _CachedRobots:
    # None means "couldn't confirm what's allowed" -> treated as disallow-all,
    # distinct from a successfully parsed empty ruleset (allow-all).
    parser: Optional[RobotFileParser]
    fetched_at: float
    crawl_delay: Optional[float] = None


_cache: Dict[str, _CachedRobots] = {}


def _domain_root(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


async def _fetch_robots_txt(domain_root: str, client: httpx.AsyncClient) -> _CachedRobots:
    robots_url = urljoin(domain_root + "/", "robots.txt")
    try:
        resp = await client.get(robots_url, timeout=10.0)
    except httpx.HTTPError as exc:
        logger.warning(
            "robots.txt unreachable for %s (%s); treating as fully disallowed until next check",
            domain_root, exc,
        )
        return _CachedRobots(parser=None, fetched_at=time.monotonic())

    if resp.status_code == 404:
        parser = RobotFileParser()
        parser.parse([])  # no rules published -> allow everything
        return _CachedRobots(parser=parser, fetched_at=time.monotonic())

    if resp.status_code >= 400:
        # 401/403/5xx -- can't confirm what's allowed. Fail safe rather
        # than assume permission.
        logger.warning(
            "robots.txt returned HTTP %s for %s; treating as fully disallowed until next check",
            resp.status_code, domain_root,
        )
        return _CachedRobots(parser=None, fetched_at=time.monotonic())

    parser = RobotFileParser()
    parser.parse(resp.text.splitlines())
    delay = parser.crawl_delay(USER_AGENT) or parser.crawl_delay("*")
    return _CachedRobots(
        parser=parser,
        fetched_at=time.monotonic(),
        crawl_delay=float(delay) if delay else None,
    )


async def _get_cached(domain_root: str, client: httpx.AsyncClient) -> _CachedRobots:
    cached = _cache.get(domain_root)
    if cached and (time.monotonic() - cached.fetched_at) < _CACHE_TTL_SECONDS:
        return cached
    fresh = await _fetch_robots_txt(domain_root, client)
    _cache[domain_root] = fresh
    return fresh


async def can_fetch(url: str, client: Optional[httpx.AsyncClient] = None) -> bool:
    """True if this URL's domain's robots.txt permits fetching it for our user agent."""
    owned_client = client is None
    client = client or httpx.AsyncClient(follow_redirects=True)
    try:
        cached = await _get_cached(_domain_root(url), client)
    finally:
        if owned_client:
            await client.aclose()

    if cached.parser is None:
        return False
    return cached.parser.can_fetch(USER_AGENT, url)


async def crawl_delay_for(url: str, client: Optional[httpx.AsyncClient] = None) -> float:
    """Seconds to wait between requests to this domain, per its robots.txt (0.0 if unspecified)."""
    owned_client = client is None
    client = client or httpx.AsyncClient(follow_redirects=True)
    try:
        cached = await _get_cached(_domain_root(url), client)
    finally:
        if owned_client:
            await client.aclose()
    return cached.crawl_delay or DEFAULT_CRAWL_DELAY


async def assert_can_fetch(url: str, client: Optional[httpx.AsyncClient] = None) -> None:
    """Same check as `can_fetch`, raising `RobotsDisallowed` instead of returning False."""
    if not await can_fetch(url, client):
        raise RobotsDisallowed(f"robots.txt disallows fetching {url} for user agent '{USER_AGENT}'")
