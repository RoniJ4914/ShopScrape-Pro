from __future__ import annotations

import logging
from typing import Dict, Any

import httpx

from .base import BaseSender

logger = logging.getLogger(__name__)


class DiscordSender(BaseSender):
    channel = "discord"

    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client

    async def send(self, destination: str, payload: Dict[str, Any]) -> bool:
        client_owned = self._client is None
        client = self._client or httpx.AsyncClient(timeout=10.0)
        try:
            resp = await client.post(destination, json=payload)
            if resp.status_code >= 300:
                logger.warning("Discord webhook returned %s: %s", resp.status_code, resp.text[:200])
                return False
            return True
        except httpx.HTTPError as exc:
            logger.warning("Discord webhook send failed: %s", exc)
            return False
        finally:
            if client_owned:
                await client.aclose()
